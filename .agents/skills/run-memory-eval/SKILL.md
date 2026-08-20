---
name: run-memory-eval
description: >
  Run the agent-memory evaluation end to end: pre-flight every dependency, run the
  harness against a local throwaway PostgreSQL, monitor it, and hand back a written
  report with conclusions worst-first. Use when asked to run the memory eval, produce
  a memory baseline, check whether memory still works, or validate a suspected memory
  defect. Procedure: evaluations/MEMORIES/RUNBOOK.md.
---

# Run the memory evaluation

The full procedure is `evaluations/MEMORIES/RUNBOOK.md`. **Read it before step 1**
— it holds the failure modes, the report-reading order, and the reasons. This
file is the checklist and the things you must not get wrong.

**Arguments** (all optional): a provider (`openrouter` | `gemini` | `groq` |
`faucet`, default `LLM_PROVIDER`), an output name, `--no-live` to pre-check
without spending calls, `--dry-run` to exercise mechanics with a scripted model
and no network.

---

## Rules that override any convenience

1. Work in `.worktrees/feat/agent-tool`. Assert `test -f .git` — a linked
   worktree has `.git` as a **file**. The shell's cwd has been seen to revert to
   the main checkout between calls; re-assert it, don't assume it.
2. The target database must be **local and disposable** (`cowork_memeval`). This
   harness fills memory and then deletes it.
3. **Never** set `MEMEVAL_ALLOW_REMOTE_POSTGRES=1` and never weaken the guard. A
   refused host is the guard working. If the pre-check reports the override is
   already set, stop and tell the user.
4. `.env` holds a **production Supabase URL**. Always pass `PG_TEST_URL`
   explicitly; never rely on default resolution.
5. Read the password out of `.env` inside the command. Never echo it, never put
   it in a report.
6. **Never edit production code to make a report green.** A defect the harness
   finds is fixed deliberately, in its own change, failing test first.
7. Never write reply text into `evaluations/MEMORIES/baselines/` — those files
   are metadata-only and a test enforces it. Reply text lives in `runs/`, which
   is gitignored.
8. The write-up goes to `evaluations/MEMORIES/reports/`, in Vietnamese, by a
   path relative to the repo root, and is never committed (§7). One was
   committed under `docs/references/` on 2026-08-19 for want of this line.
9. **Ask before committing anything.**

---

## Checklist

Make a todo per step.

### 1. Position
```bash
cd /c/WORK/EMAIL-AGENT-v1/.worktrees/feat/agent-tool && test -f .git && pwd
```

### 2. Pre-check
```bash
LOCAL_URL="$(grep -m1 '^DATABASE_URL_LOCAL=' .env | cut -d= -f2-)"
PG_TEST_URL="${LOCAL_URL%/*}/cowork_memeval" PYTHONPATH=src PYTHONIOENCODING=utf-8 \
  .venv/Scripts/python.exe scripts/memeval_preflight.py --provider <provider>
```
Exit `1` means **stop**. Report which check failed and its detail — the script
prints the whole `__cause__` chain, which is usually the actual answer. Do not
start the run to "see what happens".

A `WARN` is for the user to weigh. Surface it; don't decide it silently.

### 3. Offline tier
Cheap, and a failure here makes every number meaningless:
```bash
PYTHONPATH=src .venv/Scripts/python.exe -m pytest -q
```
```bash
.venv/Scripts/python.exe -m ruff check src/ tests/ scripts/
```
Report failures; don't fix them as a side quest unless asked.

### 4. Run
```bash
LOCAL_URL="$(grep -m1 '^DATABASE_URL_LOCAL=' .env | cut -d= -f2-)"
PG_TEST_URL="${LOCAL_URL%/*}/cowork_memeval" PYTHONPATH=src PYTHONIOENCODING=utf-8 \
  .venv/Scripts/python.exe scripts/evaluate_memory.py \
    --provider <provider> --output evaluations/MEMORIES/baselines/<name>.json
```
About 52 model calls — 24 probe asks plus ~28 seeding turns — and single-digit
minutes. **Run it in the background and wait for the completion notification**
— do not poll in a sleep loop.

**Never start a second run while one is in flight.** Concurrent runs pile up on
the schema migration advisory lock and both wedge, with no output — the failure
mode RUNBOOK §6 covers.

**Before restarting a run you believe died, prove it died.** An empty output file
proves nothing: this harness prints almost nothing until it finishes. Check for a
live process, and check the run's own artifacts:

```bash
ls -lt evaluations/MEMORIES/runs/ | head -3
```

A run that got as far as writing a detail file finished. If a process is still
alive, wait — a full run is single-digit minutes and can be longer under a slow
provider. Restarting on the assumption of death is how the 2026-08-19 overlap
happened, and both runs then completed, leaving one baseline and two detail files
that had to be told apart by `ran_at` and mtime. Reports now carry `nonce`
alongside `run_key` for exactly this; `run_key` alone cannot distinguish two runs
of the same probe set and model.

They no longer corrupt each other. They used to: every tenant, user and session
id came from `run_key`, which has no wall-clock component, so two runs of the
same probe set and model addressed identical stores and whichever finished first
tore down the other's. That happened on 2026-08-19, when the runs at `16:35:39Z`
and `16:40:42Z` overlapped by 3.5 minutes. `build_identity` now namespaces every
id with a fresh per-run nonce. `run_key` is unchanged, so two runs of the same
inputs still *report* the same `run_key` while owning different stores — do not
read a shared `run_key` as evidence that two runs shared a store.

### 5. Monitor
React to exactly two things: a line about an API key being evicted, exhausted or
invalid (finish the run, but that scope has no result), and silence past ~10
minutes (RUNBOOK §6 — usually a held advisory lock).

### 6. Read the report, in this order
`seed_failures` → run identity (`provider`, `model`, `probe_set_id`,
`schema_version`, `run_key`) → `verdicts` worst-first → `needs_reading` →
`per_scope` last. Each can invalidate everything below it.

Then **open `runs/<timestamp>-…-detail.json` and read the replies behind every
`certain:false` row.** Those grades rest on a refusal-phrase guess. A conclusion
reported without reading them is not finished work.

### 7. Write up
**Where:** `evaluations/MEMORIES/reports/<YYYY-MM-DD>-<probe-set>.md`, written as
a path relative to the repo root. Never an absolute path — this repo is checked
out at a different path in the main tree and in every worktree, so a link naming
one of them is dead everywhere else. That directory is gitignored, deliberately:
a write-up quotes the questions and the replies in full, which is exactly why
`runs/` is ignored. Do not commit it, and do not put it under `docs/`. The
2026-08-19 write-up was committed under `docs/references/` because this line did
not exist.

**In Vietnamese.** The probe set, the seeded memories and every reply you quote
are Vietnamese; a write-up in another language has to translate its own evidence,
and a mistranslated reply reads exactly like a wrong grade. Keep identifiers,
field names, outcome and verdict labels, and file paths verbatim.

Use the template in RUNBOOK §5. Be explicit about what the run does **not**
support: one run is one sample, and two runs with identical settings have
disagreed on 2 of 8 questions. Never present a difference between two runs as a
finding.

**Comparability is not self-reporting.** `run_key` hashes
`(probe_set_id, model, seed)` — not the questions — and `probe_set_id` is set by
hand. Two reports can look identical in every identity field and have been
graded against different questions. Check `git log
evaluations/MEMORIES/probes/` before comparing, and say what you found.

### 8. If something failed, validate the bug before proposing a fix
Sort it first:

- **A — the grader**, **B — the question**, **C — the plumbing**: harness. Fix
  freely, failing test first.
- **D — the product**: production. Deliberate change, own commit, never to make
  a report green.

A fault in A, B or C makes every reading of D meaningless, so rule them out in
that order. Before calling a question guessable (B), confirm the mask actually
masks (C) via
`tests/unit/features/ai_chat/memory_eval/test_arm_masking_reaches_the_model.py`.

Then: reproduce or admit you cannot → read the replies → one hypothesis, and
what would falsify it → **measure before implementing** → failing test → fix →
re-run the offline tier → record it (SPEC for a defect found, SPEC §15.1 for a
gap deliberately left open).

---

## Do not

- Do not report a scope's counts when that scope is named in `seed_failures`. It
  was never asked; it did not "find nothing".
- Do not call a `leaked` or `scope_did_nothing` result a product problem without
  first checking the question (SPEC §7.4).
- Do not treat exit code `0` as "memory is good". It means the harness ran.
- Do not commit without asking.
