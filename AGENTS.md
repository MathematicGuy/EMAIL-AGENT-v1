# Coding Agent Guidelines

Operating guide for EMAIL-AGENT-v1. Keep this file under ~80 lines; anything
that is not an always-needed constraint belongs in a linked doc. Always
install Python dependencies into a virtual environment (venv).

## Project

Cowork Agent (Email-to-Action-Plan): FastAPI service that turns unread Gmail
into structured action plans, plus multi-turn AI Chat. `POSTGRES_MODE=local`
uses Docker Postgres; `cloud` uses hosted Supabase Postgres; `off` (or no
`DATABASE_URL`) is SQLite + in-memory. Flip the flag in `.env`; local and
cloud are separate databases.

Two decoupled workflows — do not merge them:
- **Email RAG** (single-turn): classify → `NO_ACTION` | `DIRECT_PLAN` |
  `RETRIEVE_RAG`. Company corpus is committed `data/extracted/*.md` only.
  Semantic store: `RAG_STORE_PROVIDER=turbovec` (Hybrid of dense + BM25 +
  RRF for Email RAG and Chat Type 4). Unknown, retired (`qdrant`), or
  failed providers degrade to null memory.
- **AI Chat** (multi-turn): session + working / declarative / episodic /
  semantic memory. Chat-native tasks start `retrieval_eligible=false`.
  No `@Email` tool in chat (ADR-004). User documents are gated
  (`USER_DOCUMENTS_ENABLED`).

## Layout (implemented)

```text
src/cowork_agent/
├── app.py                       # FastAPI composition root; entry point `mail-todo-api`
├── config.py                    # env settings loaders (Gmail, Gemini, Groq)
├── prompting.py                 # shared untrusted/retrieved block delimiters for prompts
├── api/                         # HTTP handlers / response serialization
├── domain/models.py             # pure domain models (no framework imports)
├── features/email_action_plan/  # workflow, policies, ports, schemas
├── integrations/gmail/          # OAuth, Gmail adapter, deterministic fakes
├── integrations/llm/            # Gemini/Groq providers, fakes
├── integrations/rag/            # local hybrid semantic retrieval (V1-M3)
├── orchestration/local.py       # in-process local orchestration
└── persistence/                 # SQLite mailbox-connection repo; migrations/
```

Dependency direction: `domain` ← `features` ← `integrations` /
`orchestration` / `persistence` ← `app`. Tests: `tests/unit`,
`tests/integration`. Providers ship fakes.

## Commands

Always `uv run` — plain `python -m` picks up the Anaconda interpreter on this
machine and fails with unrelated `ssl` errors.

- Install: `uv sync --extra dev --extra postgres` (drop `postgres` and the
  `tests/integration/persistence` route skips)
- Test: `uv run pytest -q` (~18 s, 1596 passed; defaults in `pyproject.toml`)
- Lint: `uv run ruff check .`
- Types: `uv run mypy src` (strict)
- Run API: `mail-todo-api` (host/port via `APP_HOST` / `APP_PORT`)
- React frontend: `cd frontend; pnpm install; pnpm dev`

Frontend (`frontend/`): `pnpm install` · `pnpm dev` · `pnpm test` ·
`pnpm lint` · `pnpm check-types`

## Boundaries

- Never commit `.env` or secrets. Never put secrets in `VITE_*`.
- Gmail is `gmail.readonly`. Raw email/attachments are transient; never
  persist them and never ingest them into company RAG or long-term memory.
- Ask before changing SQL migrations or RAG bootstrap fallbacks.
- System architecture: Level 1 system architecture is documented in `docs/architectures/current-architectures/`.

## Verification

`tests/README.md` is the harness, not prose. Read it before running or writing
any test: **§1** maps each `src/` path to the narrowest route (R1–R16) with its
measured cost; **§3** names the one file that owns each cross-cutting invariant
— check it before writing a test, because re-asserting an owned invariant at
another layer is a deletion candidate, not coverage; **§4** has the pruning
checklist.

Widen a level only when the narrow route is green. Run the full suite once at
the end, or immediately when a shared contract (ports, schemas, migrations)
changed. When `src/` changes, also run `ruff` and `mypy`. When `frontend/`
changes, run `pnpm test` and `pnpm check-types` there.

A yellow `DESELECTED - NOT VERIFIED BY THIS RUN` banner ends every run, naming
what `-m 'not live'` dropped. Green above that banner is not a verified suite.

Read [experience registry](docs/references/agent-experience-registry.md)
before review-heavy work or multi-file implementation. After a written
plan exists, fan out file-disjoint implementer subagents; the parent
keeps spec, scope, and the Definition of Done.

**Context compaction:** before compacting conversation context, invoke the
`handoff` skill and save the handoff document to the OS temp directory.

## Authoritative docs

- ADRs: `tasks/adr/` (local control-plane runtime: ADR-010)
- Target architecture: `docs/architectures/TARGET-ARCHITECTURE.md`
- Email RAG runtime: `docs/evaluations/RETRIEVAL/EMAIL-RAG-STATUS.md`
- PRDs: `tasks/prds/PRD-v1-Core-Email-and-RAG.md`, `PRD-v2-Memory-Extension.md`
- Frontend: `frontend/README.md`, `docs/SPEC-Demo-Frontend.md`

## Agent skills

### Issue tracker

Linear team Heval1st (`HEV-` issues), via the Linear MCP tools. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five roles: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: root `CONTEXT.md` plus ADRs in `tasks/adr/`. See `docs/agents/domain.md`.
