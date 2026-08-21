# Test pruning ledger — dead coverage / stale contracts (§4 condition 2)

Scratch ledger only. No `tests/**` or `src/**` edits in this task.

## Visited start-list

| Item | Result |
|---|---|
| `tenant_id` on `KnowledgeChunk` | **No test hits** constructing/asserting the field. `src/.../knowledge_base.py` `KnowledgeChunk` has no `tenant_id`. |
| `tenant_id` on `SemanticRetrievalRequest` | **No test hits**. `src/.../target_contracts.py` request has no `tenant_id`. |
| `tenant_id` on `EphemeralEmailEnvelope` | **No test hits**. Envelope model has no `tenant_id`. |
| `tenant_id` on `GenerationContext` | **No test hits**. Email `schemas.GenerationContext` and chat `generation_context.GenerationContext` have no `tenant_id`. |
| `tenant_id` on `load_corpus` | **2 test call sites** still pass `tenant_id=` (rows D-001, D-002). Src signature still accepts unused kwarg → **`fix call`**, not delete. **Ask before removing `knowledge_base.load_corpus` signature later.** |
| `tenant_id` on `VerifiedPrincipal` / `ChatMemoryScope` / memeval isolation / task persistence / `ProjectDocumentQuery` | **keep** — field still present in `src/` (`identity.py`, `_chat_contracts_memory.py`, repos, `project_documents.py`). README already says so. Not §4.2. |
| `BM25Index.search(..., tenant_id=)` | Unused kwarg in `src/.../rag/bm25.py`; **no test call sites** pass it. No ledger fix row. Same optional later-signature ask as `load_corpus` (src-only). |
| Retired `qdrant` | **1 executable hit**: owned degrade-to-null in `test_bootstrap.py` (D-003) → **keep**. Docstring-only mention in `test_project_documents_hybrid.py` (pre-ADR-008 history) — purpose is project ACL/isolation, not the retired store → **keep** (no delete). Src: `_RETIRED_STORE_PROVIDERS = {"qdrant"}` + degrade path only. |
| `except Exception` / bare `except:` / `except BaseException` | **none found** in `tests/**/*.py` (re-checked). Visited; not a delete. Narrow `except` of specific types (`HTTPException`, `ValueError`, `ImportError`, …) remain and are out of scope. |
| JSON keys / model fields failing `src/` name grep | Start-list scoped scan of forbidden retrieval/email contracts: **no stale key assertions**. Legitimate `tenant_id` JSON on chat scope / negative eval metadata exclusion still match `src/`. Retired `@Email` / `tool_choices` rejection tests assert absence/rejection of a retired API surface that `src/` still rejects → **keep**. |
| Task 2 empty-coverage map (`test-pruning-ledger-coverage.md` §2) | **Present.** 240 empty-context nodeids reviewed as a cohort. Absence of `cowork_agent` hits is **not** §4 condition 2. **keep** unless a named field/path is also gone from `src/` (none of those 240 were also stale-contract hits). **Do not** copy all 240 as delete rows. Summary: D-004. Protected files in that list stay protected. |

Protected surfaces never proposed `delete`: `test_network_guard.py`, `conftest.py`, `test_xdist_harness.py`, `test_pg_probe.py`, `live`, golden XPASS `q-006`/`q-014`, `tests/integration/persistence/*`.

## Ledger

| id | nodeid | §4 | src evidence (path + finding) | extra fact? | proposed action | notes |
|---|---|---|---|---|---|---|
| D-001 | `tests/integration/test_knowledge_ingestion_to_rag.py::test_ingested_docx_markdown_is_loadable_by_rag` | 2 | `src/cowork_agent/integrations/rag/knowledge_base.py:79` — `load_corpus(corpus_dir, *, tenant_id: str \| None = None)`; body never reads `tenant_id`. README §3: do not add `tenant_id` to `load_corpus`. | yes — proves ingested DOCX Markdown is loadable by RAG | `fix call` | Drop `tenant_id="local"` from the call. **Do not delete.** Ask before removing `knowledge_base.load_corpus` signature later (also still passed from `bootstrap.py` / `app.py`). |
| D-002 | `tests/integration/email_action_plan/test_workflow.py::test_retrieve_rag_workflow_runs_end_to_end_over_in_repo_memory` | 2 | Same as D-001: unused `tenant_id` kwarg on `load_corpus`. Call uses `tenant_id=LOCAL_TENANT_ID`. | yes — RETRIEVE_RAG e2e over committed corpus + `InRepoSemanticMemory` | `fix call` | Drop `tenant_id=LOCAL_TENANT_ID` from `load_corpus(...)`. **Do not delete.** Same optional src-signature ask as D-001. |
| D-003 | `tests/unit/integrations/test_bootstrap.py::test_retired_qdrant_provider_degrades_to_null` | — (owned; not stale) | `src/cowork_agent/integrations/rag/bootstrap.py:38` `_RETIRED_STORE_PROVIDERS = frozenset({"qdrant"})`; degrade to `NullSemanticMemory`. README §3 owns degrade-to-null here. | yes — sole retired-provider contract | `keep` | Only executable `qdrant` provider test in `tests/**/*.py`. Not a deletion candidate. |
| D-004 | Task 2 empty-context cohort (240 nodeids; see `docs/references/test-pruning-ledger-coverage.md` §2) | — (not §4.2 by empty hits alone) | Coverage map: scripts / fixtures / migrations / harness often outside `--cov=cowork_agent`. Cross-check vs start-list stale names: **no** additional field/path removals tied to these nodeids. | yes — many exercise `scripts/` / fixture loaders / SQL text / harness | `keep` | Do **not** bulk-delete. Revisit only if a named contract disappears from `src/`. Protected subset (`test_network_guard`, `test_xdist_harness`, `test_pg_probe`) never deletable. |
| D-005 | `tests/unit/domain/test_chat_contracts.py::test_context_request_uses_a_memory_type_free_chat_scope` | — (live contract) | `src/cowork_agent/domain/_chat_contracts_memory.py` — `ChatMemoryScope.tenant_id` still required; payload keys match. | yes — scope key set | `keep` | Exemplar of legitimate remaining `tenant_id` (README). |
| D-006 | `tests/unit/features/ai_chat/memory_eval/test_live_runner.py` (tenant isolation asserts, e.g. `first.tenant_id != second.tenant_id`) | — (live contract) | Memeval identity / scope still mint distinct `tenant_id` values in `src/` chat-memory eval path. | yes — eval isolation | `keep` | Per plan default: eval isolation `tenant_id` stays. |
| D-007 | `tests/unit/test_identity.py` (`VerifiedPrincipal(tenant_id=...)`) | — (live contract) | `src/cowork_agent/identity.py` — `VerifiedPrincipal.tenant_id` / `workspace_id` alias still present. | yes — principal identity | `keep` | Per plan default. |
| D-008 | `tests/unit/domain/test_chat_contracts.py::test_chat_message_request_from_dict_rejects_retired_tool_choices` (+ API twin `test_message_endpoint_rejects_retired_tool_choices_before_controller_dispatch`) | — (active rejection of retired surface) | Chat request path still rejects `tool_choices` / `@Email` (ADR-004; src still validates absence). | yes — retired-API rejection | `keep` | Purpose is reject-retired-input, not re-test a deleted store. |
| D-009 | `tests/unit/integrations/test_project_documents_hybrid.py` (module docstring mentions pre-ADR-008 Qdrant) | — (not a qdrant store test) | Executable tests pin project ACL + `.tvim` allowlist (`src/.../project_documents.py`). No `RAG_STORE_PROVIDER=qdrant` assertion. | yes — project isolation | `keep` | Comment-only historical note; purpose ≠ retired company store. |

## Counts

- Ledger rows: **9**
- Proposed `fix call`: **2**
- Proposed `delete`: **0**
- Proposed `keep`: **7**
- Start-list items with no hit / none found (checklist only): forbidden-contract `tenant_id` constructions; `except Exception`/`except:`/`except BaseException`; BM25 unused kwarg (no test sites)
