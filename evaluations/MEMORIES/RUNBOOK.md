# RUNBOOK — running the memory evaluation

`tasks/specs/SPEC-memory-evaluation.md` says what this measures and why. `FLOW.txt` walks through it in prose.
**This file is the procedure**: what to check before spending money, how to run
it, how to watch it, how to write up what came back, and what to do when
something breaks.

It is written to be followed by a person or by a coding agent. The skill
`/run-memory-eval` is this document as a prompt.

---

## The rules that do not bend

Read these before the first command. Every one of them exists because breaking
it either destroys real data or produces a report that reads as a fact and is
not one.

1. **Work in the worktree**, never the main checkout. `test -f .git` — a linked
   worktree has `.git` as a *file*.
2. **Never point the harness at a remote or production database.** It fills
   memory and then deletes it. Against a shared store that is a write-and-delete
   on real data.
3. **`.env` in this checkout carries a `DATABASE_URL_CLOUD` pointing at a
   production Supabase instance.** Always set `PG_TEST_URL` explicitly. Never
   rely on the default resolution to be the one you assumed.
4. **Never set `MEMEVAL_ALLOW_REMOTE_POSTGRES=1`, and never weaken the guard in
   `live_env.probe_environment`.** A refused host is the guard working.
5. **Never edit production code to make a report green.** Fixing a real defect
   the harness found is the job. Turning a report green by editing the thing
   being measured is not. If a fix is needed, it is a separate, deliberate
   change with its own failing test first.
6. **`evaluations/MEMORIES/runs/` is gitignored and holds full reply text.**
   Committed reports under `baselines/` stay metadata-only; a test enforces it.
   Do not paste reply text into a committed file.
7. **Ask before committing.** A run produces artefacts; committing them is a
   separate decision.

---

## 0. Where you must be

```bash
cd /c/WORK/EMAIL-AGENT-v1/.worktrees/feat/agent-tool && test -f .git && pwd
```

Everything below assumes:

- the interpreter is `.venv/Scripts/python.exe` — bare `python` on this machine
  hits the Windows App Execution Alias and fails;
- `PYTHONPATH=src` is set for anything run outside pytest;
- `PYTHONIOENCODING=utf-8` is set, because every question and answer is
  Vietnamese and the Windows console codepage will otherwise raise mid-run.

The Bash tool's working directory has been observed to revert to the main
checkout between calls. `cd` at the start of each script, and assert `.git` is a
file, rather than trusting it.

---

## 1. Pre-check — prove every dependency answers

```bash
LOCAL_URL="$(grep -m1 '^DATABASE_URL_LOCAL=' .env | cut -d= -f2-)"
PG_TEST_URL="${LOCAL_URL%/*}/cowork_memeval" PYTHONPATH=src PYTHONIOENCODING=utf-8 \
  .venv/Scripts/python.exe scripts/memeval_preflight.py --provider openrouter
```

Read the password out of `.env` like this rather than typing it. It must not
appear in a command line, a log, or a report.

`--json` prints the same result machine-readably. `--no-live` skips the two
calls that cost money and downgrades those checks to warnings — useful when
checking wiring, useless as evidence that a key works.

Exit code `0` means nothing failed. Exit `1` means **do not run the
evaluation**.

### What each check is actually asking

| Check | The question | On failure |
|---|---|---|
| `checkout` | Are we in a tree that holds this harness? | You are in the wrong directory. Go to §0. |
| `probe_set` | Does the question file load and validate? | A malformed probe file. Fix it before spending a single call. |
| `target` | Which store would this run write to, and is it allowed to? | Set `PG_TEST_URL` at a local throwaway. If it says the remote override is set, **unset it** — do not proceed. |
| `postgres` | Does that database answer? | Start the server, or create `cowork_memeval`. |
| `postgres_locks` | Did a killed run leave backends idle in transaction? | See §6, "the run hangs". |
| `embeddings` | Does the corpus embedder return a vector of the right size? | The semantic scope cannot be filled. A run will still complete and will report semantic as a **seed failure** — not as a memory that found nothing. |
| `chat` | Does the model answer with text? | There is no run. Without a reply there is nothing to score. |

The last two are the reason this script exists. A key that is *set* proves
nothing; a key that is exhausted or revoked produces a full report in which a
memory looks empty. `embeddings` and `chat` each spend one call to prove the
dependency answers, and print the whole `__cause__` chain when it does not —
the chat adapters raise `ChatReplyUnavailable("configured chat provider is
unavailable")` from the real error, so without the chain a broken run reports
twenty-four identical arms and no reason for any of them.

### The offline tier, which costs nothing

Run this too. It is what CI blocks on, and a failure here means the harness
itself is wrong, which makes every number a run produces meaningless.

```bash
PYTHONPATH=src .venv/Scripts/python.exe -m pytest -q
```
```bash
.venv/Scripts/python.exe -m ruff check src/ tests/ scripts/
```
```bash
.venv/Scripts/python.exe -m mypy --strict src/cowork_agent/features/ai_chat/memory_eval/
```

And the mechanics end to end, with a scripted model and no network:

```bash
PYTHONPATH=src .venv/Scripts/python.exe scripts/evaluate_memory.py --dry-run --output /tmp/dry.json
```

A dry run **measures nothing**. It proves the wiring assembles a report.

---

## 2. The run

```bash
LOCAL_URL="$(grep -m1 '^DATABASE_URL_LOCAL=' .env | cut -d= -f2-)"
PG_TEST_URL="${LOCAL_URL%/*}/cowork_memeval" PYTHONPATH=src PYTHONIOENCODING=utf-8 \
  .venv/Scripts/python.exe scripts/evaluate_memory.py \
    --provider openrouter \
    --output evaluations/MEMORIES/baselines/<name>.json
```

Pick `<name>` to say what was measured, not when — the timestamp is inside the
report. `vi-postgres-2.json` follows `vi-postgres.json`.

What happens, in order (SPEC §9): a run identity is built and hashed; each of
the four memories is filled through the product's own permission path; every
question is asked three times — everything on, one memory hidden, store never
filled; each answer is graded; conclusions are derived; everything the run
created is deleted, including when a question raised.

**Cost and duration.** Eight questions × three settings = 24 probe asks, and
seeding roughly doubles that: every non-control arm is seeded with every scope
the probe set declares, which is one episodic turn on each of the twelve
fresh-session arms and four turns on each of the four `short_term` arms. Budget
**about 52 model calls**, plus the embedding of the corpus and the two the
pre-flight spends. Expect single-digit minutes. Run it in the background and
wait for it rather than polling.

**It writes two files:**

- `baselines/<name>.json` — committed, metadata only, no reply text.
- `runs/<timestamp>-<probe_set_id>-detail.json` — gitignored, holds every
  question and every reply. This is what you read when a grade is marked
  uncertain.

**Exit code `0` does not mean memory is good.** It means the harness ran.

---

## 3. While it runs

Normal looks like: a line about seeding, then a long quiet stretch while 24
calls are made, then the report on stdout.

Two things are worth reacting to:

- **Any line about an API key being evicted, exhausted or invalid.** The
  pre-check should have caught it. If it appears mid-run, the run is still
  worth finishing — the affected scope will be reported as a seed failure —
  but say so in the write-up rather than reporting that scope's numbers.
- **Silence past roughly ten minutes.** Go to §6.

Do not start a second run while one is in flight. Concurrent runs pile up on
the migration advisory lock and both wedge.

---

## 4. Reading the report

Read the fields in this order. The order matters: each one can invalidate
everything below it.

1. **`seed_failures`** — first, always. A memory that could not be filled or
   reached did not "find nothing"; it was never asked. Every scope named here
   has no result this run, whatever its counts say (SPEC §12.2 rule 2).
2. **`provider`, `model`, `probe_set_id`, `schema_version`, `run_key`** — what
   this was graded against and what answered it. Two reports are comparable
   only at the same `probe_set_id` and `schema_version`, and even then only as
   samples.
3. **`verdicts`**, worst first. The ordering is
   `unreadable` → `dangerous` → `broken` → `leaked` → `scope_did_nothing` →
   `scope_earned_it`.
4. **`needs_reading`** — how many conclusions rest on a guess (`certain:false`).
   Those are refusal-phrase judgements. Resolve them by reading the actual
   replies in `runs/…-detail.json`.
5. **`per_scope`** — the counts, last. They are a summary of everything above
   and mean nothing without it.

### What each conclusion means

| Conclusion | Reading |
|---|---|
| `unreadable` | This question got no answer this run. Not a finding about the product. Run it again. |
| `dangerous` | Something was invented or out of date. The most serious thing a report can say. |
| `broken` | The memory did not deliver — **and may be a defect in the lookup rather than in the store** (SPEC §7.5). |
| `leaked` | The never-filled setting answered it too, so the question is not a memory question. A fault in the *question*, not the product. |
| `scope_did_nothing` | Right answer, wrong credit: it came from somewhere other than the memory under test. Usually a guessable question (SPEC §7.4). |
| `scope_earned_it` | The memory did its job. |

### One run is one sample

Two runs with identical settings have disagreed on 2 of 8 questions, including
the never-filled setting changing its answer. **Do not compare two runs and call
the difference a finding.** A claim that something improved or regressed needs
repeated runs with the variation stated (SPEC §7.3).

---

## 5. The write-up

What to hand back. Short, and in this shape.

```
RUN
  report:      evaluations/MEMORIES/baselines/<name>.json
  detail:      evaluations/MEMORIES/runs/<timestamp>-...-detail.json
  provider:    <provider>/<model>
  target:      <host>:<port>/<database>       (no password)
  probe set:   <probe_set_id>, run_key <run_key>

PRE-CHECK
  <one line per check, and anything that warned>

SEED FAILURES
  <verbatim, or "none">
  <if any: which scopes therefore have no result this run>

CONCLUSIONS, WORST FIRST
  <probe>  <targets>  <verdict>   full/ablated/control   <certain?>
  ...

NEEDS READING (<n>)
  <for each uncertain row: the reply, quoted from the detail file, and what
   you concluded from reading it>

WHAT THIS RUN DOES AND DOES NOT SUPPORT
  <what it is evidence for; what it is not; whether a second run is needed>

SUSPECTED DEFECTS
  <see §6 — with the concern each belongs to, and the measurement, if any>
```

Never put reply text into a file under `baselines/`. Quoting a reply in the
chat write-up is fine; committing it is not.

---

## 6. When something breaks — validating the bug before fixing it

A bad result is not automatically a bad memory. Sort it into one of four
concerns before touching anything:

| | Concern | Fix policy |
|---|---|---|
| **A** | **The grader** — how an answer is turned into a grade | Harness. Fix freely, red test first. |
| **B** | **The question** — what was asked and what was accepted | Harness. Fix freely. |
| **C** | **The plumbing** — seeding, masking, arms, teardown | Harness. Fix freely. |
| **D** | **The product** — what the memory system actually does | Production. Fix deliberately, never to make a report green. |

**A failure in A, B or C makes every reading of D meaningless.** So rule them
out in that order. In particular: before concluding that a question was
guessable (B), check that the mask actually masks (C) — if the hidden value were
still reaching the model, the arm would be lying and every conclusion in every
report would be void.
`tests/unit/features/ai_chat/memory_eval/test_arm_masking_reaches_the_model.py`
is that check.

### The drill

1. **Reproduce, or admit you cannot.** One run is one sample (§4). A conclusion
   seen once is a hypothesis.
2. **Read the actual replies** in `runs/…-detail.json` before theorising. Most
   surprising verdicts are explained by the text.
3. **State one hypothesis, and say what measurement would falsify it.**
4. **Measure before implementing.** The episodic defect in SPEC §7.5 was found
   this way, and the first proposed fix was killed by the measurement that was
   supposed to confirm it.
5. **Write the failing test first**, then fix, then run the offline tier again.
6. **Record it.** A defect the harness found belongs in the spec
   (`tasks/specs/SPEC-memory-evaluation.md`); a gap you
   chose not to close belongs in `SPEC §15.1` as a named limit, not in silence
   (SPEC §12.2 rule 6).

### Known failure modes, and what they are

| Symptom | What it is |
|---|---|
| Every arm returns `chat_provider_unavailable` | The provider, not the memory. `ChatReplyUnavailable` hides its cause; the pre-check prints the `__cause__` chain. |
| The run hangs with no output | A killed earlier run left the `schema_migrations` advisory lock held by an idle backend. Advisory locks are session-scoped and survive rollback. Terminate backends **scoped to the eval database only**: `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'cowork_memeval' AND pid <> pg_backend_pid()`. |
| `psycopg_pool.PoolTimeout: pool initialization incomplete` | Windows: something ran on the default ProactorEventLoop. Async psycopg needs `asyncio.SelectorEventLoop(selectors.SelectSelector())` — `live_env.run_with_selector_loop` exists for this. |
| `UnicodeEncodeError` printing a reply | `PYTHONIOENCODING=utf-8` was not set. |
| A whole scope reported empty, with a key eviction line earlier in the log | An exhausted key. That scope has **no result** — it is a seed failure, not a finding. |
| `UnsafeTargetError` | The guard working. Point `PG_TEST_URL` at a local throwaway. Do not set the override. |

---

## 7. Afterwards

- The report under `baselines/` is committable; the detail file under `runs/` is
  not, and is already gitignored.
- **Ask before committing.**
- **Changing a question changes nothing the report records.** `run_key` hashes
  `(probe_set_id, model, seed)`; the question text and `expect_any` are in none
  of them, and `probe_set_id` is set by hand in the probe file. Two reports can
  therefore carry identical identity fields and have been graded against
  different questions. Until `probe_set_id` is bumped deliberately, check
  `git log evaluations/MEMORIES/probes/` before comparing two reports, and say
  so in the write-up (SPEC §15.1 item 8).
