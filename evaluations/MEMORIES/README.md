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

## `POSTGRES_MODE` — SQLite vs PostgreSQL Decision Matrix

`short_term` lives in-process and `semantic` is a static corpus, but `long_term` and
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

### Is PostgreSQL Mandatory?

**No.** PostgreSQL is not mandatory for running memory evaluations. SQLite (`POSTGRES_MODE=off`) is a fully-supported, zero-dependency persistence backend.

Use the decision matrix below to choose the appropriate backend for your evaluation:

| Dimension | SQLite (`POSTGRES_MODE=off`) | PostgreSQL (`POSTGRES_MODE=local` / `cloud`) |
|---|---|---|
| **Primary Scope** | Local dev loops, CI environments without Docker, prompt engineering, agent 3-arm attribution validation. | Release qualification, production database validation, PostgreSQL FTS verification. |
| **`short_term` Memory** | `InMemoryChatSessionBuffer` (in-process, identical). | `InMemoryChatSessionBuffer` (in-process, identical). |
| **`semantic` Memory** | Turbovec / `InRepoSemanticMemory` (in-process vector RAG, identical). | Turbovec / `InRepoSemanticMemory` (in-process vector RAG, identical). |
| **`long_term` Memory** | `SQLiteChatRepository` (`chat_profiles` SQLite table, JSON serialization). | `PostgresChatProfileRepository` (Postgres table, SQL-level TTL & constraints). |
| **`episodic` Retrieval** | In-memory Python term match ratio (`sum(term in searchable) / len(terms)`). | Native PostgreSQL Full-Text Search (`ts_rank_cd(search_vector, query)` with `simple` dictionary). |
| **Infrastructure Setup** | **Zero setup.** Creates and deletes `evaluations/MEMORIES/runs/memeval-chat.db` automatically. | Requires running PostgreSQL instance + migrations applied to throwaway `cowork_memeval`. |
| **Concurrency & Locks** | Completely isolated per run, zero advisory lock deadlocks. | Subject to `schema_migrations` advisory locks; killed runs may leave locks held. |
| **Safety Profile** | Isolated scratch file in gitignored `runs/`; cannot overwrite real data. | Guarded by `is_local_postgres` and `looks_throwaway`; remote databases strictly blocked. |

### When SQLite Suffices
- **3-Arm Attribution & Masking Verification**: Testing whether the LLM responds to memory presence, ablation, and empty control baseline ($P, F, F$).
- **Prompt & Context Engineering**: Verifying that system prompt injection, user profile formatting, and recent turn windows behave correctly.
- **Fast Offline / CI Execution**: Running the evaluation harness in lightweight CI runners without spinning up Docker services.
- **Episodic Lifecycle Gating**: Validating two-stage approval (`retrieval_eligible=False` $\to$ `True`) and Vietnamese episodic cue routing.

### When PostgreSQL is Required
- **Pre-Release Production Parity**: Proving that the exact SQL repositories (`PostgresChatProfileRepository`, `PostgresTaskEpisodeRepository`) perform under target database conditions.
- **PostgreSQL Full-Text Search Tuning**: Evaluating PostgreSQL `tsquery` stemming, tokenization, or cover density ranking (`ts_rank_cd`) thresholds.
- **Database-Level Expiration & Constraints**: Validating SQL-level `expires_at > now()` filtering and PostgreSQL unique constraint handling.
- **Connection Pool & Async Driver Validation**: Exercising `psycopg` / `psycopg_pool` async query handling under concurrency.

---

## Execution Commands by Backend

### 1. SQLite Mode (Recommended for Local Dev & Quick Checks)

```powershell
# Pre-flight check (proves dependencies answer without dialing PostgreSQL)
$env:POSTGRES_MODE="off"; $env:PYTHONPATH="src"; $env:PYTHONIOENCODING="utf-8"
.venv/Scripts/python.exe scripts/memeval_preflight.py --provider openrouter --no-live
```

```powershell
# Live evaluation with SQLite scratch store
$env:POSTGRES_MODE="off"; $env:PYTHONPATH="src"; $env:PYTHONIOENCODING="utf-8"
.venv/Scripts/python.exe scripts/evaluate_memory.py --provider openrouter `
  --output evaluations/MEMORIES/baselines/<name>-sqlite.json
```

### 2. PostgreSQL Mode (Production Store Parity)

```powershell
# Pre-flight check against local throwaway PostgreSQL
$env:PG_TEST_URL="postgresql://cowork:cowork_dev_only@127.0.0.1:5432/cowork_memeval"
$env:PYTHONPATH="src"; $env:PYTHONIOENCODING="utf-8"
.venv/Scripts/python.exe scripts/memeval_preflight.py --provider openrouter
```

```powershell
# Live evaluation against local throwaway PostgreSQL
$env:PG_TEST_URL="postgresql://cowork:cowork_dev_only@127.0.0.1:5432/cowork_memeval"
$env:PYTHONPATH="src"; $env:PYTHONIOENCODING="utf-8"
.venv/Scripts/python.exe scripts/evaluate_memory.py --provider openrouter `
  --output evaluations/MEMORIES/baselines/<name>-postgres.json
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

