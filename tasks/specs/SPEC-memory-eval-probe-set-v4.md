# Memory Evaluation — Probe Set v4 (`v4_four_scopes_volume`)

**Status:** Proposed.
**Area:** `evaluations/MEMORIES/probes/`, `tests/fixtures/memory_eval/`
**Parent:** [SPEC-memory-evaluation.md](SPEC-memory-evaluation.md) — the harness.
**Harness v3:** [SPEC-memory-eval-harness-v3-scalability.md](SPEC-memory-eval-harness-v3-scalability.md)
**Predecessor dataset:** [SPEC-memory-eval-probe-set-v3.md](SPEC-memory-eval-probe-set-v3.md)

This spec covers **data only, plus the offline guards that keep the data
honest**. No production prompt or retrieval changes. No harness schema bump:
`v4` loads under `schema_version 2.0.0` exactly as `v1`, `v2` and `v3` do.

v3 §12 lists "adding a volume (v4) set" under **Ask first**. This spec is that
ask, written out.

---

## 1. Why a fourth probe set

v3 made the twenty cells *hard*. It did not make them *readable*. Those are
different problems and they need different fixes.

Parent §7.3 is the constraint that matters here: **a conclusion is one sample,
not a measurement.** Two runs under identical settings have been seen to
disagree on 2 of 8 questions — including the never-filled arm changing its
answer. At n=1 per cell, a cell that flips tells you nothing about the product,
because a single flake is indistinguishable from a regression.

v2's own stated goal was to make n=1 cells readable, and it got most cells to
n=2. v3 kept that grid and spent its budget on difficulty instead. So the
readability problem is still open, and it is the only thing v4 exists to fix:

> **Every live (scope × test) cell carries n ≥ 3, so one flake cannot flip a
> cell's story.**

Difficulty is not re-litigated. v4 inherits v3's crowded seed wholesale (§6) and
adds volume around it.

### 1.1 What had to land first

Volume was blocked on two harness problems, not on dataset design. Both are now
fixed, and both fixes are what make 50 affordable to *read*:

| Was | Now | Commit |
|---|---|---|
| Correct restraint scored `scope_did_nothing`, the second-worst verdict, on 10 of 20 rows | `restraint_held`, sorted last | `23a2ff6` |
| Every refusal-path grade was `certain=false`, so 10 of 20 rows demanded a hand read | Certain when the probe declared `invented_any` — a dataset lever, not a fixed cost | `4483ef6` |

Without those, 50 probes meant ~25 rows wearing a failure label and ~25 rows
demanding a transcript read. Parent §6.3 named that outcome as its own revisit
trigger: *"Revisit this if the question set ever grows past what a person will
read by hand."* v4 is written on the far side of that revisit.

**This is why §9.1 is a hard rule rather than a style note.** The second fix
only pays out on probes that declare their bait.

---

## 2. Identity — a new file, never an edit of v3

New file `evaluations/MEMORIES/probes/v4-four-scopes-volume.json`, new
`probe_set_id` `v4_four_scopes_volume`. Do **not** use the reserved test-only id
`v3_50_probes` (v3 §12; `tests/unit/.../test_probes.py` owns it).

`run_key` hashes `(probe_set_id, model, seed)` and no question text, so a new id
is the only thing that makes the discontinuity a fact in the report. The report
builder binds on `probe_set_id` **and** `probe_set_sha256` and exits 1 on a
mismatch, so a v3 baseline can never be graded against v4.

The corpus forks for the same reason: v4 indexes
`tests/fixtures/memory_eval/corpus-v4/`, leaving `corpus/`, `corpus-v2/` and
`corpus-v3/` byte-identical so v1–v3 keep reproducing.

The `v4-` filename prefix is load-bearing. `evaluate_memory.py` with no
`--probe-set` calls `resolve_latest_probe_set` (maximum integer after a leading
`v`), so **shipping this file changes the default launch from v3 to v4.**

---

## 3. Language — Vietnamese only

Unchanged from v3 §3, which is unchanged from parent §2.2. Every dataset field
a model reads or is graded against is Vietnamese: `question`, `expect_any`,
`stale_any`, `refusal_about`, `invented_any`, the seed, the corpus.

`note` stays English — it is for us, and no model sees it.

CONTROL must not pass from English priors. There are no English questions.

---

## 4. Probe budget

**50 probes. The budget is derived from n ≥ 3 per live cell, then spent on the
axes that are cheap.**

### 4.1 Which cells are live

Twelve cells exist (4 scopes × 3 tests). Two are dead for reasons already
settled, and this spec does not reopen either:

- **`long_term` update** — `write_profile` overwrites in place, so there is
  nothing to observe. Parent §15.1 item 15.
- **`semantic` update** — a "corrected" document is a ranking question wearing
  an update costume. v3 §4.1 declined it; so does this.

Ten live cells × 3 = **30 probes is the floor**. The remaining 20 are
discretionary, and §4.3 says why they land where they do.

### 4.2 The grid

| scope | recall | update | restraint | total | binding constraint |
|---|---|---|---|---|---|
| `short_term` | 4 | 2 | 4 | **10** | the 8-turn window (§4.4) |
| `long_term` | 1 | — | 5 | **6** | four profile fields, one unguessable (§4.5) |
| `episodic` | 5 | 2 | 5 | **12** | every extra episode costs 100 LLM turns (§4.6) |
| `semantic` | 12 | — | 10 | **22** | none — corpus documents are free (§4.6) |
| | **22** | **4** | **24** | **50** | |

Every live cell is at n ≥ 3 except `long_term` recall, which is **n = 1 and
cannot be raised** — see §4.5. That is a named exception, not an oversight, and
it is the one cell v4 still cannot make readable.

### 4.3 Why the distribution is lopsided, and why that is the honest answer

`semantic` takes 22 of 50 and `long_term` takes 6. That is not balance, and it
should not be defended as balance. It is cost:

- A semantic probe costs **zero LLM seed turns**. The corpus is indexed once.
- An episodic probe's *seed* costs `2 × E` turns **on every probe in the set**,
  not just episodic ones (§4.6).
- A `long_term` probe cannot be added at all beyond restraint, because there are
  only four profile fields.

So depth goes where depth is affordable. The alternative — holding every scope
to 12 or 13 — would buy `long_term` cells that cannot exist and would pay ~200
extra LLM turns for episodic cells that do not answer a new question.

**Read the report per scope, never as one number.** Parent §1 says the point of
this harness is that every result names the memory that produced it; a global
pass rate over a lopsided grid is exactly the number that claim exists to avoid.
The scorecard is already per-scope. Nothing in v4 should produce a headline
figure.

### 4.4 `short_term` is capped by the prompt window, not by imagination

`_MAX_ACTIVE_SESSION_TURNS = 8` in `generation_context.py`, and v3 §4.2 fixes
the rule:

> **`len(seed.short_term) + 1 ≤ _MAX_ACTIVE_SESSION_TURNS`**

So the ST seed can hold **at most 7 lines**, and v4 uses 7 — sitting at 8 of 8,
the ceiling exactly. `test_the_seed_fits_the_prompt_window` enforces it.

This caps `recall` and `update`, which consume distinct seeded facts. It does
**not** cap `restraint`, which is passed by declining and consumes nothing: a
restraint probe exploits what is *absent* from the seed. That asymmetry is why
the ST row reads 4 / 2 / 4 rather than a flat split.

**Do not raise `_MAX_ACTIVE_SESSION_TURNS` to fit more ST probes.** It is
production behaviour, and v3 §13.2 forbids changing production from this work
without triage naming Concern D with a failing test first.

### 4.5 `long_term` recall stays at n = 1

Four fields exist (`PROFILE_PREFERENCE_FIELDS`), and three cannot carry a recall
probe:

- `language` and `timezone` are **proven guessable** — the assistant writes
  Vietnamese unconditionally (parent §2.2), so it names a Vietnamese timezone
  with the profile masked and with the store empty. Parent §7.4 records this as
  a finding from v1.
- `response_tone` contaminates the wording of every other probe's reply.

That leaves `assistant_persona`. One cell, n = 1, permanently. The report must
not be read as though that cell were measured; it is a single sample and parent
§7.3 applies to it with full force.

`long_term` restraint is unconstrained — every fact a profile does not hold is a
candidate — so the scope reaches n = 5 there.

### 4.6 Cost, and the one lever that is not a dataset choice

Harness v3 arithmetic, with `S` = ST seed lines, `E` = episodes, `N` = probes,
`N_st` = ST probes:

```text
seed_llm_turns = 2 × (S × N_st + E × N)
ask_turns      = 3 × N
```

v4 at `S=7`, `E=4`, `N=50`, `N_st=10`:

```text
seed_llm_turns = 2 × (7 × 10 + 4 × 50) = 2 × (70 + 200) = 540
ask_turns      = 3 × 50                                 = 150
total                                                   ≈ 690
```

v3 was ≈ 280. **The run is ~2.5× v3, and 400 of the 690 turns are episodic
re-seeding for probes that do not target episodic memory.**

That is structural, not wasteful. `seed_episodic` runs on every seeded arm of
every probe because `identity_for(identity, probe, arm)` gives each probe its
own tenant — the collision guard from parent §15.1 item 10 — and because v3
§4.2 requires FULL and ABLATED to write the full `SeedSpec` so that FULL is a
realistic full-memory condition rather than a scope in isolation.

**This is why `E` stays at 4.** Each additional episode adds `2 × E × N` = 100
turns at N=50. A fifth episode has to be worth 100 turns and it is not: v3's
four already give a same-shape ranking tie (two CCCD) and a supersession pair
(two passport), which is every episodic question this spec asks.

**The lever, named but not pulled:** sharing one seeded episodic store across
all probes within an arm would cut ~380 turns. It is a harness change that
weakens the per-probe isolation guard, it needs its own red-first test, and it
is out of scope here. If a future set wants `E > 5`, pull this lever first.

One run at a time remains the operating rule (parent §15.1 item 10).

---

## 5. What v4 does not change about difficulty

v3's hardness devices are inherited verbatim, not redesigned:

- crowded `short_term` (a confusable office and a confusable person; the oldest
  line near the trim boundary)
- a same-shape episodic distractor (two CCCD tasks, different offices)
- a semantic lookalike form code (`OT-114` vs `OT-141`)
- `invented_any` on every near-miss with a seeded neighbour

New probes sit **on the existing seed** wherever possible. Where v4 must extend
the seed, it extends it in the direction v3 already established (§6).

A low Full-arm pass rate is compatible with success. Do not soften the dataset
to raise a score — v3 §13.2, and parent §12.2 rule 6.

---

## 6. The seed

### 6.1 Inherited unchanged from v3 §6

- `long_term`: the same four profile values, `assistant_persona = Hải Âu`.
- `episodic`: the same four approved task requests, in the same order. Order is
  load-bearing — `ep_recall_01` resolves an exact ranking tie on `updated_at`
  (parent §15.1 item 18).
- `short_term`: v3's six lines, in order.

### 6.2 The one seed extension

**`short_term` gains a seventh line**, taking the window to 8 of 8. It must
carry a fact that is (a) unguessable, (b) not a near-neighbour of an existing
restraint hole, and (c) safe to place newest, since the oldest line is the one
at risk of eviction and that role belongs to the CCCD/Đà Nẵng fact.

Everything else in the seed is byte-identical to v3.

### 6.3 The corpus is where the volume goes

`tests/fixtures/memory_eval/corpus-v4/` starts as a copy of `corpus-v3/`'s six
documents and grows to support 12 semantic recall probes. New documents cost
nothing at run time.

Each new document must contribute **at least one unguessable token** — a form
code, a figure, a named window — in the shape v3 established. A document that
only supports a probe answerable from general Vietnamese office knowledge adds a
`leaked` row, not a measurement (parent §7.4).

### 6.4 Restraint holes — do not fill them

v3 §6.1's ten holes carry over unchanged and are joined by every new v4
restraint probe's hole. Filling any one of them silently converts a restraint
probe into a recall probe.

The v4 probe file's `note` field must name the hole for every restraint probe,
as v3's does.

**The near-miss rule tightens:** a new corpus document must not accidentally
fill an older set's hole. `corpus-v4/` is forked precisely so this can be
checked in one place.

---

## 7. `invented_any` is now mandatory

This is the rule that changes most from v3, and it is a direct consequence of
`4483ef6`.

> **Every restraint probe declares `invented_any`, or its `note` names the
> reason it cannot.**

A restraint probe that declares bait and declines cleanly is graded
`certain=true` and stays out of `needs_reading`. A restraint probe that declares
nothing is `certain=false` forever, and at 24 restraint probes that is 24 rows
of hand-reading per run.

Two probes in v3 legitimately cannot declare bait, and their reasons are the
template for any future exemption:

- `st_restraint_01` — no neighbouring case number exists anywhere in the seed,
  so there is no plausible invention to name.
- `lt_restraint_03` — the only candidate is the timezone string, and listing it
  would grade an honest time fact as invention.

**An exemption must name the specific text it would have had to list and why
listing it is wrong.** "Could not think of one" is a probe that needs
rewriting — usually by giving it a seeded neighbour, which is also what makes it
harder.

Every declared phrase must already exist in the seed or corpus:
`test_invented_any_phrases_exist_somewhere_in_the_seed` enforces this over every
`probes/*.json`, so v4 is covered by parametrisation the day it ships.

**Target: `needs_reading ≤ 4 of 50.** v3 shipped at 10 of 20 and lands at 6 of
20 under the new rule. If v4's first run reports more than 4, the finding is in
the dataset's bait declarations, not in the product.

---

## 8. Changes outside the dataset

### 8.1 Operator docs: default launch becomes v4

`evaluations/MEMORIES/README.md`'s probe-set table gains a v4 row, and the
"default launch" sentence moves from v3 to v4. Same edit shape as v3 §9.2.

### 8.2 No harness change

No new grade, verdict, report field, or schema bump. `restraint_held` and the
`invented_any` certainty rule already shipped and are not part of this work.

If v4's first run argues for a harness change, that is a **separate** spec.
Shipping a dataset and a harness change together makes the run
uninterpretable — neither one can be attributed.

---

## 9. What this does not change

- **No production code** under `src/cowork_agent/` outside `memory_eval` tests.
- **No production prompt or retrieval change.** v3 §13.2 stands.
- **v1, v2 and v3 keep working.** Their files and corpora stay byte-identical.
- **No golden LLM judge.** Parent §6.3's cost/benefit is re-run in §7 and comes
  out the same way: the fix was making uncertainty a dataset lever, not adding a
  second provider.
- **No CI gating on live results.** Parent §13.
- **`ep_update_01`'s open Concern D is not closed here.** The product has no
  supersession mechanism, only recency ordering (parent §15.1 item 17). v4
  measures it at n ≥ 3 instead of n = 1; it does not fix it.

---

## 10. Boundaries

- **Always:** Vietnamese-only dataset fields (§3); new file + new
  `probe_set_id`; CONTROL never seeded; FULL/ABLATED write the full `SeedSpec`;
  every honesty test parametrized over every `probes/*.json`; `uv run`.
- **Ask first:** SQL migrations, parent `SPEC-memory-evaluation.md` edits,
  production prompt / retrieval changes, any harness change (§8.2), raising
  `_MAX_ACTIVE_SESSION_TURNS`, pulling the shared-episodic-seed lever (§4.6).
- **Never:** edit v1/v2/v3 JSON or `corpus/`, `corpus-v2/`, `corpus-v3/`; commit
  `.env` or secrets; fill a §6.4 restraint hole; use `probe_set_id`
  `v3_50_probes`; skip `seed_long_term` / `seed_episodic` on FULL/ABLATED; set
  `MEMEVAL_ALLOW_REMOTE_POSTGRES=1`; commit `runs/*-detail.json`; treat a low
  Full-arm pass rate as a failed dataset.

---

## 11. Verification

Every check below is an existing parametrized test that picks v4 up from
`probes/*.json` the moment the file lands. **No new test infrastructure is
required**, which is itself the argument that the harness is ready for volume.

| Check | How |
|---|---|
| v4 loads | `test_the_shipped_probe_set_loads` |
| every cue-gated probe fires its read | `test_cue_gated_probes_actually_fire_their_retrieval` |
| no question reads task order | `test_recall_probes_do_not_themselves_create_tasks` |
| every seed request is an explicit task | `test_episodic_seed_requests_are_accepted_as_explicit_task_requests` |
| every recall expectation exists in the seed | `test_recall_expectations_exist_somewhere_in_the_seed` |
| every restraint probe declares `refusal_about` | loader rejection, covered by `test_probes.py` |
| every `invented_any` phrase is grounded in the seed | `test_invented_any_phrases_exist_somewhere_in_the_seed` |
| the ST seed fits the window | `test_the_seed_fits_the_prompt_window` — must read **8 of 8** |
| mechanics | `--probe-set v4-four-scopes-volume.json --dry-run` |

One new offline guard is needed, because §7 makes a rule the loader does not
know about:

- **`invented_any` is declared or exempted.** For every restraint probe in every
  `probes/*.json`, assert `invented_any` is non-empty **or** `note` contains an
  explicit exemption marker. Parametrized like the rest, so v1–v3 must pass it
  too — v3's two exemptions (§7) are the ones that will need the marker written
  in.

### 11.1 What no offline check can establish

That the questions are *hard*, that CONTROL actually fails on them, or that the
distractors bite. Only a live run shows that. Parent §7.4 is the record of this
being learned the expensive way on v1.

### 11.2 What the live run is for

**Harness verification, not product greenwash.** Success means:

- preflight exits 0
- launch without `--probe-set` resolves v4
- baseline JSON carries `probe_set_id: "v4_four_scopes_volume"` and the sha256
  of the shipped file
- the report binds id + hash, and refuses a v3 baseline
- the 3-arm matrix is present for all 50; if `"aborted": true`, still build the
  report from what landed
- `needs_reading ≤ 4` (§7)
- every scope's cells read at n ≥ 3, except `long_term` recall (§4.5)

A low Full-arm pass rate is compatible with success. Do not "fix" the dataset to
raise a score, and do not change production prompts from this run unless triage
names Concern D with a failing test first (v3 §13.2, `mem-eval` skill §3).

---

## 12. Success criteria

1. `evaluations/MEMORIES/probes/v4-four-scopes-volume.json` exists with
   `probe_set_id` `v4_four_scopes_volume`, `schema_version` `2.0.0`, the §6
   seed, and the 50 probes of §4.2.
2. `tests/fixtures/memory_eval/corpus-v4/` exists and supports 12 semantic
   recall probes, each on an unguessable token.
3. v1, v2, v3 and their three corpora are byte-identical to before this change.
4. Every §11 check is green, including the new `invented_any` guard, and it is
   green for v1–v3 as well.
5. Default launch resolves the v4 file.
6. One live run completes the §11.2 checklist.
7. Every live cell reads at n ≥ 3, and `long_term` recall's n = 1 is stated in
   the run's report rather than left for a reader to notice.

---

## 13. Open questions

1. **Does `E = 4` survive first contact at n ≥ 3?** §4.6 argues the four v3
   episodes answer every episodic question this set asks, but that is an
   argument, not a measurement. If the first run shows the five episodic recall
   probes are really the same probe asked five ways, the fix is a fifth episode
   and the 100-turn bill, or the §4.6 lever.
2. ~~**Is a 690-turn run one sitting?**~~ **Answered.** The v3 run of
   2026-08-22T06:53Z took **14.4 minutes wall for ≈280 turns** on
   `mistral-medium-3-5` over scratch SQLite, of which only **1.5 minutes was
   asking** — the other 90% is seeding, exactly as §4.6 predicts. Scaling
   linearly puts v4 at **≈35 minutes**. That is one sitting, so no resumability
   work is needed. It also prices the §4.6 lever: the ~380 turns it would save
   are worth roughly 20 minutes per run.
