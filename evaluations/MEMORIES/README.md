# Memory Evaluation

Measures whether each of the four memory scopes holds what was put in it, drops
superseded values, and refuses to invent — with every result attributable to
exactly one scope.

**This file is the commands and the store configuration.** What any of it means
is [MEMORY_IN_A_NUTSHELL.md](./MEMORY_IN_A_NUTSHELL.md). How to run it for real
is [RUNBOOK.md](./RUNBOOK.md). Why it is built this way is
[SPEC-memory-evaluation.md](../../tasks/specs/SPEC-memory-evaluation.md).

## Commands

On this machine, bare `python` hits the Windows App Execution Alias and fails.
Vietnamese text needs `PYTHONIOENCODING=utf-8` or the console codepage raises
mid-run.

```powershell
# Mechanics only: no key, no database, scripted replies. Measures nothing.
$env:PYTHONPATH="src"; .venv/Scripts/python.exe scripts/evaluate_memory.py --dry-run
```

```powershell
# Prove every dependency answers before spending anything. Exit 1 = do not run.
$env:PYTHONPATH="src"; $env:PYTHONIOENCODING="utf-8"
.venv/Scripts/python.exe scripts/memeval_preflight.py --provider openrouter
```

```powershell
# The real thing. ~52 model calls, single-digit minutes.
.venv/Scripts/python.exe scripts/evaluate_memory.py --provider openrouter `
  --output evaluations/MEMORIES/baselines/<name>.json
```

`--provider` defaults to `$LLM_PROVIDER`, else gemini. Embedding the corpus
needs `GEMINI_API_KEY`, or `JINA_API_KEY` when
`DOCUMENT_EMBEDDING_PROVIDER=jina`.

**Exit code 0 means the harness ran**, not that memory is good.

## `POSTGRES_MODE` — which store is under test

`short_term` lives in-process and `semantic` is a corpus, but `long_term` and
`episodic` are durable, and the product backs them with **two different
implementations**. The harness follows the same resolution the app does
(`cowork_agent/config.py::database_url`).

| `POSTGRES_MODE` | resolves to | backs `long_term` / `episodic` with |
|---|---|---|
| `off` | nothing | SQLite — a scratch `runs/memeval-chat.db`, created and thrown away |
| `local` | `DATABASE_URL_LOCAL` | `PostgresChatProfileRepository` + `PostgresTaskEpisodeRepository` |
| `cloud` | `DATABASE_URL_CLOUD` | the same Postgres pair |
| unset | `DATABASE_URL` if set, else SQLite | either, per the legacy variable |

`PG_TEST_URL` overrides all of it.

These are **not interchangeable and do not share bugs** — a defect has already
been found in one path that the other never had. A clean SQLite run says nothing
about Postgres. Evaluate whichever one you ship; run both if you ship both.

```powershell
# SQLite: no server, nothing to install, safe to run anywhere.
$env:POSTGRES_MODE = "off"; .venv/Scripts/python.exe scripts/evaluate_memory.py
```

```powershell
# Postgres: throwaway database only. See the safety rule below.
$env:PG_TEST_URL = "postgresql://<user>:<pw>@127.0.0.1:5432/cowork_memeval"
.venv/Scripts/python.exe scripts/evaluate_memory.py
```

## Never point this at a shared database

The harness seeds memory and then deletes it. Against a shared or production
store that is a write-and-delete on real data. `probe_environment` raises
`UnsafeTargetError` for any non-local host unless
`MEMEVAL_ALLOW_REMOTE_POSTGRES=1` is set. **Do not set it.** A refused host is
the guard working — fix the environment, never the guard.

`load_runtime_environment()` reads `.env` from the current working directory,
and this repo's `.env` carries a `DATABASE_URL_CLOUD` pointing at a production
Supabase instance. Always set `PG_TEST_URL` explicitly rather than trusting the
default resolution. A stale `.env` in a worktree has already aimed the harness
somewhere it must never write.

Use `cowork_memeval` for Postgres runs. Not `cowork` (real), and not
`cowork_mail_todo` — that is the integration suite's database, and the suite
does `DROP SCHEMA public` on it.

## What comes out

- `baselines/<name>.json` — **committed, metadata only**: ids, counts, verdicts,
  timings, model identifiers. No questions, no replies, no seed text. A unit
  test enforces this.
- `runs/<timestamp>-<probe_set_id>-detail.json` — **gitignored**, holds every
  question and every reply. This is what you read when a grade is uncertain.

Two reports are comparable only at the same `probe_set_id` and
`schema_version` — **and only if they ran against the same store.** The report
does not record which store it used, so note it yourself until that is fixed
(SPEC §15.1 item 8).
