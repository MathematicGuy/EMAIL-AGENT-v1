# Memory Evaluation

Measures whether each of our four memory scopes holds what was put in it, drops
superseded values, and refuses to invent — with every result attributable to
exactly one scope.

Five documents, no overlap:

| | |
|---|---|
| **[FLOW.txt](./FLOW.txt)** | What it measures and how, in plain language. **Read this first.** |
| [WORKFLOW.md](./WORKFLOW.md) | The order you do things in, what each step is worth, and how to read a report without fooling yourself. |
| **[RUNBOOK.md](./RUNBOOK.md)** | The operating procedure: pre-check every dependency, run, monitor, write up, validate a suspected defect. Also available as the `/run-memory-eval` skill. |
| [SPEC-memory-evaluation.md](../../tasks/specs/SPEC-memory-evaluation.md) | The design and the reason behind each decision. The authority on intent. |
| this file | How to run it and how to read what comes out. |

## Run it

**For a real run, follow [RUNBOOK.md](./RUNBOOK.md)** — it pre-checks that every
dependency actually answers before any money is spent, and states the safety
rules about which database may be written to. What follows is the shape of the
commands; the runbook is the procedure.

```powershell
# Mechanics only. No key, no database, scripted replies.
python scripts/evaluate_memory.py --dry-run
```

A dry run validates that the harness works. It measures nothing about the real
system and must never be used to make a decision.

```powershell
# The real thing. Needs a store (see below), a key for the provider, and an
# embedding key for the corpus: GEMINI_API_KEY, or JINA_API_KEY when
# DOCUMENT_EMBEDDING_PROVIDER=jina.
# --provider defaults to $LLM_PROVIDER, else gemini.
python scripts/evaluate_memory.py --provider openrouter
```

8 probes × 3 arms = 24 model calls. The report is written under `baselines/`
and printed to stdout.

Before that, prove the dependencies answer rather than merely being configured —
an exhausted embedding key produces a full report in which semantic memory looks
empty:

```powershell
python scripts/memeval_preflight.py --provider openrouter
```

**Exit code 0 means the harness ran**, not that memory is good.

## `POSTGRES_MODE` — which store is under test

`short_term` lives in-process and `semantic` is a corpus, but `long_term` and
`episodic` are durable, and the product backs them with **two different
implementations**. The harness follows the same rule the app does
(`cowork_agent/config.py::database_url`), because evaluating the store the
product is not running would describe a system nobody uses.

| `POSTGRES_MODE` | resolves to | backs `long_term` / `episodic` with |
|---|---|---|
| `off` | nothing | SQLite — a scratch `runs/memeval-chat.db`, created and thrown away |
| `local` | `DATABASE_URL_LOCAL` | `PostgresChatProfileRepository` + `PostgresTaskEpisodeRepository` |
| `cloud` | `DATABASE_URL_CLOUD` | the same Postgres pair |
| unset | `DATABASE_URL` if set, else SQLite | either, per the legacy variable |

`PG_TEST_URL` overrides all of it, matching `tests/integration/persistence`.

These are **not interchangeable and do not share bugs** — a defect has already
been found in one path that the other never had. A clean run against SQLite says
nothing about Postgres, and vice versa. Evaluate whichever one you actually ship;
run both if you ship both.

```powershell
# SQLite: no server, nothing to install, safe to run anywhere.
$env:POSTGRES_MODE = "off"; python scripts/evaluate_memory.py

# Postgres: start the throwaway container first (docker desktop start; docker compose up -d postgres)
$env:PG_TEST_URL = "postgresql://cowork:cowork_dev_only@127.0.0.1:5432/cowork_mail_todo"
python scripts/evaluate_memory.py
```

### Never point this at a shared database

The harness seeds memory and then deletes it. Against a shared or production
database that is a write-and-delete on real data. `probe_environment` therefore
raises `UnsafeTargetError` for any non-local host unless
`MEMEVAL_ALLOW_REMOTE_POSTGRES=1` is set. Do not set it. If a run is refused,
fix the environment rather than the guard — `load_runtime_environment()` reads
`.env` from the current working directory, so a stale `.env` in a worktree can
silently aim the harness somewhere it must never write. That has happened.

Use `cowork_mail_todo` for Postgres runs, never `cowork`: the former is the
disposable test database, and the integration suite does `DROP SCHEMA public`
on it.

## How to read a report

Every probe is asked three times — once per arm — and those three outcomes
collapse into one verdict.

| arm | what changes |
|---|---|
| `full` | nothing: all four scopes seeded and readable |
| `ablated` | the probe's target scope is masked out of the read |
| `control` | **the seed is skipped**; all scopes enabled, store empty |

`control` disables the seed, **not** the read — the distinction the whole leak
signal rests on. [FLOW.txt §3](./FLOW.txt) explains why in full.

**One run is one sample.** Two runs at identical configuration have been seen to
disagree on 2 of 8 probes, including a `control` arm changing its answer. Treat a
difference between two reports as a hypothesis, not a finding. See
[SPEC §7.3](../../tasks/specs/SPEC-memory-evaluation.md).

Rows are sorted worst-first, so the top of the table is where to look.

| verdict | means | what to do |
|---|---|---|
| `dangerous` | some arm asserted a superseded answer or invented one | Fix first. This is the headline. |
| `broken` | the scope did not deliver even with everything enabled | Check `seed_failures` before blaming the scope. |
| `leaked` | the control arm passed | Not a memory probe. Rewrite it or drop it. |
| `scope_did_nothing` | right answer, but the ablated arm passed too | The answer came from elsewhere; the probe is mis-targeted. |
| `scope_earned_it` | only the full arm passed | The scope is doing its job. |

Two fields decide how much of the above you can believe:

- **`seed_failures`** — scopes that could not be reached or seeded, with the
  reason. A `broken` verdict on a scope listed here says nothing about memory.
  Read this column first.
- **`needs_reading`** — how many rows rest on the refusal phrase list rather
  than on a declared substring. The harness does not resolve these; open the
  matching reply in `runs/` and decide yourself. See [SPEC §6.3](../../tasks/specs/SPEC-memory-evaluation.md).

## The probes are Vietnamese

The assistant answers only in Vietnamese, so the probe questions, the seed
turns, the refusal phrases and the retrieval cues are all Vietnamese. This is
not cosmetic: while the cue lists were English, a Vietnamese question fired no
retrieval at all and four of the eight probes measured nothing while reporting a
memory failure. [SPEC §2.2](../../tasks/specs/SPEC-memory-evaluation.md) has the full list of what broke.

Diacritics are load-bearing — matching is case-folded but not accent-folded, so
`khong ro` never meets `không rõ`. Keep new probe text accented.

## Rules

- **Committed reports are metadata-only.** Case ids, counts, verdicts, timings,
  model identifiers. No questions, no replies, no seed text. A unit test
  enforces this.
- **`runs/` is gitignored.** Full replies live there for debugging.
- **Two reports are comparable only at the same `probe_set_id` and
  `schema_version` — and only if they ran against the same store.** A SQLite
  baseline and a Postgres baseline measure different implementations. The report
  does not yet record which one it used, and `run_key` does not either, so the
  two are indistinguishable on disk: note the store yourself until that is
  fixed.

## What this does not cover

Cross-tenant isolation, summary episodes, and the launch-gate bridge are all
out of scope for v1, each for a stated reason.
[SPEC §15.1](../../tasks/specs/SPEC-memory-evaluation.md) is the single list — it says what is missing and why,
and nothing here duplicates it.

The one worth knowing before you read any result: **there is no isolation
probe.** Seeding a second tenant is not wired, and a probe that asks for
material nobody seeded gets a refusal from an empty store — it would look like
a passing tenancy check while proving nothing. Cross-tenant isolation is covered
strictly, and offline, by the memory-policy unit tests. [SPEC §5.2](../../tasks/specs/SPEC-memory-evaluation.md)
covers why it also may never target `semantic`: the company RAG corpus has no
tenant partition anywhere, by design.
