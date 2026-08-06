# Current Architecture Document Path-Update Handoff

**Status:** Code reorganization complete; architecture documents intentionally not rewritten in this change.  
**Audience:** Next documentation agent or maintainer.  
**Primary scope:** Every Markdown file in `docs/architectures/current-architectures/`.  
**Source baseline:** `docs/references/cowork-project-structure-spec.md` plus live source under `src/cowork_agent/`.

## Outcome to deliver

Update current-architecture documents so every source path, module name, package-tree statement, and line citation resolves against the reorganized code. Preserve current-runtime claims. Do not turn target requirements into implementation claims.

## Reorganization boundary

The package changed from `mail_todo` to `cowork_agent`. Existing behavior and public HTTP routes remain compatible. The FastAPI composition root moved to `cowork_agent.app`. Current API and Streamlit adapters remain under `api/` and `gui/` because they are live code, even though the locked target tree does not enumerate presentation adapters.

No RAG implementation, four-type memory system, scheduler, retry engine, DLQ, classifier/router split, or new persistence behavior was added. Do not mark those target acceptance criteria complete.

## Path map

| Previous path | Current path | Action / ownership |
|---|---|---|
| `src/mail_todo/api/server.py` | `src/cowork_agent/app.py` | Moved; FastAPI composition root and `main()` |
| `src/mail_todo/api/handlers.py` | `src/cowork_agent/api/handlers.py` | Moved; framework-neutral handlers |
| `src/mail_todo/application/services.py` | `src/cowork_agent/features/email_action_plan/workflow.py` | Moved; current digest workflow |
| `src/mail_todo/application/contracts.py` | `src/cowork_agent/features/email_action_plan/schemas.py` | Moved; extraction and workflow schemas |
| `src/mail_todo/application/ports.py` | `src/cowork_agent/features/email_action_plan/ports.py` | Moved; current workflow ports |
| `src/mail_todo/domain/models.py` | `src/cowork_agent/domain/models.py` | Moved; shared domain models |
| `src/mail_todo/domain/policies.py` | `src/cowork_agent/features/email_action_plan/policies.py` | Moved; feature-owned policy functions |
| `src/mail_todo/infrastructure/config.py` | `src/cowork_agent/config.py` | Moved; runtime settings |
| `src/mail_todo/infrastructure/gmail.py` | `src/cowork_agent/integrations/gmail/provider.py` | Moved; Gmail OAuth/API/provider implementation |
| `src/mail_todo/infrastructure/security.py` | `src/cowork_agent/integrations/gmail/auth.py` | Moved; OAuth state and token encryption |
| `src/mail_todo/infrastructure/connections.py` | `src/cowork_agent/persistence/repositories/mailbox_connections.py` | Moved; SQLite mailbox connection repository |
| `src/mail_todo/infrastructure/gemini.py` | `src/cowork_agent/integrations/llm/providers/gemini.py` | Moved; Gemini adapter and shared extraction helpers |
| `src/mail_todo/infrastructure/groq.py` | `src/cowork_agent/integrations/llm/providers/groq.py` | Moved; Groq adapter |
| `src/mail_todo/infrastructure/memory.py` | Split across four paths below | Split by ownership; behavior preserved |
| `migrations/001_mail_todo.sql` | `src/cowork_agent/persistence/migrations/001_mail_todo.sql` | Moved; still not wired by `create_app()` |
| `migrations/001_mail_todo.down.sql` | `src/cowork_agent/persistence/migrations/001_mail_todo.down.sql` | Moved rollback DDL |

### Former `infrastructure/memory.py` ownership split

| Symbols | Current path |
|---|---|
| `InMemoryRunRepository`, `InMemoryResultRepository` | `src/cowork_agent/persistence/repositories/local.py` |
| `InMemoryQueue`, `InMemoryOutbox` | `src/cowork_agent/orchestration/local.py` |
| `FakeMailbox`, `SafeTextAttachmentExtractor` | `src/cowork_agent/integrations/gmail/fakes.py` |
| `FakeActionExtractor` | `src/cowork_agent/integrations/llm/fakes.py` |

## Public and runtime identifiers that did not change

- CLI command remains `mail-todo-api`; its entry point is now `cowork_agent.app:main`.
- HTTP namespace remains `/v1/mail-todo/...`.
- Default SQLite file remains `.data/mail_todo.db`.
- Provider, workflow, repository, and domain symbol names remain unchanged unless listed in the split table.
- RAG remains absent from live source.
- PostgreSQL migration remains unwired by the FastAPI composition root.

## Documents requiring updates

1. `current-overall-architecture.md`
   - Replace package/module names and source paths.
   - Update the live package tree from `api/application/domain/gui/infrastructure` to the new feature/integration/persistence/orchestration layout plus retained `api/gui` adapters.
   - Replace the single `infrastructure/memory.py` citation with symbol-specific split paths.
   - Keep runtime behavior claims unchanged unless live code disproves them.
2. `current-email-architecture.md`
   - Update CLI entry point, composition root, workflow, Gmail, auth, repository, local-adapter, and provider citations.
   - Keep `/v1/mail-todo` endpoints and `.data/mail_todo.db` wording.
3. `current-rag-architecture.md`
   - Update package-tree and source citations.
   - Preserve the conclusion that live source has no RAG implementation.
4. `current-architecture-review.md`
   - Re-run citation-resolution checks against new paths.
   - Update the migration path and any test-file command paths.
   - Preserve historical review results as historical evidence; label new verification separately.

## Citation update method

Do not perform blind path-only replacement for citations. Files moved and import blocks changed, so line numbers may differ.

For each citation:

1. Find the referenced symbol in live `src/cowork_agent/` code.
2. Confirm the cited claim still matches runtime wiring from `cowork_agent.app:create_app`.
3. Replace the path and recalculate exact line numbers.
4. If one old citation points into former `infrastructure/memory.py`, select the new ownership-specific file from the split table.
5. Run a final check that every `src/...py:line` and migration citation resolves.

## Required safeguards

- Treat `docs/references/cowork-project-structure-spec.md` as target authority, not proof of current implementation.
- Treat `src/cowork_agent/app.py` composition as authority for current runtime wiring.
- Do not claim target-only folders or empty strategy files exist.
- Do not claim tests pass unless run after this reorganization.
- Do not change architecture conclusions merely because paths moved.
- Preserve the distinction between SQLite runtime storage and unwired PostgreSQL DDL.

## Known stale documents outside primary scope

These files also contain old `mail_todo` or `src/mail_todo` references and need a separate documentation pass:

- `README.md`
- `docs/technical_spec.md`
- `docs/references/ARCHITECHTURE.md`
- `docs/references/rag_mail_pipeline_explanation.md`
- `docs/architectures/master-comparison.md`

Do not update them incidentally while handling the primary four current-architecture documents unless scope is explicitly expanded.

## Completion checklist

- [ ] All four current-architecture documents use `src/cowork_agent/...` paths.
- [ ] All module-qualified names use `cowork_agent...` where package names are intended.
- [ ] Every line citation resolves against live files.
- [ ] Former `infrastructure/memory.py` citations map to correct split owners.
- [ ] CLI and HTTP compatibility names remain documented correctly.
- [ ] RAG absence and unwired PostgreSQL status remain accurate.
- [ ] New verification evidence is recorded separately from pre-reorganization evidence.
