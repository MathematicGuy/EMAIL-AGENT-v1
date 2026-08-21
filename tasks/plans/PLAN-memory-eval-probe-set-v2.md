# PLAN — Memory Evaluation Probe Set v2

**Spec:** [SPEC-memory-eval-probe-set-v2.md](../specs/SPEC-memory-eval-probe-set-v2.md)
**Parent spec:** [SPEC-memory-evaluation.md](../specs/SPEC-memory-evaluation.md)
**Branch:** `dev` (current)

Eight tasks, ordered so that **the guards land before the data they guard**.
Writing the probe file first would mean authoring 20 Vietnamese questions with
no test able to tell me a cue is dead or a question parses as a task order —
which is exactly the failure mode the parent spec §2.2 documents as having
silently disabled four of v1's eight probes.

Each task states its own verification. Nothing is "done" on inspection.

---

## Task 1 — Parametrize the invariant tests over `probes/*.json`

**Files:** `tests/unit/features/ai_chat/memory_eval/test_probe_set_fires_retrieval.py`,
`tests/unit/features/ai_chat/memory_eval/test_shipped_probe_set.py`

Both hardcode `_PROBE_SET = .../v1-four-scopes.json`. Replace with discovery over
the probes directory and parametrize every test on the discovered path, so the
failing test names the file it failed on.

- Add a `test_probe_sets_are_found` guard asserting the glob is non-empty. A
  wrong glob makes every parametrized assertion vacuously true, which is how a
  wrong path fails silently — the existing file already carries this reasoning
  for its single path and it matters more once the path is computed.
- `test_recall_expectations_exist_somewhere_in_the_seed` must read **that set's
  own** `seed.semantic.corpus_dir`, not a constant. v2 uses a different corpus;
  a shared constant would grade v2's expectations against v1's documents.

**Verify:** `pytest tests/unit/features/ai_chat/memory_eval/ -q` — same number of
logical assertions, now parametrized over the one existing file, all green. No
behaviour change yet; this is the harness for tasks 4–6.

---

## Task 2 — Add the seed-turn budget test (red first)

**File:** `tests/unit/features/ai_chat/memory_eval/test_shipped_probe_set.py`

New parametrized test asserting, per probe set:

```
len(seed.short_term) + len(seed.episodic) + 1 <= _MAX_ACTIVE_SESSION_TURNS
```

Import `_MAX_ACTIVE_SESSION_TURNS` from `generation_context` — do not write `8`.
If the product changes the window, this test must move with it.

The failure message has to explain the mechanism, because the symptom is
invisible: short_term probes deliberately keep their seeded session (parent
§5.3), `live_runner` seeds short_term then episodic into it, and the probe turn
lands on top; anything past the window evicts the **oldest** short_term seed
line, whose recall probe then reports amnesia on the `full` arm.

**Verify:** passes on v1 (5 ≤ 8). Confirm it can fail: temporarily pad a copied
seed past the cap in a scratch parametrization, watch it go red, remove the
scratch. A guard never seen red is not known to guard anything.

---

## Task 3 — Build `corpus-v2/`

**Files:** `tests/fixtures/memory_eval/corpus-v2/{leave-policy,overtime-policy,remote-work-policy,travel-expense-policy,equipment-policy}.md`

Copy `leave-policy.md` and `overtime-policy.md` from `corpus/` **unchanged**.
Write three new documents per spec §6. All Vietnamese, all invented, each ending
in the existing "không phải chính sách thật" disclaimer.

Load-bearing details, in the file and in a comment on the probe that depends on
each:

- `remote-work-policy.md` carries form code **`WFH-207`** and a monthly cap.
- `travel-expense-policy.md` carries **`450.000 đồng`** per day and covers
  **domestic travel only** — it must not mention overseas travel at all, or
  `sem_restraint_02` stops being a restraint probe.
- `equipment-policy.md` carries a laptop replacement procedure and **no form
  code of any kind**, or `sem_restraint_03` stops being a restraint probe.

Leave `tests/fixtures/memory_eval/corpus/` untouched.

**Verify:** `grep -c` for `OT-114`, `WFH-207`, `450.000` returning exactly one
hit each across the new corpus; `grep -riE "biểu mẫu|form|mã số" equipment-policy.md`
returning nothing.

---

## Task 4 — Write `v2-four-scopes-wide.json`

**File:** `evaluations/MEMORIES/probes/v2-four-scopes-wide.json`

Seed per spec §4, twenty probes per spec §5. `schema_version: "2.0.0"`,
`probe_set_id: "v2_four_scopes_wide"`.

Authoring rules, each of which the tests from tasks 1–2 now enforce:

- Every `episodic` question contains `tác vụ trước`; every `semantic` question
  contains `chính sách công ty`. Exact tokens — `_contains_cue` matches token
  sequences after `casefold()`, and `casefold()` does not strip accents.
- No question contains a `_TASK_DIRECTIVE_VERBS` token (`tạo`/`lập`/`lên`)
  followed within two fillers by `tác vụ` / `nhiệm vụ` / `công việc` /
  `kế hoạch`. Asking a probe must never write an episode.
- Every `expect_refusal` probe declares `refusal_about` naming **its own noun**,
  never a bare generic (`chính sách`, `quy định`). Parent §6.3: a decline about
  policy in general would be satisfied by a reply drawn from the wrong policy,
  and `sem_restraint_01` exists precisely to catch that reply.
- `seed.short_term` stays at 4 entries and `seed.episodic` at 3. Task 2's test
  is the backstop, but the budget is deliberate, not incidental — spec §3.3.
- Every probe carries a `note` explaining what it would catch and why its
  expectation is unguessable. v1's notes are the model; they are the only place
  this reasoning survives into the next person's hands.

**Verify:** the full parametrized suite from tasks 1–2 green over both probe
sets. Then `python scripts/evaluate_memory.py --probe-set <path> --dry-run` —
exit 0, a report with `probe_count: 20`.

---

## Task 5 — Record the long_term-update gap in the parent spec

**File:** `tasks/specs/SPEC-memory-evaluation.md` §15.1

Append one numbered item: `update` is not measurable on `long_term` because
`seed_long_term` performs a single `write_profile` that overwrites the row, and
every non-`short_term` probe gets a fresh conversation (§5.3), so no path exists
by which a superseded profile value could reach the model. Name the change that
would be needed — `seed.long_term` becoming a list of successive writes, a
`probes.py` + `seeding.py` change and a schema bump — and note that it would buy
a probe that still could not observe a stale profile value.

Parent §12.2 rule 6. A capability left unprobed because the product cannot
support it leaves a named gap, not silence.

**Verify:** read back in context; no contradiction with §10.4, which explains why
v1 ships `update` on `short_term` only.

---

## Task 6 — Point the docs at both sets

**Files:** `evaluations/MEMORIES/README.md`, `evaluations/MEMORIES/RUNBOOK.md`,
`tasks/specs/SPEC-memory-evaluation.md` §14 file map

Both sets are runnable and they are **not** comparable to each other. Say so
where a reader chooses one: v1 is the 8-probe set every committed baseline was
graded against; v2 is the 20-probe set, ~60 live turns, one run at a time.

Add `probes/v2-four-scopes-wide.json` and `tests/fixtures/memory_eval/corpus-v2/`
to the §14 file map.

**Verify:** every path named in the changed lines exists on disk.

---

## Task 7 — Write the golden-dataset authoring prompts

**Spec:** §8. **Files:** `evaluations/MEMORIES/golden/README.md`,
`evaluations/MEMORIES/golden/prompts/{CONTRACT,short-term,long-term,episodic,semantic}.md`

Runs **after Task 4**, not before: the prompts name probe ids, and prompts
naming ids that do not exist yet cannot be checked against anything.

This task produces **prompts only**. No golden answers, no subagent run — the
operator picks the subagent and runs them later.

`CONTRACT.md` carries every rule the four scope prompts inherit:

- **Ground every answer in the seed.** Read
  `evaluations/MEMORIES/probes/v2-four-scopes-wide.json` and
  `tests/fixtures/memory_eval/corpus-v2/`. Every golden answer cites the seed
  line or corpus line it came from. Content traceable to neither is forbidden.
  A probe whose answer cannot be cited is **reported back as a defect in the
  probe**, never answered — spec §8.2.
- **Three references per probe, not one** — `full` = the content answer;
  `ablated` and `control` = a decline. `restraint` probes decline on all three.
  A probe with a content answer on `control` is a defect (parent §7.4), reported
  and not written — spec §8.3.
- **Accented Vietnamese.** `casefold()` does not strip accents, so an unaccented
  golden answer is a different string from what the product writes (parent §2.2).
- **The output schema**, and the part file each scope prompt writes to
  `evaluations/MEMORIES/golden/parts/<scope>.json`.
- **Do not edit the probe file, the corpus, or anything under `src/`.** The
  subagent's whole output is its part file plus a list of reported defects.

Each scope prompt is then short — a page or less: which probe ids it owns, which
seed material grounds them, where to write. It **references paths, never embeds
their text**; embedding would rot silently the first time a probe is edited
(spec §8.4).

`README.md` says what the directory is, how to run the four prompts in parallel,
how to merge `parts/*.json` into `v2-four-scopes-wide.golden.json`, and — per
spec §8.6 — that no judge consumes this file yet.

**Verify:** every path named in every prompt exists on disk (`ls` each one);
every probe id named across the four scope prompts, sorted, equals the 20 ids in
the probe file, checked with a command and not by eye. A prompt that omits a
probe produces a golden file with a silent hole in it.

---

## Task 8 — Full offline suite and dry run

```
pytest tests/unit/features/ai_chat/memory_eval/ tests/unit/scripts/test_evaluate_memory.py -q
pytest tests/unit/features/ai_chat/test_retrieval_policy_vietnamese.py -q
python scripts/evaluate_memory.py --probe-set evaluations/MEMORIES/probes/v2-four-scopes-wide.json --dry-run
```

All green, dry run exits 0. Report the actual command output — not "should
pass".

**Not in scope:** a live run. Parent §13 keeps the live tier out of the gate, and
parent §7.3 means one live run could not validate a dataset anyway. If you want
one afterwards it is a separate decision, and RUNBOOK §1's preflight comes first.

---

## Risks

| Risk | Handling |
|---|---|
| A v2 question carries no cue and silently measures nothing | Task 1's parametrized cue test, red before the data lands |
| A v2 question parses as a task order and writes an episode when asked | Same, `test_recall_probes_do_not_themselves_create_tasks` |
| The seed overflows the 8-turn prompt window and reads as amnesia | Task 2, and the seed is authored at exactly the cap on purpose |
| A recall expectation turns out guessable | Not detectable offline (spec §9.1). The `control` arm finds it on the first live run; expect to retire one or two expectations then, as happened twice to v1 |
| A reader compares a v1 baseline to a v2 report | Different `probe_set_id`, which parent §12.2 rule 5 keys on; task 6 says it in prose too |
| 60 live turns draw more provider dropouts than 24 | Real and unmitigated. Parent §15.1 item 10; one run at a time |
| A golden answer is a subagent's guess rather than a reading of the seed | `CONTRACT.md` requires a seed/corpus citation per answer and forbids uncited content; an unciteable probe is reported, not answered (spec §8.2) |
| A scope prompt silently omits a probe | Task 7's id-set check, run as a command against the probe file |
| The prompts drift from an edited probe file | They reference paths and never embed text (spec §8.4) |

## Definition of done

- `v2-four-scopes-wide.json` and `corpus-v2/` committed; v1 and `corpus/` byte-identical to before.
- Offline suite green over **both** probe sets, including the new budget guard.
- `--dry-run` on v2 exits 0 with `probe_count: 20`.
- The long_term-update gap is a numbered item in the parent spec's §15.1.
- README, RUNBOOK and the §14 file map name both sets and say they are not comparable.
- `golden/prompts/` holds the contract plus four scope prompts; between them they
  name all 20 probe ids, verified by command, and every path they reference exists.
- No golden answers are produced by this change — the prompts are the deliverable.
- No file under `src/cowork_agent/` changed.
