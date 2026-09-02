# Test Routing Index

A map for picking the **smallest** test route that covers a change, and a
registry of which file owns which invariant so no duplicate tests are written.

Always `uv run pytest`. Avoid passing `--dist loadfile` explicitly so pytest uses the repository's optimized `--dist loadgroup` scheduler.

**Whole suite: `uv run pytest -q` -> approximately 15–18 s; the count varies with optional integrations.** Defaults: 4 xdist
workers (`--dist loadgroup`), `-m 'not live'`, `--strict-markers`.
Detailed optimization notes: [`docs/references/test-optimization/`](../docs/references/test-optimization/test-optimization.md).

---

## 1. Route Index

Pick the narrowest row containing your change. Times are serial (`-p no:xdist`) --
what one route costs alone; the whole-suite row is parallel.

| # | Route | Tests | Serial | Covers |
|---|---|---|---|---|
| R1 | `tests/unit/domain` | 76 | 0.7 s | Frozen contracts, enums, validation rules. No I/O. |
| R2 | `tests/unit/features` | 563 | 2.1 s | Chat controller/memory/intent + email action-plan mapping. Fakes only. |
| R3 | `tests/unit/integrations/rag` | 83 | 3.5 s | BM25, RRF fusion, reranker, query guard, embedding key rotation, in-repo memory. |
| R4 | `tests/unit/integrations/llm` | 103 | 1.4 s | Prompt assembly, parsing, key rotation, classifiers, OpenRouter last-resort. |
| R5 | `tests/unit/integrations/gmail tests/unit/integrations/mailbox tests/unit/integrations/outlook` | 49 | 0.7 s | Gmail/Microsoft OAuth, PKCE, token cipher, provider router, mailbox adapters. |
| R6 | `tests/unit/integrations` | 366 | 5.2 s | R3+R4+R5 plus bootstrap, Supabase. |
| R7 | `tests/unit/persistence` | 80 | 1.8 s | Repository logic against fakes. |
| R8 | `tests/unit/orchestration` | 19 | 1.7 s | Workers, pollers, recovery. |
| R9 | `tests/unit/scripts` | 100 | 4.2 s | `scripts/*.py` eval CLIs. |
| R10 | `tests/unit/fixtures` | 23 | 1.1 s | Golden-fixture schema and corpus-label validation. |
| R11 | `tests/integration/api` | 78 | 6.4 s | FastAPI via in-process ASGI transport. |
| R12 | `tests/integration/persistence` | 9 | 1.0 s | Real PostgreSQL (skips without server; `pg-control-plane` xdist group). |
| R13 | `tests/integration/email_action_plan` | 38 | 2.8 s | Provider-neutral mailbox -> classify -> plan -> persist, end to end on fakes. |
| R14 | `tests/integration` | 117 | 7.8 s | R11+R12+R13 plus corpus-backed workflow. |
| R15 | `tests/unit` | 1369 | 9.8 s | Everything above the integration line. |
| R16 | `tests/unit --ignore=tests/unit/scripts` | 1269 | 6.8 s | R15 minus eval CLIs (default during regular development). |
| — | *(everything)* | 1486 | **12 s parallel** | `uv run pytest -q` |

### Source -> Route Mapping

| Edited under `src/cowork_agent/` | Run |
|---|---|
| `domain/` | R1, then R2 |
| `features/ai_chat/` | R2 |
| `features/email_action_plan/` | R2 + R13 |
| `integrations/rag/` | R3 (+ R6 if `bootstrap.py` or `project_documents.py`) |
| `integrations/knowledge_ingestion/` | `tests/unit/integrations/knowledge_ingestion`, then `test_rag.py` |
| `integrations/llm/` | R4 |
| `integrations/gmail/` | R5 + R13 |
| `integrations/google_calendar/` | `tests/unit/integrations/google_calendar` + `tests/unit/api` + `tests/unit/features/ai_chat/test_calendar_binder.py` + `tests/unit/features/ai_chat/test_agenda_tool.py` |
| `integrations/mailbox/`, `integrations/outlook/` | R5 + R11 + R13 |
| `persistence/` | R7 + R12 |
| `orchestration/` | R8 |
| `app.py`, API routes | R11 (+ `tests/unit/api` for router-level invariants) |
| `identity.py`, session/cookie | R11 + `tests/unit/test_identity.py` |
| `scripts/*.py` | R9 |
| `data/extracted/*.md` (corpus) | R10 + R3 |

---

## 2. Markers

| Marker | Meaning | Default |
|---|---|---|
| `live` | Needs real external service/credentials (e.g. `test_e2e_frontend_api.py`). | **Deselected.** `-m live` to opt in. |
| `slow` | >1 s wall clock on its own. | Selected. |
| `serial` | Must run serially (auto-assigned to `xdist_group("serial")`). | Selected. |
| `xdist_group` | Pin tests sharing destructive state (e.g. `pg-control-plane`) to one worker. | Selected. |

---

## 3. Invariant Ownership

Before writing a test, check if its invariant is already owned.

| Invariant | Owned by | Do not re-assert in |
|---|---|---|
| Legacy `/result` JSON schema, `nextActions` slice, empty-state message | `unit/features/email_action_plan/test_compat_mapper.py` | API-level tests |
| `processedEmails` is development-only | `integration/api/test_principal_boundary.py` | — |
| Run creation idempotency `(user, Idempotency-Key)` | `integration/email_action_plan/test_workflow.py` | API tests |
| Persisted tasks survive replayed run | `integration/email_action_plan/test_workflow.py` | — |
| Postgres migrations idempotent | `integration/persistence/test_postgres_repositories.py` | — |
| No raw email body reaches API responses | `integration/api/test_principal_boundary.py` | workflow/repository tests |
| No raw email body reaches chat memory | `unit/domain/test_chat_contracts.py` | gateway tests |
| Retrieval ordering, `top_k`, `min_score`, timeout status | `unit/integrations/rag/test_rag.py` + `test_turbovec_memory.py` | integration tests |
| Binary `document_date` harvest (PDF/DOCX metadata) | `unit/integrations/knowledge_ingestion/test_date_harvest.py` | service tests |
| Company RAG pre-filter (`document_ids`/`years`/`months`) | `unit/integrations/rag/test_rag.py` | hybrid/turbovec |
| Retrieval over committed corpus + degrade-to-null | `unit/integrations/test_bootstrap.py` | — |
| Jina embed key rotation (429/403) | `unit/integrations/rag/test_embeddings.py` | bootstrap / hybrid |
| Project-document ACL & cross-project isolation | `unit/integrations/test_project_documents_hybrid.py` | orchestration/API tests |
| Eval report is metadata-only (no query/chunk text) | `unit/scripts/` | — |
| OAuth grant identity binding | `unit/integrations/gmail/test_provider.py` + `unit/integrations/outlook/test_outlook_provider.py` | — |
| Broken `SSL_CERT_FILE` isolation | `tests/conftest.py` | — |
| GET `/sessions/{id}/messages` content redaction | `integration/api/test_chat_api.py` | frontend mapper |
| Chat lifecycle idempotency | `unit/features/ai_chat/test_controller.py` + `unit/persistence/test_chat_history_migration.py` | frontend tests |
| Outbound non-loopback socket guard | `tests/unit/test_network_guard.py` | — |
| Offline RAG pinning on app boot | `tests/conftest.py` | API/workflow tests |
| Source tree resolution for `cowork_agent` | `unit/test_xdist_harness.py` | — |
| Postgres pre-flight safe fallback | `unit/test_pg_probe.py` | persistence modules |
| Embedding key rotation pacing | `unit/integrations/rag/test_embeddings.py` | — |
| OpenRouter fallback to Google Gemini | `unit/integrations/llm/test_last_resort.py` + `test_openrouter.py` | chat controllers |
| Evaluation API recursive error/content redaction | `integration/api/test_evaluation_jobs_api.py` | job service, plug-ins, frontend |
| Evaluation credential alias secrecy and exclusive lease lifecycle | `unit/features/batch_evaluation/test_credentials.py` + `unit/integrations/llm/test_evaluation_mistral.py` | API, runner, smoke CLI |
| Evaluation SQLite shard isolation | `unit/features/batch_evaluation/plugins/test_memory_eval.py` | runner, API, scripts |
| Memory baseline metadata privacy | `unit/scripts/test_evaluate_memory.py` | report builders and API |
| Mistral key-independence smoke metadata and 429 gate | `unit/scripts/test_smoke_test_mistral_evaluation_keys.py` | provider/lease unit tests |
| Report filename rule (traversal, reserved names, slug fallback) | `unit/domain/test_report_artifacts.py` | store, route and chat-controller tests |
| Report store stays inside its injected root | `unit/persistence/test_report_artifact_store.py` | API tests |
| `runtime(request)` returns the composed `CoworkRuntime` value | `unit/test_composition.py` | API tests |
| Per-user calendar grant: whose token a turn resolves, and the refusal when there is none (J1, J2) | `unit/features/ai_chat/test_calendar_binder.py` | controller and tool tests |
| A chained calendar consent never costs the mail connection or mints a session (J4, J5) | `unit/api/test_calendar_router.py` | mailbox API tests |
| Calendar grant storage, scope guard, and revocation (J1, J3, J6, J7) | `unit/integrations/google_calendar/test_calendar_oauth.py` | repository and composition tests |
| Which messages determine an hour and which do not (PROGRESS.md F5/F7) | `unit/features/ai_chat/test_ambiguous_hour.py` | calendar tool and QA tier tests, which assert only that the guard is *reached* |
| What the routing launch gate measures, tool axis included | `unit/scripts/test_evaluate_chat_routing.py` | fixture and resolver tests, which own the labels and the narrowing |
| Reading a calendar window is not writing one: the past is allowed, and a day is a day in the calendar's zone (PROGRESS.md F9) | `unit/features/ai_chat/test_agenda_tool.py` | `test_calendar_tool.py`, which owns the write-side bounds |
| Settings parsing never reads dotenv; executable boundaries own loading | `unit/test_config.py` | provider, adapter and route tests |

### Critical Invariants
- **`HashingEmbedder` carries no semantics**: Assert counts/scores/thresholds, never semantic rank.
- **`tenant_id` removed**: Do not reintroduce to single-user domain/email/retrieval contracts.

---

## 4. Rules for Adding & Pruning Tests

1. **One invariant, one owner**: Add cases to existing owning files rather than creating redundant layer tests.
2. **Lowest layer possible**: Prefer pure unit tests over ASGI app-boot integration tests.
3. **No subprocesses for CLIs**: Use `tests/unit/scripts/cli_harness.py::run_cli` (in-process `main(argv)`).
4. **Offline by default**: Non-loopback sockets raise `RuntimeError`. Mock external services at the seam.
5. **No real sleeps**: Fake delays with fixtures (e.g. `slept` in `test_embeddings.py`).
6. **Missing dependency skips loudly**: Print instructions instead of erroring.

---

## 5. Verification & Pre-PR Gates

Before submitting changes or opening a PR to `main`, run and pass the full CI + E2E gate suite:

```bash
# 1. Backend CI checks
uv run ruff check . && uv run mypy src && uv run pytest -q

# 2. Frontend CI checks
cd frontend && pnpm lint && pnpm check-types && pnpm test && pnpm build

# 3. Playwright E2E tests
pnpm run test:e2e
```
