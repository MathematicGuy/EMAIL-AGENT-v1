# Test Routing Index

A map for picking the **smallest** test route that covers a change, and a
registry of which file owns which invariant so no duplicate tests are written.

Always `uv run pytest`.

**Whole suite: `uv run pytest -q` -> ~14.4 s, 1838 passed.** Defaults: 4 xdist
workers (`--dist loadgroup`), `-m 'not live'`, `--strict-markers`.
Detailed optimization notes: [`docs/references/test-optimization/`](../docs/references/test-optimization/test-optimization.md).

---

## 1. Route Index

Pick the narrowest row containing your change. Times measured with 4 workers (`-n 4 --dist loadfile`).

| # | Route | Tests | Time (-n 4) | Covers |
|---|---|---|---|---|
| R1 | `tests/unit/domain` | 179 | 2.7 s | Frozen contracts, enums, validation rules. No I/O. |
| R2 | `tests/unit/features` | 844 | 5.0 s | Chat controller/memory/intent + email action-plan mapping. Fakes only. |
| R3 | `tests/unit/integrations/rag` | 102 | 5.3 s | BM25, RRF fusion, reranker, query guard, embedding key rotation, in-repo memory. |
| R4 | `tests/unit/integrations/llm` | 76 | 4.1 s | Prompt assembly, parsing, key rotation, classifiers, OpenRouter last-resort. |
| R5 | `tests/unit/integrations/gmail` | 40 | 2.8 s | OAuth/PKCE, token cipher, mailbox adapter. |
| R6 | `tests/unit/integrations` | 348 | 5.4 s | R3+R4+R5 plus bootstrap, Supabase. |
| R7 | `tests/unit/persistence` | 37 | 4.1 s | Repository logic against fakes. |
| R8 | `tests/unit/orchestration` | 19 | 4.0 s | Workers, pollers, recovery. |
| R9 | `tests/unit/scripts` | 184 | 8.9 s | `scripts/*.py` eval CLIs. |
| R10 | `tests/unit/fixtures` | 33 | 4.3 s | Golden-fixture schema and corpus-label validation. |
| R11 | `tests/integration/api` | 33 | 9.7 s | FastAPI via in-process ASGI transport. |
| R12 | `tests/integration/persistence` | 9 | 3.4 s | Real PostgreSQL (skips without server; `pg-control-plane` xdist group). |
| R13 | `tests/integration/email_action_plan` | 48 | 4.6 s | Gmail -> classify -> plan -> persist, end to end on fakes. |
| R14 | `tests/integration` | 91 | 11.5 s | R11+R12+R13 plus corpus-backed workflow. |
| R15 | `tests/unit` | 1761 | 16.0 s | Everything above the integration line. |
| R16 | `tests/unit --ignore=tests/unit/scripts` | 1577 | 11.6 s | R15 minus eval CLIs (default during regular development). |
| — | *(everything)* | 1838 | **14.4 s** | `uv run pytest -q` |

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
| `persistence/` | R7 + R12 |
| `orchestration/` | R8 |
| `app.py`, API routes | R11 |
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
| OAuth grant identity binding | `unit/integrations/gmail/test_provider.py` | — |
| Broken `SSL_CERT_FILE` isolation | `tests/conftest.py` | — |
| GET `/sessions/{id}/messages` content redaction | `integration/api/test_chat_api.py` | frontend mapper |
| Chat lifecycle idempotency | `unit/features/ai_chat/test_controller.py` + `unit/persistence/test_chat_history_migration.py` | frontend tests |
| Outbound non-loopback socket guard | `tests/unit/test_network_guard.py` | — |
| Offline RAG pinning on app boot | `tests/conftest.py` | API/workflow tests |
| Source tree resolution for `cowork_agent` | `unit/test_xdist_harness.py` | — |
| Postgres pre-flight safe fallback | `unit/test_pg_probe.py` | persistence modules |
| Embedding key rotation pacing | `unit/integrations/rag/test_embeddings.py` | — |
| OpenRouter fallback to Google Gemini | `unit/integrations/llm/test_last_resort.py` + `test_openrouter.py` | chat controllers |

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

## 5. Verification & Fast Gates

Before submitting changes, run:
```bash
uv run pytest -q ; uv run ruff check . ; uv run mypy src
```
