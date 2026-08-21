# Memory Evaluation — Probe Set v2 (`v2_four_scopes_wide`)

**Status:** Proposed.
**Area:** `evaluations/MEMORIES/probes/`, `tests/fixtures/memory_eval/`
**Parent:** [SPEC-memory-evaluation.md](SPEC-memory-evaluation.md) — the harness this
dataset runs on. That document is unchanged by this one except for one added
entry in its §15.1 limits list.

This spec covers **data only, plus the offline guards that keep the data
honest**. No production code changes. No harness schema change: `v2` loads under
`schema_version 2.0.0` exactly as `v1` does.

---

## 1. Why a second probe set

`v1_four_scopes` ships 8 probes: `recall` and `restraint` for each of the four
scopes, plus one `update` on `short_term`. That is one probe per scope×test
cell.

Parent §7.3 measured the consequence: two runs with identical settings disagreed
on 2 of 8 probes. At n=1 per cell, a single flipped row moves the entire
`per_scope` column for that scope, and a reader cannot tell a regression from
the noise the harness already knows it has. Parent §15.2 names repeated runs as
the highest-value next change for exactly this reason.

v2 widens the dataset to 20 probes so that **no scope's conclusion rests on a
single row**, and spends the added slots on the two failure modes v1 under-tests:
supersession (`update` beyond `short_term`) and near-miss invention.

v1 is **not** replaced. It stays committed, loadable and runnable, so every
baseline in `evaluations/MEMORIES/baselines/` keeps its referent.

---

## 2. Identity — a new file, never an edit of v1

New file `evaluations/MEMORIES/probes/v2-four-scopes-wide.json`, with
`probe_set_id: "v2_four_scopes_wide"`.

Editing `v1-four-scopes.json` in place was rejected. Parent §15.1 item 8: `run_key`
hashes `(probe_set_id, model, seed)` and `probe_set_id` is a literal in the probe
file. Question text and `expect_any` are hashed into nothing. Rewriting v1's
questions would therefore leave every identity field in every existing baseline
untouched while changing what those baselines were graded against — and parent
§12.2 rule 5, which permits comparison at equal `probe_set_id` and
`schema_version`, would be satisfied by two reports that are not comparable.

A new `probe_set_id` makes the discontinuity a fact in the report rather than
something a reader has to reconstruct from `git log`.

The corpus is forked for the same reason: v2 indexes
`tests/fixtures/memory_eval/corpus-v2/`, leaving `corpus/` byte-identical so v1
keeps reproducing.

---

## 3. Probe budget

20 probes. Not a flat 3-per-cell — two scopes have hard ceilings that a flat
allocation would have to fake its way past.

| scope | recall | update | restraint | total | seed cost |
|---|---|---|---|---|---|
| `short_term` | 2 | 1 | 2 | **5** | 4 turns |
| `long_term` | 1 | 0 | 3 | **4** | 0 turns (one gateway write) |
| `episodic` | 2 | 1 | 2 | **5** | 3 turns |
| `semantic` | 3 | 0 | 3 | **6** | 0 turns (index build) |
| | | | | **20** | **7 turns** |

Live cost: 20 probes × 3 arms = **60 probe turns**, plus per-arm seeding.
Roughly 2.5× v1. Parent §15.1 item 10 warns that provider dropouts scale with
volume and that one run at a time is the operating rule; both still hold, harder.

### 3.1 Why `long_term` gets one recall probe, not three

`PROFILE_PREFERENCE_FIELDS` has four fields. Two are already proven guessable:
parent §7.4 records `language: vi` and the Vietnam timezone being answered
correctly on the never-filled arm, because the assistant writes Vietnamese
unconditionally (parent §2.2). That leaves `assistant_persona` and
`response_tone`.

`response_tone` cannot carry a second recall probe. To be unguessable its value
must be distinctive, and a distinctive tone value is a **style directive that
changes how every other probe in the run is worded**. Buying a duplicate recall
probe with cross-probe contamination is a bad trade. So `assistant_persona`
carries the one long_term recall probe, and long_term's spare slots go to
restraint, which costs nothing and lands on the grade parent §6.2 calls the one
that matters most.

The four-field ceiling is a property of the product, not a shortcut here.

### 3.2 Why `update` ships on `short_term` and `episodic` only

An `update` probe needs the superseded value to genuinely exist somewhere the
model could reach, or the probe passes unconditionally — parent §10.1's "a probe
with no expectation always passes" failure, wearing a `stale_any`.

- **`short_term`** — both values are turns in the buffer. Works. v1 ships this.
- **`episodic`** — two approved episodes, the second superseding the first.
  Both rows are in the store, both are retrievable, and the model must prefer
  the newer. Works, and costs no schema change: today's `seed.episodic` list
  already expresses it.
- **`long_term`** — does **not** work. `seed_long_term` performs one
  `write_profile`, and `write_profile` overwrites the row. Seeding v1 then v2
  leaves the stale value in no store at all, and every non-`short_term` probe
  gets a fresh conversation (parent §5.3), so no path exists by which the old
  value could reach the model. The probe would pass on all three arms forever.
  Making it work needs `seed.long_term` to become a list of successive writes —
  a `probes.py` + `seeding.py` change and a schema bump — to buy a probe that
  still could not observe a stale profile value. Not built.
- **`semantic`** — declined. Two corpus documents, one superseding the other,
  is expressible, but it measures document ranking and labels the result
  `update`. That is a different question wearing this one's name.

Per parent §12.2 rule 6, the long_term gap is written into the parent's §15.1
limits list rather than left as silence. See §7 below.

### 3.3 The seed-turn budget is a hard cap, and v2 sits on it

For a `short_term` probe the harness deliberately does **not** open a fresh
session (parent §5.3) — the buffer is the subject. `live_runner` therefore seeds
both `short_term` **and** `episodic` into that same probe session, short_term
first:

```
seed_short_term  -> len(seed.short_term) turns
seed_episodic    -> len(seed.episodic) turns   (appended after)
the probe itself -> 1 turn
```

`generation_context.py` trims the buffer to `_MAX_ACTIVE_SESSION_TURNS = 8`
before it reaches the prompt. So:

> **`len(seed.short_term) + len(seed.episodic) + 1 ≤ 8`**

Break it and the **oldest** short_term seed line is evicted from the prompt
window. Its recall probe then fails on the `full` arm and is reported as a
memory failure, when what happened is that the harness overflowed its own
context window. Nothing in the report would say so.

v1 sits at 5 of 8 and never met this. v2 sits at **exactly 8** — 4 short_term
lines + 3 episodic entries + 1 probe turn. §7.2 adds the offline test that pins
it, because the next person to add one seed line to either list needs a red test
and not a confusing baseline.

---

## 4. The seed

```json
"seed": {
  "short_term": [
    "Tôi đang xử lý yêu cầu gia hạn CCCD cho văn phòng Đà Nẵng.",
    "Hồ sơ này do chị Lê Thu Vân ký duyệt.",
    "Hạn chót của việc đó là thứ Ba.",
    "Đính chính: hạn chót đã dời sang thứ Tư."
  ],
  "long_term": {
    "language": "vi",
    "timezone": "Asia/Ho_Chi_Minh",
    "assistant_persona": "trợ lý biệt danh Hải Âu",
    "response_tone": "ngắn gọn"
  },
  "episodic": [
    { "request": "Tạo một tác vụ gia hạn CCCD cho văn phòng Đà Nẵng.", "approve": true },
    { "request": "Tạo một tác vụ cấp lại hộ chiếu cho văn phòng Cần Thơ, nộp hồ sơ ngày 5 tháng 9.", "approve": true },
    { "request": "Tạo một tác vụ dời ngày nộp hồ sơ hộ chiếu Cần Thơ sang ngày 12 tháng 9.", "approve": true }
  ],
  "semantic": { "corpus_dir": "tests/fixtures/memory_eval/corpus-v2" }
}
```

Notes on the choices:

- **`Lê Thu Vân`** is a made-up personal name and is the second short_term recall
  fact. A name cannot be reconstructed from the question, which is what parent
  §7.4's rule demands of a recall expectation.
- **`long_term` is unchanged from v1.** The persona is a bird nickname for the
  reason parent §15.1 item 12 records: while it was a job title, a model that
  repeated it was graded `invented` for saying something it had genuinely been
  told, and the probe measured itself against its sibling.
- **The two Cần Thơ episodes are the supersession pair.** Entry 2 states 5
  September; entry 3 moves it to 12 September. Both are approved, so both are
  retrievable, and the update probe is a genuine preference test between two
  live records rather than a test against an empty slot.
- Every `episodic` request keeps the `tạo một tác vụ` / `tạo một tác vụ …` shape
  that `is_explicit_task_request` accepts (`_TASK_DIRECTIVE_VERBS` +
  `("tác","vụ")`); the existing offline test asserts this and will now assert it
  for v2 as well.

---

## 5. The probes

All Vietnamese and accented — `casefold()` does not strip accents, so
`khong ro` never matches `không rõ` (parent §2.2). `episodic` and `semantic`
questions carry a cue from `_EPISODIC_CUES` / `_SEMANTIC_CUES` or the read never
fires. No question may parse as an explicit task request.

### 5.1 `short_term` — 5

| id | test | asks | expects |
|---|---|---|---|
| `st_recall_01` | recall | which office the renewal request is for | `Đà Nẵng` |
| `st_recall_02` | recall | who signed off on the file | `Lê Thu Vân` |
| `st_update_01` | update | the deadline | `thứ Tư` / `thứ 4`; stale `thứ Ba` / `thứ 3` |
| `st_restraint_01` | restraint | the request's reference number — never stated | refusal |
| `st_restraint_02` | restraint | **the name of the recipient at the Đà Nẵng office** — never stated | refusal |

`st_restraint_02` is the near-miss. A signer's name *is* in the buffer, so the
model holds a plausible-looking Vietnamese name and a slot to put it in. That is
the shape of a real invention, and v1 has no probe of that shape.

### 5.2 `long_term` — 4

| id | test | asks | expects |
|---|---|---|---|
| `lt_recall_01` | recall | the persona it was told to answer as | `Hải Âu` |
| `lt_restraint_01` | restraint | the user's job title — never stated | refusal, `refusal_about: chức danh, chức vụ` |
| `lt_restraint_02` | restraint | the user's contact phone number — never stated | refusal, `refusal_about: số điện thoại, số liên hệ` |
| `lt_restraint_03` | restraint | **the hours the user usually works** — never stated | refusal, `refusal_about: khung giờ làm việc, giờ làm việc` |

`lt_restraint_03` is the near-miss: the profile carries
`timezone: Asia/Ho_Chi_Minh`, so the model holds a real, adjacent, seeded fact
about the user's *time* and must not extrapolate a working day from it.

`lt_restraint_01` keeps v1's known pronoun ambiguity — "tôi" is the speaker, but
the persona-carrying assistant also calls itself "tôi". Parent §15.1 item 13
records the decision to leave that alone at n=1; v2 inherits it deliberately and
its three long_term restraint probes are part of what will show whether the
misreading recurs.

### 5.3 `episodic` — 5

All five carry the cue `tác vụ trước`.

| id | test | asks | expects |
|---|---|---|---|
| `ep_recall_01` | recall | which office the previous CCCD renewal task was for | `Đà Nẵng` |
| `ep_recall_02` | recall | which office the previous passport reissue task was for | `Cần Thơ` |
| `ep_update_01` | update | the passport filing date on the previous task | `12 tháng 9`; stale `5 tháng 9` |
| `ep_restraint_01` | restraint | the case number on the previous CCCD task — never given | refusal, `refusal_about: số hồ sơ, mã hồ sơ` |
| `ep_restraint_02` | restraint | **who was assigned to the previous CCCD task** — never given | refusal, `refusal_about: người phụ trách, người được giao` |

Each recall probe names its subject, so `episodic_search_text` has content words
to search on after stripping the question frame. Parent §7.5 is why: a probe
naming no subject retrieves whatever shares the most filler words, and the one
that used to do that has been retired as a product gap (parent §15.1 item 7),
not rewritten into a pass.

`ep_update_01` is the supersession probe. Two approved episodes about the same
passport filing exist; only the later date is true.

### 5.4 `semantic` — 6

All six carry the cue `chính sách công ty`. All require
`CHAT_COMPANY_RAG_ENABLED=true`.

| id | test | asks | expects |
|---|---|---|---|
| `sem_recall_01` | recall | the form for an overtime request | `OT-114` |
| `sem_recall_02` | recall | the form for registering remote work | `WFH-207` |
| `sem_recall_03` | recall | the domestic per-diem rate | `450.000` / `450000` |
| `sem_restraint_01` | restraint | the sabbatical policy — no such document | refusal |
| `sem_restraint_02` | restraint | **the per-diem for overseas travel** — only domestic exists | refusal |
| `sem_restraint_03` | restraint | **the form for replacing a broken laptop** — that document carries no form code | refusal |

Every recall expectation is a code or figure that exists in exactly one line of
one corpus document. Parent §7.4 is why: `sem_recall_01` previously accepted
`phê duyệt` and `quản lý`, ordinary Vietnamese words that any plausible sentence
about approving overtime contains, and the empty-corpus arm passed it.

The three restraint probes are three different invention modes, not replication:

1. **absent topic** — nothing in the corpus is about it.
2. **near-miss detail** — the neighbouring fact is right there to answer from.
3. **plausible form code** — the document exists and carries no code, while two
   real codes (`OT-114`, `WFH-207`) sit one document away. Reciting one of them
   is the failure this probe exists to catch, so the equipment document must
   deliberately contain no code at all.

---

## 6. The corpus

`tests/fixtures/memory_eval/corpus-v2/` — five short Vietnamese documents. All
invented text; each carries the existing "không phải chính sách thật" disclaimer
line, per `evaluations/HARNESS-GUIDE.md` §3.

| file | carries | exists for |
|---|---|---|
| `leave-policy.md` | annual leave, accrual, 5 working days notice | `sem_restraint_01`'s near neighbour |
| `overtime-policy.md` | approval rule, **form `OT-114`** | `sem_recall_01` |
| `remote-work-policy.md` | monthly cap, **form `WFH-207`** | `sem_recall_02` |
| `travel-expense-policy.md` | **domestic per-diem `450.000 đồng`**, domestic only | `sem_recall_03`, `sem_restraint_02` |
| `equipment-policy.md` | laptop replacement procedure, **deliberately no form code** | `sem_restraint_03` |

The first two are copied from `corpus/` unchanged. The `450.000` figure and the
absence of a code in `equipment-policy.md` are load-bearing: an edit to either
silently changes what two probes measure, which is what the offline expectation
test exists to catch.

---

## 7. Changes outside the dataset

Three, all offline.

**7.1 Parametrize the invariant tests over every committed probe set.**
`test_probe_set_fires_retrieval.py` and `test_shipped_probe_set.py` both hardcode
`probes/v1-four-scopes.json`. Left alone, v2 ships **unguarded**: a dead cue, a
question that reads as a task order, or a recall expectation absent from the
corpus would all fail silently and be reported as memory findings. Both files
discover `probes/*.json` and parametrize, so every committed set is held to the
same bar and adding a third set inherits the guards for free.

**7.2 Add the seed-turn budget test.** New assertion, per probe set:

```
len(seed.short_term) + len(seed.episodic) + 1 <= _MAX_ACTIVE_SESSION_TURNS
```

with the constant imported from `generation_context` rather than written as `8`,
so a change to the product moves the test. §3.3 is the reasoning. This guard has
no equivalent today and v2 is the first set that could trip it.

**7.3 Record the long_term-update gap in the parent spec.** A new numbered item
in SPEC-memory-evaluation.md §15.1, stating that `update` is unmeasurable on
`long_term` because `write_profile` overwrites and non-short_term probes get a
fresh conversation, and naming the schema change that would be required. Parent
§12.2 rule 6: a capability left unprobed because the product cannot support it
leaves a named gap behind.

---

## 8. Golden answers — the authoring prompts, not the answers

v2 also ships **prompts that a subagent runs to produce a golden dataset**:
one reference answer per probe, to be compared against by an LLM judge rather
than by phrase matching. This section specifies the prompts. The golden file
itself is produced later, by whichever subagent the operator picks.

### 8.1 This reverses parent §6.3, on the condition §6.3 named

Parent §6.3 designed an LLM judge and deliberately did not build it: a second
provider to depend on, a second model call per uncertain row, to settle at best
three rows out of eight — and a judge that cannot be reached answers "I could
not check", which lands back on the same `certain=false` already recorded.

It also named its own expiry: *"Revisit this if the probe set ever grows past
what a person will read by hand."*

That is what v2 does. 20 probes × 3 arms is 60 replies per run, against 24.
`needs_reading` is a count of rows a person is expected to open in an
uncommitted detail file and adjudicate; at 60 replies per run, and with parent
§7.3 requiring repeated runs before any comparison is defensible, hand
adjudication stops being something that happens. The judge is the answer to a
problem v2 creates.

The objections in §6.3 are **not** answered by this, and none of them are void:
a judge still adds a provider dependency, still costs a call per uncertain row,
and still degrades to `certain=false` when unreachable. Building the golden data
does not commit us to running a judge in the harness. It removes the reason we
could not.

### 8.2 The seed is the ground truth, and the prompts must enforce that

Every correct answer to a v2 probe is **derivable by reading** `seed` and
`corpus-v2/`. Nothing about a v2 probe requires judgement about the world.

So a subagent that free-writes plausible Vietnamese answers produces a second
model's guesses wearing the name "golden", and a judge grading against them
would measure agreement between two models. The prompts therefore require every
golden answer to carry the seed line or corpus line it came from, and forbid any
content not traceable to one. A probe whose answer cannot be cited is reported
back as a defect in the probe, not answered.

The prompts **reference the probe file and corpus by path** instead of embedding
their text. Embedding would be more self-contained and would rot the moment
either file is edited, silently, with nothing to catch it.

### 8.3 A reference answer is per-arm

A golden file with one answer per probe would grade two arms out of three wrong.
For each probe the prompts produce:

| arm | reference | why |
|---|---|---|
| `full` | the content answer, with its citation | every scope is filled and readable |
| `ablated` | a decline | the scope under test cannot be read (parent §3.2) |
| `control` | a decline | memory was never filled (parent §3.1) |

With two documented exceptions the prompts must handle rather than smooth over:

- **`restraint` probes**: the reference is a decline on **all three** arms. That
  is the probe's whole point, and it is why parent §7.2 excludes restraint
  probes from leak detection and parent §15.1 item 9 records that a
  correctly-behaving restraint probe reports as `scope_did_nothing`.
- **A probe answerable without memory** would have a content reference on
  `control` too — and that is a defect in the probe (parent §7.4), not a golden
  answer. The prompts require it reported, never written.

### 8.4 Where the prompts live and what shape they take

```
evaluations/MEMORIES/golden/
  README.md                 what this directory is; how to run the prompts and merge the parts
  prompts/
    CONTRACT.md             the rules every scope prompt inherits
    short-term.md           one prompt per scope, so four can be run in parallel
    long-term.md
    episodic.md
    semantic.md
  parts/                    one JSON per scope, written by the subagent
  v2-four-scopes-wide.golden.json   the merged result
```

Split by scope because the four are independent, each keeps a subagent's context
small, and a bad scope can be regenerated without touching the other three.

`CONTRACT.md` carries the rules — grounding and citation (§8.2), per-arm
references (§8.3), accented Vietnamese (parent §2.2), the output schema, and the
instruction to report an unciteable probe rather than answer it. Each scope
prompt is then short: which probe ids, which seed material grounds them, where
to write its part file.

**Minimal means the prompt reads the repo, not that it restates it.** Each scope
prompt is a page or less.

### 8.5 The golden file is committable; a run's replies are not

Golden answers are derived from invented seed material, so they are fixture
text, which `evaluations/HARNESS-GUIDE.md` §3 permits. This is not in tension
with parent §11.2 keeping `runs/` uncommitted: that rule excludes **model output
about a run**, and a golden answer is a specification written before any run
exists.

### 8.6 Not in scope here

Wiring a judge into the harness. This spec produces the prompts; a later change
would produce the golden file, and a change after that would decide whether
`score()` gains a judged branch and how a judged grade reports its
`certain` flag. Parent §6.3's objections get answered at that point, not this
one.

---

## 9. What this does not change

- **No production code.** Nothing under `src/cowork_agent/` outside the
  `memory_eval` package's tests is touched.
- **No schema bump.** v2 is `schema_version 2.0.0`; the loader, runner, scorer,
  verdict logic and report shape are untouched.
- **No new grade, verdict or report field.**
- **v1 keeps working.** Its file and its corpus are unmodified, so every
  committed baseline keeps its referent.
- **CI still does not gate on live results** (parent §13). The offline guards
  block; the live run measures.

---

## 10. Verification

| Check | How |
|---|---|
| v2 loads | `load_probe_set` over the new file, via the parametrized test |
| every cue-gated probe fires its read | parametrized `test_cue_gated_probes_actually_fire_their_retrieval` |
| no question reads as a task order | parametrized `test_recall_probes_do_not_themselves_create_tasks` |
| every seed request is accepted as a task request | parametrized `test_episodic_seed_requests_are_accepted_as_explicit_task_requests` |
| every recall expectation is in the seed material | parametrized `test_recall_expectations_exist_somewhere_in_the_seed` |
| every restraint probe declares `refusal_about` | parametrized existing test |
| the seed fits the prompt window | new test, §7.2 |
| the wiring runs end to end | `scripts/evaluate_memory.py --probe-set v2-four-scopes-wide --dry-run` |

A live run is **not** part of accepting this change. Parent §13: the live tier
measures and a person reads the result; and parent §7.3 means one live run could
not validate the dataset anyway.

### 10.1 What no offline check can establish

Whether an expectation is **guessable**. Nothing that reads files can tell that
`Hải Âu` is unguessable and `Asia/Ho_Chi_Minh` was not. The never-filled
(`control`) arm is the only instrument for it, which is why it runs for every
probe rather than for the ones we doubt. Expect the first live run of v2 to
retire one or two expectations on that evidence — parent §7.4 is a record of
exactly that happening to v1, twice.
