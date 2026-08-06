# Coding Agent Guidelines

Operating guide for EMAIL-AGENT-v1. Keep this file under ~80 lines; anything
that is not an always-needed constraint belongs in a linked doc.

## Project

Cowork Agent (Email-to-Action-Plan): FastAPI app that converts unread Gmail
into structured action plans via one LLM provider (Gemini or Groq). Local MVP:
SQLite for mailbox connections, in-memory run/result stores, in-process dispatch.

## Layout (implemented)

```text
src/cowork_agent/
├── app.py                       # FastAPI composition root; entry point `mail-todo-api`
├── config.py                    # env settings loaders (Gmail, Gemini, Groq)
├── api/                         # HTTP handlers / response serialization
├── domain/models.py             # pure domain models (no framework imports)
├── features/email_action_plan/  # workflow, policies, ports, schemas
├── gui/app.py                   # Streamlit test GUI
├── integrations/gmail/          # OAuth, Gmail adapter, deterministic fakes
├── integrations/llm/            # Gemini/Groq providers, fakes
├── orchestration/local.py       # in-process local orchestration
└── persistence/                 # SQLite mailbox-connection repo; migrations/
```

Dependency direction: `domain` ← `features` ← `integrations` / `orchestration`
/ `persistence` ← `app`. Tests mirror this under `tests/unit` and
`tests/integration`; providers ship fakes for deterministic local runs.

## Commands

- Install: `python -m pip install -e ".[dev,gui]"`
- Test: `python -m pytest -q` (pythonpath/testpaths preconfigured)
- Lint: `python -m ruff check .`
- Types: `python -m mypy src` (strict)
- Run API: `mail-todo-api` (host/port via `APP_HOST` / `APP_PORT`)
- GUI: `python scripts/run_gui.py`

## Verification rule

Run the smallest pytest scope covering the edit (e.g.
`python -m pytest tests/unit/features -q`); when `src/` changed, also run
`ruff check` and `mypy`. Expand to the full suite only on failure or when a
shared contract (ports, schemas, migrations) changed. See the
[experience registry](docs/references/agent-experience-registry.md) before
review-heavy work.

**Context compaction:** before compacting the conversation context at any
point, always invoke the `handoff` skill first and save the handoff document
to the OS temp directory so continuity is preserved.

## Authoritative docs

- Architecture decisions: `docs/adr/ADR-001..003`
- Target architecture: `docs/architectures/TARGET-ARCHITECTURE.md`
- Current-vs-target gap analysis and migration milestones:
  `docs/master-comparison.md`
- Product requirements: `docs/PRD-v1-Core-Email-and-RAG.md` and
  `docs/PRD-v2-Memory-Extension.md`

## Non-negotiable invariants

1. Raw email bodies and attachment content are never persisted or logged;
   they exist only as transient in-run state.
2. Gmail access stays read-only: only the `gmail.readonly` scope.
3. Attachment processing is out of scope (ADR-003): record presence only.
4. Target-state components (PostgreSQL, durable queue/DLQ, RAG, four-type
   memory system) are not implemented yet; do not scaffold them unless the
   request explicitly cites a `master-comparison.md` milestone.
