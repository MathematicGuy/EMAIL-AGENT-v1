# Coding Agent Guidelines

Operating guide for EMAIL-AGENT-v1. Keep this file under ~80 lines; anything
that is not an always-needed constraint belongs in a linked doc. Always
install Python dependencies into a virtual environment (venv).

## Project

Cowork Agent (Email-to-Action-Plan): FastAPI service that turns unread Gmail
into structured action plans, plus multi-turn AI Chat. Without `DATABASE_URL`
the local fallback is SQLite + in-memory stores. With `DATABASE_URL` the
control plane is Supabase Postgres (runs, tasks, chat memory, identity).

Two decoupled workflows — do not merge them:
- **Email RAG** (single-turn): classify → `NO_ACTION` | `DIRECT_PLAN` |
  `RETRIEVE_RAG`. Company corpus is committed `data/extracted/*.md` only.
  Semantic store: `RAG_STORE_PROVIDER=turbovec`, else Qdrant when
  `QDRANT_ENABLED=true`, else deprecated in-repo hybrid, else null memory.
- **AI Chat** (multi-turn): session + working / declarative / episodic /
  semantic memory. Chat-native tasks start `retrieval_eligible=false`.
  No `@Email` tool in chat (ADR-004). User documents are gated
  (`USER_DOCUMENTS_ENABLED`).

## Layout (implemented)

```text
src/cowork_agent/
├── app.py                       # FastAPI composition root; entry point `mail-todo-api`
├── config.py                    # env settings loaders (Gmail, Gemini, Groq)
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
`tests/integration`, `tests/compatibility`. Providers ship fakes.

## Commands

- Install: `python -m pip install -e ".[dev]"`
- Test: `python -m pytest -q` (pythonpath/testpaths preconfigured)
- Lint: `python -m ruff check .`
- Types: `python -m mypy src` (strict)
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

Run the smallest pytest scope covering the edit (`tests/README.md`). When
`src/` changes, also run `ruff` and `mypy`. When `frontend/` changes, run
`pnpm test` and `pnpm check-types` there. Expand to the full suite only on
failure or when a shared contract (ports, schemas, migrations) changed.
Read [experience registry](docs/references/agent-experience-registry.md)
before review-heavy work.

**Context compaction:** before compacting conversation context, invoke the
`handoff` skill and save the handoff document to the OS temp directory.

## Authoritative docs

- ADRs: `tasks/adr/`
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
