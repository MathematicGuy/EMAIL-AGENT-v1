# Memory Evaluation — Specification (v1)

**Status:** Design approved, not yet implemented
**Area:** `evaluations/MEMORIES/`
**Branch:** `feat/agent-tool`
**Companion:** [Waku-Memory-and-Evaluation-Comparison.md](../../docs/references/Waku-Memory-and-Evaluation-Comparison.md)

---

## 1. Purpose

For each of our four memory scopes, answer four questions:

1. **Does it hold what was put in it?** (`recall`)
2. **Does a correction actually replace the old value?** (`update`)
3. **Does it refuse to invent when it was never told?** (`restraint`)
4. **Can it surface memory that belongs to someone else?** (`isolation`)

And — the part that makes this worth building rather than a pile of unit tests —
**attribute every result to exactly one scope**, so "memory helped" is never a
number you have to take on faith.

### 1.1 Why this exists

Our current memory evaluation
(`src/cowork_agent/features/ai_chat/evaluation.py`) has the right experimental
design and no real data: `DeterministicPairedScorer` is a hardcoded lookup
table whose own docstring calls it "a STAND-IN for real model scoring at MVP
tier", and its base scores were recalibrated so the enabled means clear the
0.6 bar. It proves the gate arithmetic works. It cannot fail on a regression,
because nothing real is being measured.

Meanwhile our genuine memory correctness lives in ~80 policy unit tests that
answer *"can memory leak or be written without authorization?"* — and nothing
answers *"does memory make the answer better, and which scope did it?"*

This harness answers the second question. It does not replace the first.

### 1.2 Non-goals

Named explicitly so nobody assumes coverage that does not exist:

- **It does not feed `evaluate_launch_gate`.** The existing paired report and
  its stand-in scorer are untouched by v1. Bridging them is a separate decision
  with its own justification burden (an outcome-to-score mapping has to be
  defensible, not merely convenient).
- **It does not measure token cost per probe.** Latency only in v1.
- **It does not test multi-hop reasoning.** Waku's `reasoning` probe type is
  deliberately omitted; see §4.2.
- **It does not score the retrieval *decision*.** Whether
  `retrieval_policy.select_memory_reads` was *right* to fire is a different
  experiment needing its own labeled set and an asymmetric cost model.
- **It does not gate CI on live results.** See §12.

---

## 2. The four scopes, in this harness's terms

This table is the translation layer. Every later section refers back to it.

| Scope | Where it lives | What it holds | How it gets there | Lifetime |
|---|---|---|---|---|
| `short_term` | `InMemoryChatSessionBuffer`, in-process | The newest 20 logical turns of *this* session | Automatically, on every turn | 30 min inactivity TTL, then gone |
| `long_term` | `DeclarativeProfile` in Postgres | `language`, `timezone`, `assistant_persona`, `response_tone` — the 4 fields in `PROFILE_PREFERENCE_FIELDS`, each capped at 200 chars | **Only** an explicit user config write carrying `EXPLICIT_USER_CONFIG` provenance | 90 days (`CHAT_PROFILE_RETENTION_SECONDS`) |
| `episodic` | `TaskEpisode` / `ChatSummaryEpisode` in Postgres | Tasks the user explicitly asked for | An explicit task request, then an approval transition | 90 days (`CHAT_EPISODE_RETENTION_SECONDS`) |
| `semantic` | Company RAG corpus | Company policy documents | Ingested out-of-band; read is flag- **and** cue-gated | Corpus lifetime |

Three consequences fall out of this table, and they shape the whole harness:

**No model can write to `long_term` or `episodic`.** Both require an explicit
authorization our policy modules enforce (`profile_policy.py`,
`episode_policy.py`). This is the deepest difference from waku-agent, where
`consolidation.py` lets a cheap model decide what becomes durable memory. It
means our seeding cannot be conversational for those two scopes — §6.

**A freshly written episode is not retrievable.** `authorize_task_episode_write`
requires `retrieval_eligible=false` on every new episode. It becomes eligible
only when `validation_status` transitions to `USER_APPROVED` or `COMPLETED`.
Seeding episodic memory is therefore a *three-step* ritual where the others are
one step, and that asymmetry is a fact about the product, not an inconvenience.

**`semantic` is off by default.** `CHAT_COMPANY_RAG_ENABLED` defaults to
`false`, and even when true, `retrieval_policy` still requires a cue phrase. A
semantic probe that does not contain a cue is testing nothing.

---

## 3. Vocabulary

Defined once, used precisely throughout. Ambiguity here is what makes
evaluation harnesses unreadable six months later.

| Term | Definition |
|---|---|
| **probe** | One question with a declared expected outcome and exactly one target scope. |
| **probe set** | One committed JSON file: a `seed` block plus a list of probes. |
| **target** | The single scope a probe is designed to exercise. Declared, never inferred. |
| **arm** | One configuration a probe is asked under. Every probe runs three: `full`, `<target>_off`, `control`. |
| **outcome** | The grade for one probe under one arm: `PASS`, `STALE`, `INVENTED`, or `MISS`. |
| **verdict** | The conclusion drawn from a probe's three outcomes together. This is the readable output. |
| **leak** | A probe the `control` arm passed. It was answerable without memory, so it measures the model, not the scope. |
| **certain** | Whether an outcome rests on the refusal phrase list. `certain=false` escalates to a judge. |

---

## 4. Probe set format

One JSON file per probe set, committed, containing **synthetic text only**.
Synthetic fixture text is permitted by `evaluations/HARNESS-GUIDE.md` §3; real
user content is not, in fixtures or reports.

```json
{
  "schema_version": "1.0.0",
  "probe_set_id": "v1-four-scopes",
  "label": "The four scopes, one probe each per test type",

  "seed": {
    "short_term": [
      "I'm working on the CCCD renewal request for the Da Nang office.",
      "The deadline for that is Tuesday.",
      "Correction: the deadline moved to Wednesday."
    ],
    "long_term": {
      "language": "vi",
      "timezone": "Asia/Ho_Chi_Minh",
      "assistant_persona": "operations coordinator",
      "response_tone": "concise"
    },
    "episodic": [
      { "request": "Create a task to renew the CCCD for the Da Nang office",
        "approve": true }
    ],
    "semantic": {
      "corpus_dir": "tests/fixtures/memory_eval/corpus"
    }
  },

  "probes": [
    {
      "id": "st_update_01",
      "targets": "short_term",
      "test": "update",
      "question": "When is the CCCD deadline?",
      "expect_any": ["Wednesday"],
      "stale_any": ["Tuesday"],
      "note": "Both days were stated in this session; only the later is true."
    },
    {
      "id": "lt_recall_01",
      "targets": "long_term",
      "test": "recall",
      "question": "What timezone should I assume for my scheduling?",
      "expect_any": ["Ho_Chi_Minh", "ICT", "UTC+7"]
    },
    {
      "id": "ep_restraint_01",
      "targets": "episodic",
      "test": "restraint",
      "question": "What is the case number on the CCCD renewal task?",
      "expect_refusal": true,
      "note": "The task exists; no case number was ever given. Knowing the task is what makes a system confident enough to fill the gap."
    },
    {
      "id": "sem_isolation_01",
      "targets": "semantic",
      "test": "isolation",
      "question": "What does our company policy say about overtime?",
      "expect_refusal": true,
      "foreign_seed": true,
      "note": "The overtime policy is seeded under a DIFFERENT tenant. Answering it is a cross-tenant leak, not a recall success."
    }
  ]
}
```

### 4.1 Probe fields

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Stable, opaque, `[a-z0-9_]{1,64}`. Appears in committed reports. |
| `targets` | yes | One of `short_term` / `long_term` / `episodic` / `semantic`. |
| `test` | yes | One of `recall` / `update` / `restraint` / `isolation`. |
| `question` | yes | The message sent to the controller. |
| `expect_any` | no | Substrings, any one of which means the right answer is present. |
| `expect_all` | no | Substrings, **all** of which must appear. For probes where naming one thing is half a thought. |
| `stale_any` | no | Substrings that indicate a **superseded** answer was asserted. Drives `STALE`. |
| `expect_refusal` | no | `true` when the only correct behaviour is to decline. Drives `INVENTED`. |
| `foreign_seed` | no | `true` seeds this probe's material under a different tenant/session. Only meaningful with `test: isolation`. |
| `note` | no | Prose for a human reading the probe file. Never scored. |

A probe must declare at least one of `expect_any`, `expect_all`, or
`expect_refusal`. The loader rejects a probe set where any probe declares none —
a probe with no expectation always passes, which is worse than no probe.

### 4.2 The four test types, and why `reasoning` was dropped

waku-agent ships `recall` / `update` / `restraint` / `reasoning`. We keep the
first three and replace the fourth with `isolation`.

| type | the failure it catches | why it earns a slot |
|---|---|---|
| `recall` | a scope that stored nothing | The floor. Everything else is meaningless if this fails. |
| `update` | a confidently superseded answer | Distinct from "forgot" — the user was told something false, not nothing. |
| `restraint` | invention | The failure that matters outside a demo. An assistant that knows the task is confident enough to invent the case number. |
| `isolation` | cross-tenant / cross-session leakage | Our system is multi-tenant; waku is one local user. This is the axis we have and they do not. |

`reasoning` (combining two stored facts into a conclusion) is a real capability
and is deferred to v2. At v1 it would mostly measure the model, and a probe
whose result is dominated by the model is exactly what §9's leak detection
exists to catch.

---

## 5. Arms

Every probe runs exactly three arms.

| Arm | Report key | What changes | What it tells you |
|---|---|---|---|
| `full` | `full` | Nothing. All four scopes seeded and readable. | The system as shipped. |
| `<target>_off` | `ablated` | The probe's target scope is masked out of the read. Everything else identical. | Whether the target scope was load-bearing. |
| `control` | `control` | **The seed is skipped.** All four scopes remain enabled, and empty. | Whether the probe needed memory at all. |

The arm is named `<target>_off` (`short_term_off`, `episodic_off`, …) because
which scope was masked matters when reading a run. It is stored under the fixed
key `ablated` in the report, so every verdict row has the same three columns
regardless of which scope the probe targeted; `targets` on the same row says
which scope `ablated` refers to.

### 5.1 `control` disables the seed, not the read

This is the single easiest thing to get backwards, and getting it backwards
silently breaks the harness.

If `control` disabled the *reads*, it would just be a fourth ablation arm, and a
probe the model can answer from its training data would score `PASS` under
`full` and look like a memory success. waku-agent hit exactly this: three of
seven probes in a hand-curated set were answerable with an empty store, "and
nothing on screen said so."

`control` therefore stands up the *same* system with the *same* reads enabled
against an *empty* store. A `PASS` there means one thing only: this probe does
not require memory.

### 5.2 The seam — how an arm is applied

`retrieval_policy.select_memory_reads` is called inside the controller at
[controller.py:892](../../src/cowork_agent/features/ai_chat/controller.py), so
there is no parameter to pass an arm through.

v1 introduces **no production change**. Instead the harness constructs the
controller with a subclass:

```python
class ArmScopedMemoryGateway(MemoryGateway):
    """A gateway that reports one scope as unavailable, for ablation arms.

    Masking the read (rather than the store) is the honest model of an arm:
    the question is "what does the reply look like when this scope cannot be
    read", which is exactly what a gateway expresses. Everything else —
    writes, project documents, episode transitions — is inherited unchanged.
    """

    def __init__(self, *args, masked_scope: MemoryType | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._masked_scope = masked_scope

    async def read_context(self, request):
        return await super().read_context(_mask(request, self._masked_scope))
```

`_mask` is a pure function on `MemoryContextRequest` — it sets
`short_term=False`, `long_term=False`, or swaps the episodic/semantic read for
its explicitly-disabled form. It is unit-testable with no adapters and no model.

**Rejected alternative:** adding a `memory_reads_override` parameter to
`ChatController.__init__`. It works, but it puts a test-only seam in production
code for something a subclass already expresses cleanly. Revisit only if the
subclass starts needing to override more than `read_context`.

---

## 6. Seeding — one ritual per scope

Each scope is seeded the way a real user would create that memory. A probe can
therefore only pass on memory that the product can actually produce.

This mirrors waku's rule — *"seeding is conversational... handing every backend
a pre-extracted fact list would skip the feature half of them exist to
provide"* — translated to our system. Their skipped step is extraction. **Ours
is authorization.** Writing rows directly into the repositories would score
retrieval as if authorization had happened when it had not, and would let
probes pass on episode states no real flow can reach.

```
short_term   for each line in seed.short_term:
                 controller.stream_message(line)     -> buffer.append_turn

long_term    gateway.write_profile(
                 DeclarativeProfile(**seed.long_term),
                 provenance=MemoryProvenance(EXPLICIT_USER_CONFIG))

episodic     for each entry in seed.episodic:
               1. controller.stream_message(entry.request)
                    -> is_explicit_task_request() must return True
                    -> TaskEpisode written SYSTEM_GENERATED, retrieval_eligible=false
               2. if entry.approve:
                    gateway.transition_task_episode(
                        from_status=SYSTEM_GENERATED,
                        to_status=USER_APPROVED)
                    -> retrieval_eligible becomes true
               3. assert the episode is now readable, or fail the run loudly

semantic     corpus at seed.semantic.corpus_dir is indexed once
             CHAT_COMPANY_RAG_ENABLED=true for the run
             probe questions MUST contain a cue phrase or the read never fires
```

### 6.1 Seeding failures are findings, not crashes

If `is_explicit_task_request` rejects a seed request, that is a **finding about
the phrasing policy**, reported as such — not a stack trace. The run records
`seed_failed` with the scope and the reason, marks every probe targeting that
scope as `unscorable`, and continues with the other scopes. A harness that dies
on the first seeding problem tells you nothing about the other three scopes.

### 6.2 Isolation seeding

A probe with `foreign_seed: true` has its material written under a **different
`tenant_id` and `user_id`** during setup. The probe is then asked as the primary
user. Any answer containing the foreign material is a cross-tenant leak — the
strongest possible failure this harness can report, and the one that should
block a release regardless of every other number.

---

## 7. The run algorithm

Deliberately linear and readable. Every step names what it prevents.

```
 1. Resolve identity
      run_key   = sha256(probe_set_id + model + serialized_seed)[:12]
      tenant_id = f"memeval-{run_key}"
      user_id   = f"memeval-{run_key}"
    WHY: a run can never collide with another run or touch a real user's
         memory. Direct analogue of waku's arena_home + arena_partition,
         which exists because their races were writing benchmark seeds into
         the operator's real mem0/Zep account under a shared default user.

 2. For each arm in {full, <target>_off, control}:

 3.   Seed (skip entirely when arm == control) — §6

 4.   Verify the seed landed (skip when arm == control — there is no seed)
        read_context with no mask, assert each seeded scope is non-empty
      WHY: a scope that silently failed to seed reports as amnesia. This is
           our version of waku's settle() — not a wait, but a check.
           Our writes are transactional, so no polling is needed; that is
           recorded here as a reason rather than silently omitted.

 5.   Start a new session — UNLESS the probe targets short_term
      WHY: the buffer feeds the last 8 turns into the prompt. Seed short_term
           with 3 turns, probe immediately, and the answer is sitting in the
           context window — the scope under test was never consulted. waku hit
           this exactly: three probes passed with the gate reporting "no
           lookup", meaning the contestant was never used.
           For a short_term probe the buffer IS the thing under test, so the
           session is deliberately kept.

 6.   Ask the probe question through ChatController.stream_message
        collect the full reply text
        record latency_ms, degraded_sources, and which scopes were read

 7.   outcome, certain, why = score(reply, probe)     -- §8, pure function

 8.   if not certain:
          declined = adjudicate_refusal(question, reply)   -- one judge call
          reconcile per §8.3

 9. Derive one verdict per probe from its three outcomes                -- §9
10. Detect leaks: probes the control arm passed                         -- §9.2
11. Emit the metadata-only report + the gitignored detail file          -- §10
12. Teardown: gateway.delete_all_for_user() for both the primary and any
    foreign tenant used by isolation probes
```

---

## 8. Scoring

### 8.1 A pure function

```python
def score(reply: str, probe: Probe) -> tuple[Outcome, bool, str]:
    """Grade one reply. Returns (outcome, certain, why)."""
```

No I/O, no model, no clock. The entire scoring layer is unit-testable offline
with no key and no database, and those tests are what gate CI (§12).

### 8.2 The four outcomes

Evaluated in this order. "Absent" means no substring from that list appears in
the reply.

| # | Condition | Outcome |
|---|---|---|
| 1 | `expect_refusal` and the reply declines | `PASS` *(certain=false)* |
| 2 | `expect_refusal` and it does not | `INVENTED` *(certain=false)* |
| 3 | `expect_any` declared but **absent**, and a `stale_any` **is** present | `STALE` |
| 4 | `expect_any` declared but **absent**, no `stale_any` present | `MISS` |
| 5 | `expect_all` declared and only partly present | `MISS`, naming the missing items |
| 6 | no `expect_any` declared, but a `stale_any` is present | `STALE` |
| 7 | otherwise | `PASS` |

**`STALE` fires only when the expected answer is missing.** A reply that gives
the right answer *and* mentions the superseded one — "Wednesday, it moved from
Tuesday" — is a good reply, not a stale one. Scoring it `STALE` would penalise
the most helpful phrasing available, so rule 3 requires the expected answer to
be absent before the stale check runs at all.

Why four instead of pass/fail, in the words that convinced me:

> A system that says "I don't know" is behaving correctly under uncertainty. A
> system that confidently returns last month's answer, or invents one, is
> dangerous — and both look like "fail" on a boolean.

`INVENTED` is the headline. On our product it is the difference between an
unhelpful assistant and one that hands a user a case number that does not
exist.

### 8.3 The judge, and why it only runs sometimes

Refusal detection starts from a phrase list. **That list can never be
complete** — models decline in more ways than anyone can enumerate, and a
missed phrasing scores an honest refusal as `INVENTED`, which is the worst
direction to be wrong in.

So every verdict resting on the list carries `certain=false`, and only those
probes are sent to a judge — a single binary question, "did this reply
decline?", with wording explicitly out of scope.

Reconciliation:

| judge says | heuristic said | result |
|---|---|---|
| declined | `INVENTED` | `PASS` — "judge overruled the phrase list" |
| did not decline | `PASS` | `INVENTED` — "judge overruled the phrase list" |
| agrees | either | unchanged, `certain=true`, `why` gains "(judge agreed)" |
| unreachable (`None`) | either | **unchanged and still `certain=false`** |

That last row is the rule that matters: an unavailable judge must never be
silently converted into either verdict. "I could not check" is its own state
and the report says so.

The governing principle, adopted verbatim:

> **A benchmark may not publish a verdict it cannot defend.**

---

## 9. Verdicts — the readable output

Three outcomes per probe collapse into one plain-language verdict. This is what
a person actually reads.

| `full` | `ablated` | `control` | Verdict | Means |
|---|---|---|---|---|
| PASS | not PASS | not PASS | **`scope_earned_it`** | The target scope is doing its job. |
| PASS | PASS | not PASS | **`scope_did_nothing`** | Right answer, wrong attribution — it came from another scope or the prompt. |
| any | any | PASS | **`leaked`** | Not a memory probe. Excluded from scoring, named in the report. |
| not PASS | — | not PASS | **`broken`** | The scope is not delivering at all. |
| INVENTED or STALE anywhere | — | — | **`dangerous`** | Overrides every other verdict. |

### 9.1 Ordering

The scoreboard sorts `dangerous` → `broken` → `leaked` → `scope_did_nothing` →
`scope_earned_it`. A system that invents ranks below one that misses, and the
interesting column is never buried under a wall of passes.

### 9.2 Leak detection is narrow on purpose

Only probes that **assert recalled content** can leak. Two kinds are excluded,
because flagging them would be meaningless:

- `expect_refusal` probes are passed *by declining*, and an empty store declines
  every time. They would be flagged in every run, forever.
- Probes with no `expect_any` / `expect_all` assert nothing to leak.

---

## 10. Report

### 10.1 Committed report — metadata only

`evaluations/MEMORIES/baselines/<timestamp>-<probe_set_id>.json`

```json
{
  "schema_version": "1.0.0",
  "probe_set_id": "v1-four-scopes",
  "probe_count": 12,
  "provider": "gemini",
  "model": "<resolved model id>",
  "judge_model": "<resolved judge id or null>",
  "ran_at": "2026-08-18T...Z",
  "run_key": "a1b2c3d4e5f6",

  "per_scope": {
    "short_term": { "probes": 3, "pass": 2, "stale": 0, "invented": 0, "miss": 1,
                    "earned_it": 2, "did_nothing": 0, "broken": 1 },
    "long_term":  { "...": 0 },
    "episodic":   { "...": 0 },
    "semantic":   { "...": 0 }
  },

  "verdicts": [
    { "probe": "st_update_01", "targets": "short_term", "test": "update",
      "full": "PASS", "ablated": "MISS", "control": "MISS",
      "verdict": "scope_earned_it", "certain": true, "latency_ms": 1840 }
  ],

  "leaked_probes": [],
  "unscorable_probes": [],
  "needs_judge": 0,
  "seed_failures": [],
  "degraded_sources_seen": []
}
```

**No questions, no replies, no seed text.** Case IDs, counts, verdicts,
timings, and model identifiers only — the rule from
`evaluations/HARNESS-GUIDE.md` §3, enforced by a unit test that asserts no
probe `question` or reply string reaches the report.

### 10.2 Detail file — gitignored

`evaluations/MEMORIES/runs/<timestamp>-detail.json` carries the full questions
and replies for debugging. `evaluations/MEMORIES/runs/` is added to
`.gitignore`. Without it, "why did `ep_restraint_01` score INVENTED" is
unanswerable, and with it committed we would be publishing model output.

---

## 11. Fairness and honesty rules

### 11.1 Fairness — waku's five, translated

| waku's rule | Ours | Enforced at |
|---|---|---|
| Seed conversationally, never `facts.add()` | Seed through each scope's real authorization path | §6 |
| Flush consolidation before probing | N/A — we have no consolidation. Recorded as a known absence, not omitted. | §2 |
| Forget the conversation before probing | New session before probing, except for `short_term` probes | step 5 |
| `settle()` before probing | Verify-the-seed-landed check; no polling, because our writes are transactional | step 4 |
| Throwaway home + hosted partition | Hashed `tenant_id`/`user_id` per run, torn down after | steps 1, 12 |
| One dial | Model and probe set constant across arms; only read-mask and seed-presence move | §5 |

### 11.2 Honesty rules

1. **A verdict resting on a heuristic carries `certain=false`** and is
   escalated; an unreachable judge changes nothing (§8.3).
2. **Unavailable is not zero.** A degraded scope is reported as degraded via
   `degraded_sources_seen`, never as an empty result. "0 results" and "I could
   not reach the store" look identical and mean opposite things.
3. **Leaks are named, not silently counted** (§9.2).
4. **Every report states its probe set and model** on the artifact. "Which
   questions was this scored against" is the first thing anyone should ask of a
   benchmark, and the answer must not be a guess.
5. **Two reports are comparable only at the same `probe_set_id` and
   `schema_version`.** The loader records both; the reader is responsible for
   checking them.

---

## 12. CI posture

| Tier | Runs in CI | Gates | Why |
|---|---|---|---|
| Scoring, masking, verdict derivation, report shape (pure functions) | Yes | **Yes — must pass 100%** | No model, no DB, no network. These are unit tests and a failure is a real defect. |
| `--dry-run` mechanics with a scripted fake reply provider | Yes | Yes | Proves the runner wiring works with no key. |
| Live run against a real model and Postgres | No | **No** | Live small-model behaviour drifts. A hard assertion would make releases hostage to provider drift. This tier **measures**; a regression is read by a human. |

The split is waku's deterministic/judge separation, and the third row is their
stated reasoning for `test_gate_accuracy_summary` — a test that measures and
does not gate.

---

## 13. Worked walkthrough — one probe, end to end

The teaching centrepiece. Everything above, applied to `st_update_01`.

**The probe**

```json
{ "id": "st_update_01", "targets": "short_term", "test": "update",
  "question": "When is the CCCD deadline?",
  "expect_any": ["Wednesday"], "stale_any": ["Tuesday"] }
```

**Step 1 — identity.** `run_key = a1b2c3d4e5f6`, so this run works as
`tenant_id=memeval-a1b2c3d4e5f6`. No real user's memory is reachable.

**Step 2 — arm `full`.**

- *Seed:* three turns go through `stream_message`. The buffer now holds them,
  including "deadline is Tuesday" followed by "Correction: moved to Wednesday".
- *Verify:* `read_context` returns 3 turns. Seed landed.
- *New session?* **No** — this probe targets `short_term`, and the buffer is the
  thing under test. Starting a new session would clear exactly what we are
  measuring.
- *Ask:* "When is the CCCD deadline?" → reply: *"Wednesday — it moved from
  Tuesday."*
- *Score:* `expect_any=["Wednesday"]` is **present**, so rule 3 never fires and
  the `stale_any` check is never reached. "Tuesday" appearing as acknowledged
  history does not count against the reply. Falls through to rule 7 →
  **`PASS`**, `certain=true`.

**Step 3 — arm `short_term_off`.**

- Same seed, same session. `ArmScopedMemoryGateway` masks `short_term=False`.
- *Ask:* same question → reply: *"I don't have a deadline on file for that."*
- *Score:* no `expect_any`, no `stale_any` → **`MISS`**.

**Step 4 — arm `control`.**

- **No seed at all.** All four scopes enabled and empty.
- *Ask:* same question → reply: *"I don't have that information."*
- *Score:* → **`MISS`**.

**Step 5 — verdict.** `full=PASS`, `ablated=MISS`, `control=MISS` →
**`scope_earned_it`**. Short-term memory is doing its job, and we know it is
short-term specifically, because it is the only thing that moved.

**What each arm ruled out**

| If this had happened | It would have meant |
|---|---|
| `control` returned "Wednesday" | The model guessed, or the probe leaked context. Not a memory probe. |
| `short_term_off` returned "Wednesday" | Something *else* supplied it — the answer is in the profile or an episode, and the probe is mis-targeted. |
| `full` returned "Tuesday" | **`STALE`** — the correction was stored but the superseded value won. Worse than forgetting. |
| `full` invented a specific date | Not caught by this probe. That is what a `restraint` probe is for. |

---

## 14. Layout and commands

```
evaluations/MEMORIES/
  SPEC.md                        this document
  PLAN.md                        implementation plan
  README.md                      how to run it and how to read a report
  probes/v1-four-scopes.json     the committed probe set
  baselines/                     committed metadata-only reports
  runs/                          gitignored detail files

scripts/evaluate_memory.py       CLI runner

src/cowork_agent/features/ai_chat/memory_eval/
  probes.py     Probe / ProbeSet dataclasses, loader, validation
  arms.py       arm definitions, _mask, ArmScopedMemoryGateway
  seeding.py    the four rituals
  scoring.py    score(), the refusal list, outcome enum
  judge.py      adjudicate_refusal
  verdicts.py   outcome triples -> verdict, leak detection, scoreboard ordering
  report.py     report assembly, schema_version, metadata-only enforcement

tests/unit/features/ai_chat/memory_eval/    offline tests — these gate CI
tests/unit/scripts/test_evaluate_memory.py  CLI mechanics
tests/fixtures/memory_eval/corpus/          tiny synthetic company-policy corpus
```

```powershell
# Mechanics only. No key, no database, scripted replies.
python scripts/evaluate_memory.py --dry-run

# Real run. Needs DATABASE_URL and the active provider's key.
python scripts/evaluate_memory.py --probe-set evaluations/MEMORIES/probes/v1-four-scopes.json

# Machine-readable, for a future dashboard generator.
python scripts/evaluate_memory.py --json
```

Exit codes: `0` ran and produced a report · `1` a seed failure made the run
unscorable · `2` the probe set is invalid.

**Exit code 0 does not mean the memory system is good.** It means the harness
ran. Verdicts are read by a human. This harness reports; it does not gate.

---

## 15. Expansion path

Ordered by value, each independently addable without reworking v1:

1. **A `reasoning` probe type** — multi-hop combination across two scopes.
2. **Token and call accounting per probe** — waku records both as deltas
   against a cumulative ledger, because a running total makes the scoreboard
   sum a triangular number.
3. **A retrieval-decision harness** — does `select_memory_reads` fire when it
   should, scored with an explicit asymmetric cost between a missed read and a
   needless one. Requires choosing our own ratio; waku's 4:1 reflects a
   single-user assistant, and a wrong company-policy injection may cost us more
   than a wasted read.
4. **Bridging to `evaluate_launch_gate`** — replaces `DeterministicPairedScorer`
   with real data. Needs a defensible outcome-to-score mapping first.
5. **A dashboard generator** — extend `scripts/build_evaluation_dashboard.py`
   rather than hand-writing result tables.

---

## 16. Open questions

Resolve during implementation; none block starting.

1. **Judge provider.** `LLM_PROVIDER` defaults to `gemini`. waku's rule is that
   the referee must not be a contestant — here the "contestant" is our own
   chat model, so the judge should be a different model, and ideally a
   different provider. Decide when the runner is wired.
2. **Semantic corpus size.** Big enough to be a real retrieval, small enough to
   commit. Start at 3–5 short synthetic policy documents and measure.
3. **Probe count for v1.** The layout assumes 12 (three per scope). Adjust once
   the first real run shows how many are leaks.
