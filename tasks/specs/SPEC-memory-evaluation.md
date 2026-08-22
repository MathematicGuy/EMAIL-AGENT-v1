# Memory Evaluation — Specification (v1)

**Status:** Implemented. Runs live against PostgreSQL and SQLite.
**Area:** `evaluations/MEMORIES/`
**Companion:** [Waku-Memory-and-Evaluation-Comparison.md](../../docs/references/Waku-Memory-and-Evaluation-Comparison.md)

This document explains **what the harness measures and why it was built this
way**. For the plain-language walkthrough, see
[MEMORY_IN_A_NUTSHELL.md](../../evaluations/MEMORIES/MEMORY_IN_A_NUTSHELL.md).
To run it, see [README.md](../../evaluations/MEMORIES/README.md).

**Reading order.** Section 0 is a name table. Sections 1–7 are the design, in
the order the harness runs: what it measures, how it asks, how it fills memory,
how it grades, how grades become a conclusion. **Section 8 is a worked
example.** If you read one section, read that one. Sections 9–15 are reference —
look things up there.

---

## 0. Plain words here, short names in the code

This document uses plain words. The code and the report files use shorter
names for the same things. Here is the pairing, so you never have to guess
which is which.

| Plain word used here | Name in the code and report files |
|---|---|
| **test question** — one question we ask, written down together with what a good answer must contain | `probe`, the `Probe` class, the `probes/` folder |
| **memory type** — one of our four kinds of memory | `scope`, and the `per_scope` and `targets` fields |
| **setting** — one of the three ways we ask the same question (§3) | `arm`, the `Arm` class |
| **memory switched off** — the setting where the memory being tested cannot be read | the `ablated` column in the report |
| **fill** — to put memory in place before asking | `seed`, and the `seed_failures` field |
| **grade** — how one answer scored: right, missing, out of date, or made up | the outcome values `pass`, `miss`, `stale`, `invented` |
| **conclusion** — the one-line summary for a question, worked out from its three grades | `verdict`, and the `verdicts` list |
| **source stamp** — a marker saved next to a value saying where it came from | provenance, `MemoryProvenance` |

Three sentences that use all of it:

> Each **test question** is asked three times, once in each **setting**. Each
> answer gets a **grade**. The three grades give one **conclusion** about
> whether that **memory type** did the work.

---

## 1. What this measures

For each of our four kinds of memory, four questions:

1. **Does it keep what was put in it?** (`recall`)
2. **Does a correction replace the old value?** (`update`)
3. **Does it refuse to make things up when it was never told?** (`restraint`)
4. **Can it show one user memory belonging to someone else?** (`isolation`)

And one thing more, which is why this is a harness and not a pile of unit
tests: **for every result, we know which of the four kinds of memory produced
it.** "Memory helped" is never a number you have to take on trust.

That is the whole idea. Section 3 is how it is done. Everything else in this
document exists to keep section 3 honest.

### 1.1 Why it exists

Our older memory evaluation
(`src/cowork_agent/features/ai_chat/evaluation.py`) has a sound experiment and
no real data. Its scorer, `DeterministicPairedScorer`, is a hardcoded lookup
table. Its own docstring calls it "a STAND-IN for real model scoring at MVP
tier", and its numbers were tuned until the results cleared the 0.6 pass mark.
It proves the arithmetic of the gate works. It cannot catch a regression,
because it never measures anything.

Separately, about 80 unit tests cover our memory *rules*. They answer: **can
memory leak, or be written without permission?** Nothing answered: **does memory
make the answer better, and which memory did it?**

This harness answers the second question. It does not replace the first.

### 1.2 What it does not measure

Listed so nobody assumes coverage that is not here.

- **It does not feed `evaluate_launch_gate`.** The older report and its stand-in
  scorer are untouched. Connecting them is a separate decision: you would have
  to turn our grades into scores, and that mapping has to be defensible, not
  just convenient.
- **It does not measure token cost.** Answer time only.
- **It does not test multi-step reasoning.** See §10.3.
- **It does not judge whether looking things up was the right call.** Whether
  `retrieval_policy.select_memory_reads` was *right* to fire is a different
  experiment. It needs its own labelled data and its own cost model.
- **It does not block CI on live results.** See §13.
- **It does not ship an isolation question**, even though isolation is one of
  the four questions above. §5.2 explains why, and why a fake one would be worse
  than none.

---

## 2. The system being measured

### 2.1 The four kinds of memory

Every later section points back to this table.

| Memory type | Where it lives | What it holds | How it gets there | How long it lasts |
|---|---|---|---|---|
| `short_term` | `InMemoryChatSessionBuffer`, in the running process | The newest 20 turns of *this* conversation | Automatically, every turn | 30 minutes idle, then gone |
| `long_term` | `DeclarativeProfile` in Postgres | `language`, `timezone`, `assistant_persona`, `response_tone` — the 4 fields in `PROFILE_PREFERENCE_FIELDS`, 200 characters each | **Only** a write carrying the source stamp `EXPLICIT_USER_CONFIG` | 90 days |
| `episodic` | `TaskEpisode` / `ChatSummaryEpisode` in Postgres | Tasks the user asked for | An explicit task request, then an approval | 90 days |
| `semantic` | Company document store | Company policy documents | Loaded separately; reading needs a flag **and** a trigger phrase | As long as the documents live |

Three facts fall out of that table, and they shape everything:

**A model cannot write to `long_term` or `episodic`.** Both need explicit
permission, enforced in `profile_policy.py` and `episode_policy.py`. This is our
deepest difference from waku-agent, where a cheap model decides what becomes
permanent memory. For us it means filling those two kinds of memory cannot be
done by chatting. See §5.

**A newly written task cannot be found yet.** `authorize_task_episode_write`
forces `retrieval_eligible=false` on every new task. It becomes findable only
when its status changes to `USER_APPROVED` or `COMPLETED`. So filling episodic
memory takes three steps where the others take one. That is a fact about the
product, not an inconvenience in the harness.

**`semantic` is off by default.** `CHAT_COMPANY_RAG_ENABLED` defaults to
`false`. Even when it is true, `retrieval_policy` still needs a trigger phrase
in the question. A semantic question with no trigger phrase tests nothing at
all.

### 2.2 The product answers in Vietnamese, so the questions are Vietnamese

The assistant writes Vietnamese always. The chat system prompt says so, and says
that `stored_preference.language` never overrides it.

So every part of this harness that touches human language has to be Vietnamese.
Each part broke in its own way while it was still English:

| Where | What went wrong while it was English |
|---|---|
| Test questions and fill sentences | We stored and asked in one language and the model replied in another, so a correct answer could be graded as missing. |
| `REFUSAL_PHRASES` | An honest Vietnamese refusal was graded "made up" — our worst grade — for a model behaving correctly. |
| `_EPISODIC_CUES`, `_SEMANTIC_CUES` | A Vietnamese question carried no trigger phrase, so nothing was ever looked up. Four of eight questions measured nothing while reporting a memory failure. |
| Task words in `retrieval_policy` | The verb list had `tạo`/`lập`/`lên`, but the nouns those verbs could point at were English apart from `kế hoạch`. So "Tạo một tác vụ" was refused, and filling episodic memory wrote nothing. |
| Company document files | A Vietnamese question against English documents measures translation between languages. That is a different experiment from the one we claim to run. |

Two rules follow. Tests enforce them, not habit.

**Accents matter.** Trigger-phrase matching and grading both use `casefold()`.
That ignores capital letters but does **not** remove accents, so `khong ro`
never matches `không rõ`. Trigger phrases and expected answers are stored with
accents. Whether we should also accept unaccented typing is a real question
about how people type, and it is left open rather than guessed at.

**A trigger phrase has to be unambiguous.** `trước` means both "previous" and
"before". A bare `công việc trước` trigger fires on *"bàn giao công việc trước
khi nghỉ phép"* — "hand over work **before** taking leave" — an unrelated
sentence paying for a lookup it never asked for. So general nouns need the
trailing `đó`, which pins them to the past.

`tests/unit/features/ai_chat/memory_eval/test_probe_set_fires_retrieval.py`
holds the committed questions to this. Every `episodic` and `semantic` question
must actually trigger its lookup, every episodic fill request must be accepted
as a task request, and no test question may itself read as an order to create a
task.

---

## 3. The three settings — how we know which memory did the work

Every test question is asked three times. Same question, three settings.

| Setting | Column in the report | What is different | What it tells you |
|---|---|---|---|
| everything on | `full` | Nothing. All four memories filled and readable. | The system as shipped. |
| one memory switched off | `ablated` | The memory being tested cannot be read. Everything else identical. | Whether that memory was actually needed. |
| memory left empty | `control` | **Memory is never filled.** All four stay switched on, and empty. | Whether the question needed memory at all. |

Read the three together and you learn something no single run can tell you. If
the answer is right with everything on, wrong with one memory switched off, and
wrong with memory empty, then that one memory is what produced the answer.

In the code the "one memory switched off" setting is named after what was
switched off (`short_term_off`, `episodic_off`, …), because that matters when
reading a run. It is stored under the fixed column name `ablated` so every row
of the report has the same three columns whatever was tested. The `targets`
field on the same row says which memory `ablated` refers to.

### 3.1 "Memory left empty" empties the memory — it does not switch off reading

This is the easiest thing to get backwards, and getting it backwards breaks the
harness quietly.

If this setting switched off *reading*, it would just be a second "one memory
switched off" run. Then a question the model can answer from its own training
would come out right in the "everything on" run and look like a memory success.
waku-agent hit exactly this: three of their seven questions could be answered
with an empty store, "and nothing on screen said so."

So this setting runs the *same* system with the *same* reading switched on,
against an *empty* store. A right answer there means one thing only: this
question did not need memory. §7.2 turns that into a `leaked` conclusion.

### 3.2 Where the harness plugs in

`retrieval_policy.select_memory_reads` is called deep inside the controller, at
[controller.py:892](../../src/cowork_agent/features/ai_chat/controller.py).
There is no parameter to pass a setting down to it.

The harness changes **no production code**. It builds the controller with a
subclass that hides one memory on the way past:

```python
class ArmScopedMemoryGateway(MemoryGateway):
    def __init__(self, *args, masked_scope: MemoryType | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._masked_scope = masked_scope

    async def read_context(self, request):
        return await super().read_context(_mask(request, self._masked_scope))
```

Hiding the *read* rather than emptying the *store* is the truthful version of a
setting. The question being asked is "what does the reply look like when this
memory cannot be read", and that is exactly what a gateway controls. Writes,
documents and task approvals are inherited unchanged.

`_mask` is a plain function on `MemoryContextRequest`. It sets
`short_term=False`, or `long_term=False`, or swaps the episodic/semantic read
for its switched-off form. You can unit-test it with no database and no model.

**Rejected alternative:** adding a `memory_reads_override` parameter to
`ChatController.__init__`. It works, but it puts a test-only hook into
production code to do something a subclass already does cleanly. Reconsider only
if the subclass ever needs to override more than `read_context`.

---

## 4. Keeping a run separate from everything else

```
run_key   = sha256(probe_set_id + model + serialized_seed)[:12]
nonce     = uuid4().hex[:8]                 # fresh for every run
tenant_id = f"memeval-{run_key}-{nonce}"
user_id   = f"memeval-{run_key}-{nonce}"
```

A run cannot touch a real user's memory. waku does the same thing, because their
benchmark runs were writing test data into the operator's real memory account
under a shared default user.

`run_key` names the run in the report; the nonce names its stores. They are
separate on purpose. The report and the offline runner key on `run_key`, which
must mean the same thing in two processes, so it can carry nothing per-run. But
identities built from `run_key` alone made two runs of the same probe set and
model address the same stores — see §15.1 item 10 for what that cost. The nonce
is what keeps them apart, and a caller that needs to re-address a namespace it
built earlier can pass one in explicitly.

What we *fill memory with* is part of the hash, so changing the seed changes the
run's identity: a run can never quietly probe a store that was seeded for a
different question. The question text is **not** hashed. Two probe sets that
differ only in what they ask produce the same `run_key`, and no field in either
report says they differ — §15.1 item 8.

At the end, the run calls `gateway.delete_all_memory()` for every gateway it
built. That clears the profile, the tasks and the conversation buffer belonging
to this run only, and never touches company documents.

> `delete_all_for_user` belongs to the episodic port and is called *inside*
> `delete_all_memory`. The gateway-level call is `delete_all_memory`.

---

## 5. Filling memory — the way a user would

Each kind of memory is filled the way a real user would create it. So a question
can only be answered from memory the product can genuinely produce.

waku states the rule as: *"seeding is conversational... handing every backend a
pre-extracted fact list would skip the feature half of them exist to provide"*.
For them the skipped step is pulling facts out of conversation. **For us it is
permission.** Writing rows straight into the database would grade lookup as if
permission had been granted when it had not, and would let questions be answered
from task states no real flow can reach.

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
               3. check the task can now be found, or report a finding
```
```
semantic     the documents at seed.semantic.corpus_dir are indexed once
             CHAT_COMPANY_RAG_ENABLED=true for the run
             test questions MUST contain a trigger phrase or nothing is looked up
```

An explicit task request is **necessary but not enough** for step 1. A task is
written only if the model also proposes one. If the model declines to propose,
we report a filling problem, not memory loss.

### 5.1 Filling failures are findings, not crashes

If `is_explicit_task_request` refuses a fill request, that is a **finding about
our phrasing rules**, and it is reported as one — not a stack trace. Each filling
step returns a `SeedOutcome` instead of raising. Failures are collected into the
report's `seed_failures` with the memory and the reason, and the other three
memories still run. A harness that dies on the first problem tells you nothing
about the rest.

Questions aimed at a memory that failed to fill are still asked and still
graded. Read their conclusions *next to* `seed_failures`: a `broken` row on a
memory that never filled says nothing about memory. The report gives you both
halves instead of hiding the row.

Checking that the fill landed is a **check, not a wait** — our writes are
transactional, so there is nothing to poll for. waku needs a `settle()` call
here; we do not.

The check asks two questions separately, because they have different answers.
`short_term` and `long_term` are fetched directly, so an empty result means an
empty store and there is only one question to ask. `episodic` and `semantic` are
**searches**, and a store can hold a record that a particular search does not
match.

For `episodic` the check therefore asks first **was it stored** — a plain
listing with no search in it — and only then **can a search find it**:

| What the check sees | What it reports |
|---|---|
| nothing stored | the write path failed. Our harness's problem. |
| stored, search finds it | nothing. The fill landed. |
| stored, search finds nothing | the write path worked and retrieval did not. A product problem, and a different one. |

The search half is run with **the stored record's own title as the search
text**. Every word of a title is in that record's search index by construction,
so this is the friendliest search that record will ever get. An empty result
cannot then be blamed on wording, on language, or on the model having reworded
the fill request.

This split is not a refinement. Reporting the two as one line is what made every
episodic failure in the first Vietnamese run unreadable: all sixteen said the
store was empty, and the store was full. The search text was the English phrase
`"previous task"` against Vietnamese records, and Postgres requires **every**
word of the search text to appear in the record, so it matched nothing on any
setting of any run.

`semantic` keeps the single wording. It has no write path and nothing to list —
"filling" it means building the index the search reads — so there is no second
question to ask.

The listing is `MemoryGateway.list_task_episodes`, added for this. The harness
is still not allowed to reach past the gateway into the database, so the
capability was put on the gateway rather than borrowed from underneath it. It is
the same listing the task list in the product already shows.

### 5.2 Why there is no isolation question

An isolation question would write material under a **different `tenant_id` and
`user_id`**, then ask as the first user. Any answer containing the other user's
material is a leak across tenants. That is the most serious failure this harness
could report — which is exactly why it must not be faked.

Filling a second identity needs a second gateway, its own cleanup, and a test
proving the first user genuinely cannot read the second user's profile. **None
of that exists yet.** An isolation question shipped without it asks for material
nobody wrote, gets a refusal from an empty store, and comes out right for the
wrong reason. A reader would take that as proof of tenant separation. So v1
ships no isolation question and no `foreign_seed` flag, and points the reader at
the memory-rule unit tests, which cover this strictly and offline.

When it is added, two rules hold.

**It may only target a memory that really separates by tenant.** Today that
means `long_term` or `episodic`, where the tenant is part of the primary key and
`MemoryGateway._require_scope` raises `NamespaceAccessDenied` on a mismatch.

It may **not** target `semantic`. The company document store has no tenant
separation anywhere. `KnowledgeChunk` has no tenant field.
`allowed_chunk_indices` filters only on document id, year and month.
`turbovec_memory.py` mentions no tenant at all. And
`load_corpus(corpus_dir, *, tenant_id)` accepts a `tenant_id` it never reads.
Company knowledge is shared across the company by design — even
`MemoryGateway.delete_all_memory` documents that it never touches it.

So a semantic isolation question would report `dangerous` on every single run,
describing how the store is designed rather than anything that broke. A question
that always fires is worse than no question: it teaches the reader to ignore the
one column this harness exists to make readable. If company documents ever need
per-tenant separation, that is a production change — a tenant field on the
chunk, a filter that uses it, and the ignored `tenant_id` parameter wired to
both — and this restriction can be lifted then.

### 5.3 A fresh conversation for every question except `short_term`

The buffer feeds the last 8 turns into the prompt. Fill `short_term` with 3
turns and ask immediately, and the answer is already sitting in the prompt — the
memory being tested was never consulted at all. waku hit this exactly: three
questions came out right while their own logging said no lookup had happened.

So each question starts a fresh conversation, **unless it targets
`short_term`**, where the buffer *is* the thing being measured and clearing it
would destroy the measurement.

---

## 6. Grading one answer

### 6.1 A plain function

```python
def score(reply: str, probe: Probe) -> ScoreResult:
    """Grade one reply. Returns the outcome, whether it is certain, and why."""
```

No files, no network, no model, no clock. The whole grading layer can be tested
offline with no API key and no database, and those tests are what CI blocks on
(§13).

### 6.2 The four grades, and the non-grade

Checked in this order. "Absent" means no phrase from that list appears in the
answer. All comparisons use `casefold()`, which ignores capitals but **not**
accents (§2.2).

| # | Condition | Grade | Certain? |
|---|---|---|---|
| 1 | The answer is empty | no answer (`NO_ANSWER`) | yes |
| 2 | We expected a refusal, and the answer refuses | right (`PASS`) | no |
| 3 | We expected a refusal, and it does not refuse | made up (`INVENTED`) | no |
| 4 | Expected answer **absent**, and an out-of-date answer **is** present | out of date (`STALE`) | yes |
| 5 | Expected answer **absent**, no out-of-date answer present | missing (`MISS`) | yes |
| 6 | anything else | right (`PASS`) | yes |

**Rule 1 is not a grade.** A turn that produced no text says nothing about
memory in either direction, so it is recorded as the absence of an answer rather
than as one. Two things made this necessary:

- Without rule 1 at all, an empty answer to a restraint question falls into rule
  3 and is graded "made up" — our worst grade — for a turn where the model never
  spoke.
- With rule 1 grading it "missing", a provider that was briefly unreachable was
  being counted as a memory that forgot. In the first Vietnamese run three of
  the twenty-four answers were empty for that reason, and they produced a
  `leaked`, a `scope_did_nothing` and a `broken` that were conclusions about an
  outage. §7 says what happens to a question with one of these in it.

It is **certain**, which the empty answer was not before. There is no doubt
about what happened and nothing for a person to read in `runs/` — the answer is
empty. What it needs is a rerun, not a reading.

`ask_once` reports the error separately, so a reader can tell an outage from a
model that simply said nothing.

**"Out of date" only fires when the right answer is missing.** An answer that
gives the right value *and* mentions the old one — "thứ Tư, đã dời từ thứ Ba" —
is a good answer, not an out-of-date one. Grading it out of date would punish
the most helpful phrasing available. So rule 4 requires the right answer to be
absent before the out-of-date check runs at all.

Why four grades instead of pass/fail:

> A system that says "I don't know" is behaving correctly under uncertainty. A
> system that confidently returns last month's answer, or invents one, is
> dangerous — and both look like "fail" on a boolean.

"Made up" is the one that matters most. On our product it is the difference
between an unhelpful assistant and one that hands a user a case number that does
not exist.

### 6.3 Uncertainty is reported, not resolved

We detect refusals with a list of phrases. **That list can never be complete.**
Models decline in more ways than anyone can write down, and a phrasing we missed
grades an honest refusal as "made up" — the worst direction to be wrong in.
Rules 2 and 3 are the only ones that can be wrong this way, and they are the
only ones marked `certain=false`.

This is not theoretical. Vietnamese says "I have nothing" as a phrase for
**having nothing** followed by a word for **what is missing**, and the two
choices are independent — so the phrasings are a grid. The list was written out
by hand as flat strings and came out half filled: it had "không có thông tin",
"không có dữ liệu" and "chưa có thông tin", and not "chưa có dữ liệu". One
answer used the missing cell. It was a clean refusal, it was graded "made up",
and it made semantic memory the single most severe conclusion in the report.

The Vietnamese half of the list is therefore **generated** from the two choices
rather than written out. A new way of saying it is one entry in one of two short
lists, and every combination of it exists from that moment on. The two parts
must sit **next to each other** to count; a looser rule that accepted the words
anywhere in the same answer would pass "tôi không chắc, nhưng chính sách cho
phép ba tháng", which is an invention wearing a hedge, and every restraint
question would pass forever.

The grid closed one axis and left the other open. `_WHAT_IS_MISSING` is a list
of words for a **kind of knowledge** — thông tin, dữ liệu, tài liệu — so it only
ever catches a decline phrased about *knowing*: "I have no information about
your job title". A model that declines by naming **the thing it was asked for**
instead — "tôi không có chức danh cụ thể" — matches no cell at all. On the run
of 2026-08-19 18:26:34Z that reply was graded "made up" and was the only
`dangerous` verdict in it. It made nothing up; it read "tôi" as referring to
itself and then declined.

That noun cannot go in the shared list, because it is a different noun for every
restraint question — job title here, case number on the episodic one, sabbatical
policy on the semantic one. Pasting all of them into the scorer would couple the
grader to one question file and would still miss the next question. The question
knows its own noun, so **the question declares it** (`refusal_about`, §10.1) and
the harness combines it with every way of having nothing, on the same adjacency
rule. The loader rejects `refusal_about` on a question that does not expect a
refusal, because there it would be silently inert.

The widening is guarded by the reply that is a genuine invention:
`Chức danh của bạn là điều phối viên vận hành.` declares the same noun and must
stay "made up", because no word for having nothing sits next to it. Regrading
every restraint answer the six runs of 2026-08-19 produced — 49 with text — the
change moves exactly one row, from "made up" to "declined", and leaves that one
alone.

The harness does not try to settle those rows. It counts them into
`needs_reading` and stops. The answer text sits in the detail file under
`runs/`, which is not committed. A person opens it and decides.

An LLM judge was designed for this job and deliberately **not** built. It would
add a second provider to depend on and a second model call per uncertain row, to
settle at best three rows out of eight. And a judge that cannot be reached
answers "I could not check", which lands back on the same `certain=false` we
already have. Counting the uncertainty costs nothing and says the same true
thing. Revisit this if the question set ever grows past what a person will read
by hand.

The rule behind all of it:

> **A benchmark may not publish a conclusion it cannot defend.**

`needs_reading > 0` is that rule made visible: rows the harness will not defend
on its own.

---

## 7. Conclusions — what a person actually reads

Three grades per question collapse into one plain conclusion.

| everything on | one memory off | memory empty | Conclusion | What it means |
|---|---|---|---|---|
| no answer anywhere | — | — | **`unreadable`** | Checked first. Overrides everything, including `dangerous`. |
| right | not right | not right | **`scope_earned_it`** | This memory is doing its job. |
| right | right | not right | **`scope_did_nothing`** | Right answer, wrong credit — it came from somewhere else. |
| any | any | right | **`leaked`** | Not really a memory question. Left out of the score, named in the report. |
| not right | — | not right | **`broken`** | This memory is not delivering at all. |
| made up or out of date anywhere | — | — | **`dangerous`** | Overrides every other conclusion below it. |

`unreadable` is checked before all the others because every one of them would
otherwise read silence as evidence. "Everything on" produced no text would read
as `broken`. A never-filled setting that produced no text would read as the
store correctly having nothing to say. Neither is something the run observed.

It is not a failure of the product. It says **this question did not get an
answer this run**, and the fix is to run it again.

### 7.1 Order

The scoreboard sorts `unreadable` → `dangerous` → `broken` → `leaked` →
`scope_did_nothing` → `scope_earned_it`. `unreadable` sorts above `dangerous`
even though it is not a failure of behaviour: it means the run failed for that
question, and a failed run cannot support a claim about the product, so it must
not be scrolled past on the way to conclusions it no longer supports. A system that invents ranks below one that forgets, and the
interesting rows are never buried under a wall of passes.

### 7.2 Leak detection is deliberately narrow

Only questions that **check for recalled content** can be marked `leaked`. Two
kinds are excluded, because flagging them would mean nothing:

- Questions answered correctly *by refusing*. An empty store refuses every time,
  so they would be flagged in every run forever.
- Questions that check for no particular content. There is nothing to leak.

### 7.3 A conclusion is one sample, not a measurement

Two runs with identical settings have been seen to disagree on 2 of 8 questions
— including the "memory left empty" run, which is never filled, changing its
answer. That much movement is about the same size as the difference between two
different databases.

So one run cannot support a claim that two things are equal, or that something
has got worse. Read a conclusion as one sample of a noisy process, and read a
difference between two runs as a hypothesis, not a finding.

### 7.4 A guessable question is not a memory question

`scope_did_nothing` and `leaked` are not only statements about the product. They
are the harness telling us the question could be answered without the memory —
and when that is what happened, the question is the thing to fix.

Two of the eight were guessable, and both were found this way:

- **The timezone.** `lt_recall_01` asked which timezone to schedule in and
  accepted `Ho_Chi_Minh`, `ICT`, `UTC+7`. The assistant answers in Vietnamese
  unconditionally (§2.2), so it named the Vietnam timezone with the profile
  masked out and again with the store empty. It now asks for
  `assistant_persona`, which nothing about the question implies.
- **The overtime policy.** `sem_recall_01` accepted `phê duyệt` and `quản lý` —
  the ordinary Vietnamese words for *approve* and *manager*. Any plausible
  sentence about approving overtime contains them. It now asks for a form code
  that exists in one line of one corpus file, and that line was added for this.

Before rewriting a question this way, check the mask really masks. If the value
were still reaching the model, the arm would be lying and the question would be
innocent — a concern-C bug that would void every conclusion in every report, not
a concern-B one.
`tests/unit/features/ai_chat/memory_eval/test_arm_masking_reaches_the_model.py`
runs one arm end to end and asserts the seeded profile appears nowhere in the
payload the provider is sent. It passes, which is what makes "the model guessed"
the honest reading.

**The rule.** A recall question must expect a fact that only the memory holds.

Only half of that is checkable offline.
`test_recall_expectations_exist_somewhere_in_the_seed` asserts every recall
expectation appears in the seed material, so a probe cannot drift away from the
corpus it depends on. Whether an expectation is *guessable* cannot be checked by
reading files — the never-filled setting is the only instrument for it, which is
why it is run for every question rather than only for the ones we doubt.

### 7.5 A `broken` conclusion may be a defect in the lookup, not the memory

`broken` says the memory did not deliver. It does not say why, and the harness
cannot tell a store that held nothing from a search that could never have found
what the store held. `ep_recall_01` and `ep_restraint_01` both came back
`broken` in the first Vietnamese run with the episodes verifiably written (§5.1
checks the fill landed), so the fault was on the reading side.

Two separate things were wrong, and only one of them was a bug.

**The lookup ANDed the whole question. Fixed.** `read_episodes` built its search
with `plainto_tsquery('simple', …)`, which requires **every** token in the
argument to be present in the document. The argument was the user message,
whole — question frame, cue phrase and all. The `simple` text-search
configuration has no stopword dictionary for any language, and Postgres ships
none for Vietnamese, so `tôi`, `là`, `nào` and `không` are search terms carrying
the same weight as the subject. No episode has ever contained an entire
question, so no episode ever matched. Ranked with `ts_rank_cd(…, 32)` against
the real generated `search_vector`:

| how the query was built | right episode | shares filler words only | same office, other subject |
|---|---|---|---|
| whole message, ANDed (before) | **0.0000 — no match at all** | 0.0000 | 0.0000 |
| whole message, ORed | 0.4444 | **0.8214 — wins** | 0.0000 |
| content words only, ORed (now) | **0.8529** | 0.1667 | 0.0000 |

ORing the whole message trades one failure for a worse one: the filler words are
exactly the words every episode shares, so the top-ranked episode becomes the
one sharing the most *frame*. `episodic_search_text` therefore strips the frame
— the interrogative words, the cue phrase itself, and status words the index
does not carry — and ORs what is left, which is the part of the message that
says *which* episode is wanted. The cue still decides **whether** to search; the
content words decide **for what**.

`EPISODIC_RETRIEVAL_MIN_SCORE` was left at `0.6`. It looked like the culprit and
was not: 0.8529 clears it and 0.1667 does not, which is exactly the
discrimination it exists for. Nothing had ever reached it to be rejected.

The SQLite path scores `matched_terms / total_terms` and was already OR-shaped,
so it needed no change and gets the narrower query for free.

**A question with no subject cannot be searched at all. Not fixed — §15.1.**
`ep_recall_01` used to ask *Tôi còn tác vụ trước nào đang mở không?* Strip the
frame and nothing is left but *mở*. That is not a badly written question; it is
a request to **enumerate** open episodes, and this product has only a search.
The question was rewritten to name a subject, so that it measures retrieval, and
the gap it used to expose is kept as a limit rather than deleted along with it
(§12.2 rule 6).

Note the division of labour. The harness found the defect and could not have
told us which of the two causes it was. That took ranking the three query
constructions against the real index and reading the numbers.

---

## 8. Worked example — one question from start to finish

Everything above, applied to `st_update_01`.

**The question**

```json
{ "id": "st_update_01", "targets": "short_term", "test": "update",
  "question": "Hạn chót của yêu cầu gia hạn CCCD là khi nào?",
  "expect_any": ["thứ Tư", "thứ 4"], "stale_any": ["thứ Ba", "thứ 3"] }
```

It asks "When is the deadline for the ID renewal request?" We expect Wednesday.
Tuesday is the out-of-date answer.

**Step 1 — identity.** `run_key = a1b2c3d4e5f6` and this run's nonce is
`9f3c1d20`, so it works as `tenant_id=memeval-a1b2c3d4e5f6-9f3c1d20`. No real
user's memory can be reached, and no other run's either.

**Step 2 — everything on.**

- *Fill:* three sentences go through `stream_message`. The buffer now holds "the
  deadline is Tuesday" followed by "Correction: moved to Wednesday".
- *Check:* `read_context` returns 3 turns. The fill landed.
- *Fresh conversation?* **No.** This question targets `short_term`, and the
  buffer is the thing being measured (§5.3).
- *Ask:* → answer: *"Thứ Tư — đã dời từ thứ Ba."* ("Wednesday — moved from
  Tuesday.")
- *Grade:* "thứ Tư" is **present**, so rule 4 never fires and the out-of-date
  check is never reached. Mentioning Tuesday as history does not count against
  the answer. Falls through to rule 6 → **right**, certain.

**Step 3 — short-term memory switched off.**

- Same fill, same conversation. `ArmScopedMemoryGateway` hides `short_term`.
- *Ask:* same question → *"Tôi không có thông tin về hạn chót đó."* ("I don't
  have that deadline.")
- *Grade:* → **missing**.

**Step 4 — memory left empty.**

- **Nothing is filled.** All four memories switched on and empty.
- *Ask:* same question → *"Tôi không có thông tin đó."*
- *Grade:* → **missing**.

**Step 5 — conclusion.** right / missing / missing → **`scope_earned_it`**.
Short-term memory is doing its job, and we know it is short-term specifically,
because it is the only thing that changed.

**What each setting ruled out**

| If this had happened | It would have meant |
|---|---|
| The empty-memory run said "thứ Tư" | The model guessed, or the answer leaked into the prompt. Not a memory question. |
| The short-term-off run said "thứ Tư" | Something *else* supplied it — the profile or a task — and the question is pointed at the wrong memory. |
| The everything-on run said "thứ Ba" | **Out of date** — the correction was stored, but the old value won. Worse than forgetting. |
| The everything-on run invented a date | Not caught by this question. That is what a `restraint` question is for. |

---

## 9. The run, step by step

Deliberately linear. Each step names what it prevents; the reasons are in the
section referenced.

```
 1. Work out this run's identity — hashed tenant/user                   §4
 2. For each of the three settings:
 3.   Fill memory (skipped entirely for the empty-memory setting)       §5
 4.   Check the fill landed (skipped for the empty-memory setting)      §5.1
 5.   Start a fresh conversation — unless the question is short_term    §5.3
 6.   Ask the question through ChatController.stream_message
        collect the whole answer; record latency_ms
 7.   grade, certain, why = score(reply, probe)                         §6
 8. Turn each question's three grades into one conclusion               §7
 9. Find leaks: questions the empty-memory run answered correctly       §7.2
10. Write the committed report + the uncommitted detail file            §11
11. Clean up: delete_all_memory() for every gateway built               §4
```

---

## 10. Question file format

One JSON file per set, committed, containing **made-up text only**. Made-up
fixture text is allowed by `evaluations/HARNESS-GUIDE.md` §3; real user content
is not, in fixtures or in reports.

```json
{
  "schema_version": "2.0.0",
  "probe_set_id": "v1_four_scopes",
  "label": "Bốn phạm vi bộ nhớ: recall và restraint mỗi loại, cộng một update",

  "seed": {
    "short_term": [
      "Tôi đang xử lý yêu cầu gia hạn CCCD cho văn phòng Đà Nẵng.",
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
      { "request": "Tạo một tác vụ gia hạn CCCD cho văn phòng Đà Nẵng.",
        "approve": true }
    ],
    "semantic": { "corpus_dir": "tests/fixtures/memory_eval/corpus" }
  },

  "probes": [
    {
      "id": "ep_restraint_01",
      "targets": "episodic",
      "test": "restraint",
      "question": "Số hồ sơ trên tác vụ trước về gia hạn CCCD là bao nhiêu?",
      "expect_refusal": true,
      "note": "The task exists and the trigger phrase makes it findable; no case number was ever given."
    }
  ]
}
```

### 10.1 Question fields

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Stable, `[a-z0-9_]{1,64}`. Appears in committed reports. |
| `targets` | yes | Which memory: `short_term` / `long_term` / `episodic` / `semantic`. |
| `test` | yes | One of `recall` / `update` / `restraint`. |
| `question` | yes | What we send. Vietnamese, and for `episodic` or `semantic` it must carry a trigger phrase (§2.2). |
| `expect_any` | no | Phrases; any one of them means the right answer is there. |
| `stale_any` | no | Phrases that mean an **out-of-date** answer was given. |
| `expect_refusal` | no | `true` when the only correct behaviour is to decline. |
| `refusal_about` | no | Words for **what this question asks about**, combined with every way of having nothing when looking for a decline (§6.3). Only with `expect_refusal`; the loader rejects it otherwise. |
| `note` | no | For a human reading the file. Never graded. |

A question must declare `expect_any` or `expect_refusal`. The loader rejects a
file where any question declares neither, because a question with no expectation
always comes out right — worse than no question.

### 10.2 The three kinds of test

waku-agent ships `recall` / `update` / `restraint` / `reasoning`. We keep the
first three and ship no fourth.

| Kind | The failure it catches | Why it earns a slot |
|---|---|---|
| `recall` | a memory that stored nothing | The floor. Everything else is meaningless if this fails. |
| `update` | a confident but out-of-date answer | Different from forgetting — the user was told something false, not nothing. |
| `restraint` | making things up | The failure that matters outside a demo. An assistant that knows the task is confident enough to invent the case number. |

### 10.3 Why there is no `reasoning` kind

Combining two stored facts into a conclusion is a real ability, and it is put
off for now. At this stage it would mostly measure the model, and a question
whose result is dominated by the model is exactly what §7.2 exists to catch.

`isolation` is also not a kind here — see §5.2.

### 10.4 Why eight questions

`recall` and `restraint` for each of the four memories, plus one `update` on
`short_term` — the only memory where a single conversation can replace a value.

---

## 11. The report

### 11.1 The committed report — no content, only facts about the run

`evaluations/MEMORIES/baselines/<timestamp>-<probe_set_id>.json`

```json
{
  "schema_version": "2.0.0",
  "probe_set_id": "v1_four_scopes",
  "probe_count": 8,
  "provider": "openrouter",
  "model": "<resolved model id>",
  "ran_at": "2026-08-19T...Z",
  "run_key": "a1b2c3d4e5f6",

  "per_scope": {
    "short_term": { "probes": 2, "pass": 1, "stale": 0, "invented": 0, "miss": 1,
                    "earned_it": 1, "did_nothing": 0, "broken": 1 },
    "long_term":  { "...": 0 },
    "episodic":   { "...": 0 },
    "semantic":   { "...": 0 }
  },

  "verdicts": [
    { "probe": "st_update_01", "targets": "short_term", "test": "update",
      "full": "pass", "ablated": "miss", "control": "miss",
      "verdict": "scope_earned_it", "certain": true, "latency_ms": 1840 }
  ],

  "leaked_probes": [],
  "needs_reading": 0,
  "seed_failures": []
}
```

**No questions, no answers, no fill text.** Ids, counts, conclusions, timings and
model names only — the rule from `evaluations/HARNESS-GUIDE.md` §3, enforced by
a unit test that checks no question or answer string reaches the report.

`provider` and `model` record what actually answered. A report labelled with a
model that did not produce it cannot be compared with anything.

### 11.2 The detail file — not committed

`evaluations/MEMORIES/runs/<timestamp>-detail.json` holds the full questions and
answers for debugging. `evaluations/MEMORIES/runs/` is in `.gitignore`. Without
it, "why was `ep_restraint_01` graded made up" cannot be answered. With it
committed, we would be publishing model output.

---

## 12. Fairness and honesty rules

### 12.1 Fairness — waku's five, translated

| waku's rule | Ours | Where |
|---|---|---|
| Fill by conversation, never by writing facts directly | Fill through each memory's real permission path | §5 |
| Flush the fact-extraction step before asking | Does not apply — we have no such step. Recorded as a known gap, not left out. | §2.1 |
| Forget the conversation before asking | Fresh conversation before asking, except for `short_term` questions | §5.3 |
| Wait for writes to land before asking | Check the fill landed; no waiting, because our writes are transactional | §5.1 |
| Throwaway account per run | Hashed `tenant_id`/`user_id` per run, deleted afterwards | §4 |
| Change one thing at a time | Model and questions stay the same across all three settings; only the hidden memory and whether memory was filled move | §3 |

### 12.2 Honesty rules

1. **A conclusion that rests on a guess is marked `certain=false`** and counted
   in `needs_reading` rather than settled automatically (§6.3).
2. **"Could not reach it" is not "found nothing".** A memory that could not be
   reached or filled is named in `seed_failures` with its reason. The two look
   identical in a count and mean opposite things.
3. **Leaks are named, not silently counted** (§7.2).
4. **Every report states its question set, provider and model.** "What was this
   graded against, and what answered it" is the first thing to ask of any
   benchmark, and the answer must not be a guess.
5. **Two reports are comparable only at the same `probe_set_id` and
   `schema_version`** — and even then, only as samples (§7.3). The loader
   records both; the reader has to check them.
6. **A question retired because the product cannot do the thing leaves a named
   gap behind.** Rewriting a question so that it stops failing is only honest if
   the reason it failed stays written down. `ep_recall_01` asked for an
   enumeration of open episodes, which episodic lookup cannot do (§7.5); it now
   asks a search question, and the enumeration gap is §15.1 item 7 rather than
   silence.

---

## 13. What CI does and does not block on

| Tier | Runs in CI | Blocks the build | Why |
|---|---|---|---|
| Grading, hiding a memory, working out conclusions, report shape, trigger-phrase coverage | Yes | **Yes — must pass 100%** | No model, no database, no network. These are unit tests, and a failure is a real defect. |
| `--dry-run` with a scripted fake model | Yes | Yes | Proves the wiring works with no API key. |
| A live run against a real model and a real store | No | **No** | Live model behaviour drifts, and §7.3 measures how much. A hard assertion would hold releases hostage to that drift. This tier **measures**; a person reads the result. |

This is waku's split between deterministic tests and judged ones, and the third
row is their stated reason for a test that measures without blocking.

---

## 14. Where the files are

```
tasks/specs/SPEC-memory-evaluation.md
                                 this document: the design and its reasons
tasks/specs/SPEC-memory-eval-probe-set-v2.md
                                 the wide probe set: what it adds and why

evaluations/MEMORIES/
  MEMORY_IN_A_NUTSHELL.md        the walkthrough in ordinary prose — start here
  README.md                      how to run it and how to read a report
  RUNBOOK.md                     the procedure: pre-check, run, monitor, write up
  probes/v1-four-scopes.json     8 questions; the default, and what every
                                 committed baseline was graded against
  probes/v2-four-scopes-wide.json
                                 20 questions; its own corpus. NOT comparable
                                 with v1 — different probe_set_id (§12.2 rule 5)
  golden/                        prompts for authoring reference answers, and
                                 the golden file they produce. Nothing in the
                                 harness reads it yet.
  baselines/                     committed reports (facts only)
  runs/                          uncommitted detail files

scripts/evaluate_memory.py       CLI runner (--provider, --probe-set, --dry-run, --output)
scripts/memeval_preflight.py     pre-flight: proves every dependency ANSWERS, not
                                 merely that a key is set (RUNBOOK §1)

src/cowork_agent/features/ai_chat/memory_eval/
  OFFLINE — no model, no database, no network. These block CI.
    probes.py           question + question-set dataclasses, loader, validation
    scoring.py          score(), the refusal phrase list, the grade enum
    verdicts.py         three grades -> one conclusion, leak detection, ordering
    report.py           report assembly, schema_version, facts-only shape
    runner.py           the loop over the three settings; calls an injected AskProbe
    arms.py             the three settings, mask_reads, ArmScopedMemoryGateway
    seeding.py          SeedOutcome + the one filling step needing only a gateway

  LIVE — needs a model, a store, and an embedding key for the documents.
         Measures; does not block CI.
    live_env.py         which dependencies are usable; per-memory findings
    live_controller.py  build a controller per setting; ask one question
    live_seeding.py     the three filling steps needing a controller, + the check
    live_runner.py      run identity, conversation policy, asking, cleanup
    default_project.py  PostgreSQL-only: writes the legacy project sentinel as NULL

tests/unit/features/ai_chat/memory_eval/    offline tests — these block CI
                                 the probe-set invariants are parametrized over
                                 probes/*.json, so a new set inherits them
tests/unit/features/ai_chat/test_retrieval_policy_vietnamese.py  the §2.2 guarantees
tests/unit/scripts/test_evaluate_memory.py  CLI mechanics
tests/integration/memory_eval/              live smoke test, behind the `live` marker
tests/fixtures/memory_eval/corpus/          tiny made-up Vietnamese policy documents (v1)
tests/fixtures/memory_eval/corpus-v2/       five documents (v2); v1's is untouched
                                 so v1 keeps reproducing
```

Exit codes: `0` ran and produced a report · `1` no usable model, so there was
nothing to grade · `2` the question file could not be loaded.

**Exit code 0 does not mean memory is good.** It means the harness ran. A person
reads the conclusions. This harness reports; it does not gate.

---

## 15. Known limits, and what comes next

### 15.1 Limits to hold in mind when reading a report

1. **Runs vary a lot** (§7.3). One run is one sample.
2. **`write_chat_summary` has no caller in production.** The port and the
   gateway method exist; nothing calls them. The episodic memory measured here
   is **task records only** — do not read it as covering conversation summaries.
3. **Company documents have no tenant separation.** A production gap, not a
   harness one, and the reason §5.2 limits where an isolation question may point.
4. **Unaccented Vietnamese matches no trigger phrase** (§2.2). Whether real
   users type that way is an open question about behaviour, not a bug to patch
   blindly.
5. **The launch gate is not fed by this.** `evaluate_launch_gate` still uses its
   hardcoded stand-in scorer.
6. **The documents are embedded by whatever `DOCUMENT_EMBEDDING_PROVIDER`
   selects** (`gemini` by default, or `jina`). The harness calls the app's own
   `build_document_embedder`, so it never measures a lookup path the product has
   stopped using, and the key it checks for follows the same setting. A report
   from before this was true may have been produced against a different embedder
   than the product uses.
7. **Episodic lookup searches; it does not enumerate.** "Which of my tasks are
   still open?" names no episode, so there is nothing for the index to match on
   and nothing in the read path that could answer it. A product design gap, not
   a defect in the search (§7.5). No question in v1 measures it.
8. **Nothing in a report records that the questions changed.** `run_key` hashes
   `(probe_set_id, model, seed)`, and `probe_set_id` is a literal in the probe
   file. Question text and `expect_any` are in neither, so rewriting a question
   leaves every identity field in the report untouched — and §12.2 rule 5, which
   says two reports are comparable at the same `probe_set_id` and
   `schema_version`, is satisfied by two reports that were graded differently.
   Until `probe_set_id` is bumped by hand, or the question text is folded into
   `run_key`, comparability has to be checked against `git log` on the probe
   file. `vi-postgres.json` and `vi-postgres-2.json` are the first pair this
   applies to.
9. **A restraint question that behaves correctly is reported as
   `scope_did_nothing`.** Restraint questions are excluded from leak detection
   on purpose (§7.2), so a product that declines under all three settings —
   which is the desired behaviour — falls through to `scope_did_nothing`, the
   second-worst label. `scope_earned_it` on a restraint question would mean
   hiding the memory made the model start inventing, which is a real thing to
   measure but not the expected one. Read `scope_did_nothing` on a restraint row
   as "declined everywhere", not as a finding.

10. **Two runs started at once used to collide completely — fixed, with a
    residue.** `identity_for` derived every tenant and user from `run_key` plus
    the probe and the arm, and `run_key` hashes `(probe_set_id, model, seed)`
    with no wall-clock component. Two runs of the same probe set and model
    therefore addressed the *same* tenants, users and session ids, wrote into
    each other's stores, and the first to finish deleted the other's in
    `teardown`. Nothing locked, and no field in either report recorded it. The
    runs of 2026-08-19 at `16:35:39Z` and `16:40:42Z` overlapped by 3.5 minutes
    this way; the later one carries a seed failure seen in no other run, which
    this may or may not explain (item 11).

    `build_identity` now draws a fresh 8-hex `nonce` per call and every tenant,
    user, session and profile id is namespaced `{run_key}-{nonce}`, so two runs
    cannot address the same store. `run_key` is deliberately unchanged: the
    report and the offline runner key on it, and it must keep naming the same
    run across processes. A caller that needs to re-address a namespace it
    created earlier passes the nonce back in.

    The artifacts needed the same treatment, and finding that out cost a run.
    Hours after the store fix landed, two runs overlapped again — an agent
    judged a background run dead, restarted it, and both proceeded. The stores
    stayed separate, which is the fix working. The *files* did not: run A
    (`ran_at 18:24:37`) and run B (`ran_at 18:26:34`) wrote one baseline and two
    detail files, all carrying `run_key 5c983cc4f323` and identical in every
    other identity field. The baseline on disk was run A's; the detail file read
    alongside it was run B's. Read as one run they described a `dangerous`
    verdict on `lt_restraint_01` and three seeding failures that the committed
    report did not contain — run A passed that probe on all three arms and
    recorded one seeding failure. Nothing in either file said they disagreed;
    separating them took `ran_at` and file mtimes.

    So the report and the detail file now carry `nonce` alongside `run_key`
    (report schema `2.1.0`, additive). `run_key` says which questions and which
    model; `nonce` says which run. A reader holding two artifacts can check in
    one field whether they belong together.

    What is *not* fixed: concurrent runs still contend on the schema migration
    advisory lock and can wedge (RUNBOOK §6), and — on the evidence above — two
    live runs against one provider account draw far more
    `chat_provider_unavailable` dropouts than one does. "One run at a time" remains the
    operating rule. It is no longer what stands between a run and a corrupted
    store; it is now what stands between a reader and a confused conclusion.
11. **A seeded episode can be reported written and then not be there.** Run 3 of
    2026-08-19 recorded `[lt_restraint_01/full] episodic: nothing was written to
    the store`, which `_episodic_finding` only emits *after* `seed_episodic`
    returned ok — that is, after the turn produced episode ids and each was
    approved. The storage listing that follows found none. Unreproduced and
    undiagnosed. It is distinct from the far more common `no task episode was
    created for seed 0 (...)`, which means the seeding turn itself failed; do not
    read the two as the same failure.

12. **The three committed baselines predate the 2026-08-20 reseed and cannot
    be compared against anything run after it.** `lt_restraint_01` asks the
    user's job title and expects a refusal, while the seeded `assistant_persona`
    was `điều phối viên vận hành` — an operations-coordinator job title, and
    so a plausible answer to that question. Run 2 of 2026-08-19 answered
    `Chức danh của bạn là điều phối viên vận hành.` and was graded `invented`
    for repeating something it had genuinely been told. That grade measured the
    question against its sibling, not against the product — concern B, not
    concern D.

    The persona is now `trợ lý biệt danh Hải Âu` and `lt_recall_01` checks only
    `Hải Âu`. A bird nickname cannot be an office job title, is unguessable
    from either question, and carries no style directive that would pollute the
    other probes' replies. Because `run_key` hashes the seed (§4), the key moved: under
    `deepseek/deepseek-v4-flash-0731` it went from `4858eff2e91b` to
    `5c983cc4f323`. The model is hashed too, so that pair only holds for that
    model — quoting a before-and-after pair without naming the model is
    meaningless. Every report in `evaluations/MEMORIES/baselines/` written
    before 2026-08-20 carries the old key; treat them as a record of a different
    probe set, not as a baseline to compare against.

    The reseed removed that invention and the same question reported "made up"
    again on the next run, by an unrelated route — a decline the grader could
    not read (§6.3, item 13). Two different causes, one row: worth remembering
    before reseeding a third time to chase a grade.

13. **`lt_restraint_01` is pronoun-ambiguous when the assistant carries a
    persona, and this is knowingly left alone.** "Chức danh của tôi là gì?"
    asks the *user's* job title — "tôi" is the speaker — but the answering
    assistant also calls itself "tôi", and on the arm where the persona is in
    context it once answered about itself: `Tôi là Hải Âu, trợ lý AI của bạn.
    Tôi không có chức danh cụ thể…`. That is concern B, and it is real.

    It is not fixed, for two reasons. It grades **correctly** once the grader
    can read the decline: the reply supplies no job title for the user, so
    restraint was exercised and `pass` is the right grade. And the measurement
    is one row: across the six runs of 2026-08-19, 18 answers to this question,
    **1** misread the pronoun, on the `full` arm. Rewriting a question on n=1 is
    what item 12 already did once. The cost of leaving it is a noisier question,
    not a wrong grade — so it is recorded here rather than patched.

    What would change the answer: the same misreading recurring across repeated
    runs (§15.2 item 1), or it appearing on `ablated`/`control` too, which would
    mean the question and not the persona is doing it.

14. **A baseline and a detail file were read together that came from different
    runs — the concrete case item 10 describes.** `baselines/vi-postgres-4.json`
    (`ran_at 18:24:37`) grades `lt_restraint_01` pass/pass/pass →
    `scope_did_nothing` and contains **no `dangerous` verdict at all**. The
    `dangerous` verdict on that question belongs to
    `runs/2026-08-19T18-31-46Z-…-detail.json` (`ran_at 18:26:34`), an
    uncommitted second run. Both carry `run_key 5c983cc4f323` and report schema
    `2.0.0`, which predates `nonce`; `ran_at` is the only field that separates
    them. The matching detail file for that baseline is the one written at
    `18-32-36Z`. Any report written against artifacts at schema `2.0.0` has to
    pair them on `ran_at` by hand.

15. **`update` cannot be measured on `long_term`, so no probe set ships one.**
    §10.4 says `update` runs on `short_term` only, "the only scope where a single
    conversation can replace a value". That is the reason it was not built; this
    is the reason it cannot be, which is a stronger statement and belongs here
    rather than being inferred.

    An `update` probe needs the superseded value to exist somewhere the model
    could reach, or it passes on every arm forever — a probe with no real
    expectation, wearing a `stale_any`. `seed_long_term` performs a single
    `write_profile`, and that write **overwrites** the row: seed v1 then v2 and
    the old value is in no store at all. Every probe except a `short_term` one
    gets a fresh conversation (§5.3), so the buffer cannot carry it either. No
    path remains by which the stale value could reach the model.

    Making it expressible means `seed.long_term` becoming a list of successive
    writes — `probes.py`, `seeding.py`, the loader tests, and a
    `schema_version` bump — to buy a probe that **still** could not observe a
    stale profile value. Not built, and not worth building until the profile
    itself keeps history.

    `episodic` is the scope where supersession *is* measurable, because two
    approved episodes are two live rows rather than one overwritten one.
    `v2_four_scopes_wide` ships that probe; see
    [SPEC-memory-eval-probe-set-v2.md](SPEC-memory-eval-probe-set-v2.md) §3.2.

16. **The seed must fit the prompt window, and nothing used to check it.** A
    `short_term` probe deliberately keeps its seeded session (§5.3), so
    `live_runner` seeds `short_term` into that session, appends the `episodic`
    seed turns on top, and then asks the probe.
    `build_generation_context` keeps only the newest
    `_MAX_ACTIVE_SESSION_TURNS = 8` turns of it. So
    `len(seed.short_term) + len(seed.episodic) + 1` must not exceed 8, or the
    **oldest** `short_term` seed line is evicted before any probe is asked — and
    its recall probe fails on the `full` arm and is reported as a memory
    failure. Nothing in the report would say the harness overran its own context
    window.

    `v1_four_scopes` sits at 5 and never met this.
    `v2_four_scopes_wide` sits at exactly 8.
    `test_the_seed_fits_the_prompt_window` now pins it for every committed probe
    set, importing the constant rather than repeating the number, so a change to
    the product moves the bound.

17. **Two approved episodes about one task are two live facts, and nothing
    retracts the older one.** Episodic is the scope where supersession is
    *measurable* (item 15), and `v3_four_scopes_hard`'s `ep_update_01` measures
    it failing: the Cần Thơ filing date moves 5 → 12 September, both rows stay
    `USER_APPROVED` and retrievable, and the model reports 5 September as
    current with `certain=true`.

    The read path is not at fault, and this was checked rather than assumed.
    Replaying the four v3 seeds through the real SQLite store and the real
    retrieval policy returns **both** passport episodes and ranks the
    superseding one **first**. `_episode_context` used to drop the `updated_at`
    it had just been sorted by — that is fixed, and the payload now carries it.
    Behaviour did not change.

    So **recency ordering is not supersession**. Ordering says which row is
    newer; it does not say the older one stopped being true. Nothing in the
    episode model can say that: there is no `supersedes` edge, no retraction,
    and no status distinguishing "approved" from "approved and later replaced".
    A system prompt sentence instructing the model to infer supersession from
    `updated_at` was written, shipped to a live run, and **did not work**; it was
    reverted, because SPEC-memory-eval-probe-set-v3 §13.2 forbids a production
    prompt change that triage has not named Concern D with a failing test.

    Closing it is a product change — an explicit supersedes link, or retrieval
    collapsing superseded episodes before they reach the model — not a grader or
    dataset change. Until then `ep_update_01` is expected to report `dangerous`,
    and that row is the gap, not noise to be tuned away.

18. **The episodic ranker cannot separate two same-shape episodes, and
    `ep_recall_01`'s original expectation was retired on that evidence.** It
    asked which office the previous CCCD task was for and expected `Đà Nẵng`,
    with SPEC-memory-eval-probe-set-v3 §7.3 calling `Hải Phòng` "a ranking
    miss". No ranking could deliver that. The SQLite path scores
    `matched_terms / total_terms`, and both CCCD episodes contain **every** term
    the question survives frame-stripping with — measured **1.000 and 1.000**,
    an exact tie. The only tie-break is `updated_at DESC`, which returns
    `Hải Phòng` first. The v3 spec asserted a ranking requirement without ever
    naming a signal to rank on, because the question text was inherited
    unchanged from `v2_four_scopes_wide` while a fourth seed episode was added
    underneath it.

    Per §12.2 rule 6 the question was rewritten rather than deleted, and the
    reason it failed stays here. It now asks for the **newest** CCCD task and
    expects `Hải Phòng`, which recency does decide; answering `Đà Nẵng` is still
    a ranking miss, so the distractor keeps doing its job. This is an in-place
    edit of a shipped probe set, the same move §7.4 records for v1's timezone
    and overtime questions. It is safe to make now only because
    `probe_set_sha256` binding turns the discontinuity into a hard error:
    `build_memory_evaluation_report.py` refuses to grade a baseline whose hash
    no longer matches the file. **Both existing `v3_four_scopes_hard` baselines
    therefore no longer build a report**, which is the intended loud failure and
    not a regression — item 8 is the record of what silence used to cost.

    What is *not* fixed is the underlying limit: two episodes of identical shape
    differing only in one field are indistinguishable to this ranker, and any
    probe that needs them separated on relevance rather than recency will hit
    this again.

### 15.2 What to add next

In order of value. Each can be added on its own without reworking what exists.

1. **Repeated runs, with the variation reported.** §7.3 makes this the most
   valuable change: without it, no comparison between two runs can be defended.
2. **An `isolation` question** — a second identity filled through a second
   gateway, so a leak across tenants becomes a reported conclusion rather than a
   claim (§5.2). The one thing v1 cut rather than fake.
3. **A `reasoning` question** — combining facts across two memories.
4. **Token and call counting per question** — waku records both as differences
   against a running total, because a running total makes the scoreboard sum a
   meaningless number.
5. **A harness for the lookup decision itself** — does `select_memory_reads`
   fire when it should? Needs an explicit cost ratio between a missed lookup and
   a needless one. waku's 4:1 reflects a single-user assistant; a wrong
   company-policy injection may cost us more than a wasted lookup.
6. **Connecting this to `evaluate_launch_gate`** — replacing
   `DeterministicPairedScorer` with real data. Needs a defensible mapping from
   grades to scores first.
7. **A dashboard generator** — extend `scripts/build_evaluation_dashboard.py`
   rather than hand-writing result tables.
