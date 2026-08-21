# Test pruning ledger — duplicate invariant assertions (§4 condition 1)

Scratch ledger for Task 4 only. Do not copy into `tests/README.md` yet (Task 6).
No tests deleted. Owners are never proposed for deletion.

Source of §3 rows: `tests/README.md` §3 (file wins over the task brief on drift).
Drift note: “App boot never reaches the embedding API” → Do not re-assert in
`API/workflow tests` (README), not `—` (brief).

Evidence rule: non-owner hit → `delete` | `add §3 row` | `keep (wire-up exception already named in §3)`.
Protected (never prune): `test_network_guard.py`, `conftest.py`, `test_xdist_harness.py`,
`test_pg_probe.py`, `live` tests, XPASS `q-006`/`q-014`, persistence skips.

Candidate ids: `I-001` …

---

## Visited-row checklist

| # | Invariant (short) | Visited |
|---|---|---|
| R01 | Legacy `/result` JSON key set / nextActions / empty-state / ordering | [x] |
| R02 | `processedEmails` is development-only | [x] |
| R03 | Run creation idempotent per `(user, Idempotency-Key)` | [x] |
| R04 | Persisted tasks survive replayed run without duplicating | [x] |
| R05 | Postgres migrations apply once and are idempotent | [x] |
| R06 | No raw email body reaches any API response | [x] |
| R07 | No raw email body reaches chat memory | [x] |
| R08 | Retrieval ordering, `top_k`, `min_score`, timeout status | [x] |
| R09 | Binary `document_date` harvest | [x] |
| R10 | Company RAG pre-filter (`document_ids` / `years` / `months`) | [x] |
| R11 | Retrieval over committed corpus + degrade-to-null | [x] |
| R12 | Jina embed key rotation (429, empty-wallet 403) | [x] |
| R13 | Project-document ACL + isolation + empty-allowlist | [x] |
| R14 | Eval report is metadata-only | [x] |
| R15 | OAuth grant identity binding | [x] |
| R16 | Broken `SSL_CERT_FILE` cannot poison a run | [x] |
| R17 | GET messages omits `rag_evidence.content` unless `include_content=true` | [x] |
| R18 | Chat submissions persist one lifecycle row + update by idempotency key | [x] |
| R19 | No non-live test opens a non-loopback socket | [x] |
| R20 | App boot never reaches embedding API (`RAG_STORE_PROVIDER` pinned) | [x] |
| R21 | `import cowork_agent` resolves to checkout `src` | [x] |
| R22 | `import cowork_agent` does not import `langfuse` | [x] |
| R23 | `-p no:xdist` must not usage-error; default 4 workers | [x] |
| R24 | Postgres pre-flight negative-cheap / unexpected socket falls through | [x] |
| R25 | Embedding key rotation backoff (2.0 s Gemini) / batch pace (0.2 s Jina) | [x] |
| R26 | OpenRouter hops to Gemini only on `OpenRouterAPIError` | [x] |

**27/27 §3 rows visited** (R01–R26; R08 has two co-owners counted as one row).

---

## R01 — Legacy `/result` JSON key set, `nextActions` slice, empty-state message, item ordering

- **Owner:** `tests/unit/features/email_action_plan/test_compat_mapper.py`
- **Do not re-assert in:** any API-level test
- **Grep phrases:** `FROZEN_RESULT_KEYS`, `nextActions`, `Không có công việc cần xử lý`, `EMPTY_STATE_MESSAGE`, `legacy_result_shape`, `action_item_sort_key`, `test_empty_run_carries_the_explicit_empty_state_message`

### Non-owner hits

| Id | File / nodeid | What it asserts | Action |
|---|---|---|---|
| I-001 | `tests/integration/email_action_plan/test_workflow.py::test_result_has_explicit_empty_state_message` | `GetDigestResult` payload `message == "Không có công việc cần xử lý"` and empty `actionItems` | **delete** — same empty-state message owned by compat_mapper; workflow only re-asserts the mapper through `GetDigestResult` |

No API-level hits on the frozen key set / `nextActions` slice / sort order outside the owner (and live e2e does not assert those keys).

---

## R02 — `processedEmails` is development-only

- **Owner:** `tests/integration/api/test_principal_boundary.py`
- **Grep phrases:** `processedEmails`, `development-only`, `APP_ENV`, `test_processed_emails_are_development_only`

### Non-owner hits

| Id | File / nodeid | What it asserts | Action |
|---|---|---|---|
| I-002 | `tests/unit/features/email_action_plan/test_compat_mapper.py` (`FROZEN_RESULT_KEYS` includes `"processedEmails"`) | Legacy result shape includes the key name | **keep** — worked example: legacy key membership ≠ production gate. Already owned as part of R01; do **not** collapse into R02. |

No other non-owner asserts the production/dev gate. **No delete candidates under R02.**

Draft (clarifying, optional — R01 already covers legacy key set):

> | Legacy `/result` includes `processedEmails` as a named key (shape only; not the env gate) | `unit/features/email_action_plan/test_compat_mapper.py` | — |

*(Prefer leaving R01 as-is; I-002 is recorded so R02 is not used to delete the mapper key assertion.)*

---

## R03 — Run creation is idempotent per `(user, Idempotency-Key)`

- **Owner:** `tests/integration/email_action_plan/test_workflow.py` (`test_same_idempotency_key_creates_only_one_run`)
- **Do not re-assert in:** API tests (they get it transitively)
- **Grep phrases:** `idempotency_key`, `Idempotency-Key`, `same_idempotency`, `was_created`, `creates_only_one_run`

### Non-owner hits

| Id | File / nodeid | What it asserts | Action |
|---|---|---|---|
| I-003 | `tests/integration/api/test_e2e_frontend_api.py::TestGmailRunLifecycle.test_idempotent_run_creation` | Two live POSTs with same `Idempotency-Key` return same run id | **keep** — protected `live` tier (never prune). Also matches §3 “API tests get it transitively” but protection wins. |
| I-004 | `tests/unit/persistence/test_run_repository.py::test_sqlite_run_repository_persists_and_preserves_idempotency` | SQLite `create` returns `was_created=False` and same id on duplicate key | **add §3 row** — storage-layer uniqueness, not `CreateDigestRun` application policy |
| I-005 | `tests/integration/persistence/test_postgres_repositories.py::test_create_is_atomic_and_idempotent_on_user_and_key` | Postgres run repo idempotent on `(user, key)` + distinct user gets new row | **add §3 row** — Postgres repository contract (also covers per-user scoping the owner does not pin at SQL) |
| I-006 | `tests/integration/persistence/test_postgres_repositories.py::test_concurrent_creates_insert_exactly_one_row` | Concurrent creates insert exactly one row | **add §3 row** — concurrency/atomicity fact owner lacks |

### Drafted new §3 rows

| Invariant | Owned by | Do not re-assert in |
|---|---|---|
| SQLite run repository `create` is idempotent on `(user_id, idempotency_key)` across reopen | `unit/persistence/test_run_repository.py` | workflow / API |
| Postgres run repository `create` is idempotent on `(user_id, idempotency_key)` and allows the same key for a different user | `integration/persistence/test_postgres_repositories.py` | workflow / API |
| Concurrent Postgres run creates with the same key insert exactly one row | `integration/persistence/test_postgres_repositories.py` | — |

---

## R04 — Persisted tasks survive a replayed run without duplicating

- **Owner:** `tests/integration/email_action_plan/test_workflow.py` (`test_persisted_tasks_are_idempotent_across_replayed_runs`)
- **Grep phrases:** `persisted_tasks_are_idempotent`, `replayed`, `task_repository.tasks`, `pipeline_version`

### Non-owner hits

None that re-assert “same tenant:user:gmail_message_id:pipeline_version → one durable row across two runs.” Other idempotency hits are run-creation (R03) or chat-turn lifecycle (R18).

**No candidates.**

---

## R05 — Postgres migrations apply once and are idempotent

- **Owner:** `tests/integration/persistence/test_postgres_repositories.py` (`test_migrations_apply_once_and_are_idempotent`)
- **Grep phrases:** `migrations_apply_once`, `apply_migrations`, `idempotent`

### Non-owner hits

None. Other “idempotent” hits are domain/repo behaviours, not migration apply-once.

**No candidates.**

---

## R06 — No raw email body reaches any API response

- **Owner:** `tests/integration/api/test_principal_boundary.py` (`test_run_history_is_scoped_ordered_and_body_free` asserts `"body" not in response.text.lower()`)
- **Do not re-assert in:** workflow/repository tests
- **Grep phrases:** `raw email body`, `_assert_no_raw_email_body`, `"body" not in`, `body_free`, `leaking_raw_email_body`

### Non-owner hits

| Id | File / nodeid | What it asserts | Action |
|---|---|---|---|
| I-007 | `tests/integration/api/test_e2e_frontend_api.py` (`test_no_raw_email_body_in_run_status`, `test_full_run_lifecycle_and_no_raw_body_in_result`, `test_no_raw_email_body_in_tasks`, unread-preview `body` absent) | Live HTTP responses have no `body` key at any depth | **keep** — protected `live` |
| I-008 | `tests/integration/email_action_plan/test_workflow.py::test_validation_drops_generated_task_leaking_raw_email_body` | Validation drops a generated task whose `request_summary` equals the raw body; task not persisted | **add §3 row** — validation/leak policy, not API JSON |
| I-009 | `tests/integration/email_action_plan/test_workflow.py::test_validation_dropped_task_is_never_persisted` | Same leak path; repository stays empty | **add §3 row** (or fold into I-008 owner once §3 updated) — persistence consequence of validation drop |
| I-010 | `tests/integration/email_action_plan/test_workflow.py::test_sqlite_persisted_tasks_are_body_free` | SQLite `iterdump()` does not contain the raw body string | **add §3 row** — durable task store has no raw body |
| I-011 | `tests/integration/email_action_plan/test_workflow.py::test_telemetry_emits_metadata_only_candidate_and_run_events` | Trace sink events omit secret body text | **add §3 row** — telemetry metadata-only (related privacy surface) |

### Drafted new §3 rows

| Invariant | Owned by | Do not re-assert in |
|---|---|---|
| Validation drops generated tasks that leak raw email body into task fields; nothing is persisted | `integration/email_action_plan/test_workflow.py` | API tests |
| SQLite task repository dump never contains the source email body | `integration/email_action_plan/test_workflow.py` | — |
| Digest telemetry events are metadata-only (no raw email body text) | `integration/email_action_plan/test_workflow.py` | — |

---

## R07 — No raw email body reaches chat memory

- **Owner:** `tests/unit/domain/test_chat_contracts.py`
- **Do not re-assert in:** gateway tests
- **Grep phrases:** `raw_email`, `no_raw_email`, `normalized_body`, `attachment_content`, `tool_payload`

### Non-owner hits

| Id | File / nodeid | What it asserts | Action |
|---|---|---|---|
| I-012 | `tests/unit/features/ai_chat/test_memory_gateway.py::test_gateway_rejects_unauthorized_task_episode_before_adapter_write` (`rag_citations` / `raw_email`) | Gateway rejects forged task episode with raw-shaped citation **before adapter write** | **add §3 row** — fail-closed write gate (no adapter call) is an extra fact the pure contract owner lacks; not a named §3 wire-up |
| I-013 | `tests/unit/features/ai_chat/test_generation_context.py::test_assembler_omits_missing_or_malformed_sources_without_inventing_content` | `GenerationContext` has no `email_body` / `raw_email` fields | **add §3 row** — generation-context field ban (assembler layer), not chat-memory episode contracts |
| I-014 | `tests/unit/features/ai_chat/test_episode_policy.py` (param with `raw_email`) | Episode policy rejects raw-shaped citations | **add §3 row** — policy-layer gate, distinct from domain contract `from_dict` |
| I-015 | `tests/integration/persistence/test_chat_profile_repository.py::test_schema_carries_no_email_body_or_chat_transcript_column` | Postgres `chat_profiles` columns exclude body/transcript names | **add §3 row** — schema-level absence, not domain `from_dict` |

### Drafted new §3 rows

| Invariant | Owned by | Do not re-assert in |
|---|---|---|
| Memory gateway rejects raw-email-shaped task episode writes before any adapter call | `unit/features/ai_chat/test_memory_gateway.py` | contract tests |
| `GenerationContext` has no raw-email / email_body fields | `unit/features/ai_chat/test_generation_context.py` | — |
| Chat profile SQL schema has no email-body or transcript columns | `integration/persistence/test_chat_profile_repository.py` | domain contract tests |

---

## R08 — Retrieval ordering, `top_k`, `min_score`, timeout status

- **Owners:** `tests/unit/integrations/rag/test_rag.py` + `tests/unit/integrations/rag/test_turbovec_memory.py`
- **Do not re-assert in:** integration tests
- **Grep phrases:** `test_ranking_min_score_and_top_k`, `test_timeout_status_when_embedder_times_out`, `min_score`, `top_k`, `RetrievalStatus.TIMEOUT`

### Non-owner hits

| Id | File / nodeid | What it asserts | Action |
|---|---|---|---|
| I-016 | `tests/unit/integrations/rag/test_bm25.py::test_search_orders_equal_scores_by_chunk_id_and_respects_top_k` | BM25 adapter ordering + `top_k` | **add §3 row** — BM25-leg behaviour, not Hybrid/Turbovec retrieve contract |
| I-017 | Integration workflow/golden uses of `RetrievalStatus` / `min_score` | Workflow degrade / golden harness parameters | **keep** — not asserting ordering/`top_k`/timeout status as the owned unit facts; different scenarios |

No integration test found that re-asserts the unit ranking/timeout matrix. **No delete under R08.**

### Drafted new §3 row

| Invariant | Owned by | Do not re-assert in |
|---|---|---|
| BM25 search orders equal scores by chunk id and respects `top_k` / allowlist | `unit/integrations/rag/test_bm25.py` | hybrid retrieve tests |

---

## R09 — Binary `document_date` harvest (PDF `/Info`, DOCX props; never mtime)

- **Owner:** `tests/unit/integrations/knowledge_ingestion/test_date_harvest.py`
- **Do not re-assert in:** service tests except one wire-up
- **Grep phrases:** `harvest_document_date`, `/Info`, `core_properties`, `document_date`, `mtime`

### Non-owner hits

| Id | File / nodeid | What it asserts | Action |
|---|---|---|---|
| I-018 | `tests/unit/integrations/knowledge_ingestion/test_service.py::test_service_records_harvested_docx_created_date` | Ingest writes harvested DOCX date into manifest; not frontmatter | **keep (wire-up exception already named in §3)** |
| I-019 | `tests/unit/integrations/knowledge_ingestion/test_service.py::test_service_records_empty_document_date_when_harvest_is_none` | Manifest `document_date == ""` when harvest returns None | **add §3 row** — empty-recording contract beyond binary harvest |
| I-020 | `tests/unit/integrations/knowledge_ingestion/test_manifest.py` (persist/reload/missing/non-string `document_date`) | Manifest store schema for `document_date` | **add §3 row** — manifest persistence, not PDF/DOCX harvest |
| I-021 | `tests/unit/integrations/rag/test_hybrid.py::test_hybrid_retrieve_copies_document_date_onto_semantic_chunk` + turbovec twin | Retrieve copies `document_date` onto `SemanticChunk` | **add §3 row** — retrieval projection, not harvest |

No `mtime` assertions outside owner (good — owner owns “never mtime”).

### Drafted new §3 rows

| Invariant | Owned by | Do not re-assert in |
|---|---|---|
| Ingestion service records empty `document_date` in the manifest when harvest returns None | `unit/integrations/knowledge_ingestion/test_service.py` | date_harvest unit |
| Manifest store persists / reloads / defaults `document_date` | `unit/integrations/knowledge_ingestion/test_manifest.py` | — |
| Hybrid/Turbovec retrieve copies `document_date` onto returned semantic chunks | `unit/integrations/rag/test_hybrid.py` + `test_turbovec_memory.py` | date_harvest |

---

## R10 — Company RAG pre-filter (`document_ids` / `years` / `months`); missing date fails year/month

- **Owner:** `tests/unit/integrations/rag/test_rag.py` (`allowed_chunk_indices`)
- **Do not re-assert in:** hybrid/turbovec except one empty-allowlist-no-embed
- **Grep phrases:** `allowed_chunk_indices`, `RetrievalFilters(years=`, `document_ids=`, `empty_allowlist`, `before_dense`

### Non-owner hits

| Id | File / nodeid | What it asserts | Action |
|---|---|---|---|
| I-022 | `tests/unit/integrations/rag/test_hybrid.py::test_hybrid_empty_allowlist_returns_no_results_before_dense` | Years filter → empty allowlist → **no dense embed call** | **keep (wire-up exception already named in §3)** |
| I-023 | `tests/unit/integrations/rag/test_turbovec_memory.py::test_turbovec_retrieve_with_years_on_undated_corpus_returns_no_results_without_embed` | Same empty-allowlist-no-embed for Turbovec | **keep (wire-up exception already named in §3)** |
| I-024 | `tests/unit/integrations/rag/test_hybrid.py::test_hybrid_retrieve_with_document_ids_does_not_return_excluded` | Hybrid retrieve respects `document_ids` | **delete** — re-asserts pre-filter outcome owned by `allowed_chunk_indices`; not the named empty-allowlist exception |
| I-025 | `tests/unit/domain/test_target_contracts.py` retrieval-filters round-trip / defaults | Contract serialization of filter fields | **add §3 row** — schema defaults/round-trip, not filter semantics |

### Drafted new §3 row

| Invariant | Owned by | Do not re-assert in |
|---|---|---|
| `RetrievalFilters` defaults and round-trips `document_ids` / `years` / `months` | `unit/domain/test_target_contracts.py` | rag pre-filter tests |

---

## R11 — Retrieval over the *committed corpus* + degrade-to-null path

- **Owner:** `tests/unit/integrations/test_bootstrap.py`
- **Grep phrases:** `degrades_to_null`, `NullSemanticMemory`, `RAG_STORE_PROVIDER`, `qdrant`, `load_corpus`, `committed`

### Non-owner hits

| Id | File / nodeid | What it asserts | Action |
|---|---|---|---|
| I-026 | `tests/conftest.py` (RAG_STORE_PROVIDER pin) | Suite default provider `none` so boot stays offline | **keep** — protected harness; also separate invariant R20 |
| I-027 | Workflow `test_retrieval_failure_retries_once_then_degrades_to_structured_empty` | Worker retry + structured empty on retrieval failure | **add §3 row** — workflow degrade, not bootstrap factory null memory |

**No delete** of bootstrap owners. Non-owner degrade paths are different surfaces.

### Drafted new §3 row

| Invariant | Owned by | Do not re-assert in |
|---|---|---|
| Email workflow retrieval failure retries once then degrades to structured empty (no null-memory factory) | `integration/email_action_plan/test_workflow.py` | bootstrap |

---

## R12 — Jina embed key rotation (429, empty-wallet 403; not generic 403)

- **Owner:** `tests/unit/integrations/rag/test_embeddings.py`
- **Do not re-assert in:** bootstrap / hybrid
- **Grep phrases:** `rotates_past`, `insufficient_balance`, `generic_forbidden`, `429`, `403`

### Non-owner hits

| Id | File / nodeid | What it asserts | Action |
|---|---|---|---|
| I-028 | `tests/unit/integrations/test_key_rotation.py` | Generic `APIKeyRotator` / env parse / mask | **add §3 row** — shared rotator utility, not Jina HTTP 429/403 policy |
| I-029 | `tests/unit/integrations/llm/test_chat_intent.py::test_gemini_classifier_rotates_key_after_rate_limit` | Gemini chat-intent key rotation | **add §3 row** — LLM path, not Jina embed |
| I-030 | `tests/unit/integrations/llm/test_chat_reply.py::test_gemini_chat_reply_rotates_past_rate_limited_key` | Gemini chat-reply key rotation | **add §3 row** — LLM path |

No bootstrap/hybrid re-assert of Jina 429/empty-wallet/generic-403. **No delete under R12.**

### Drafted new §3 rows

| Invariant | Owned by | Do not re-assert in |
|---|---|---|
| Shared `APIKeyRotator` / env key parsing / masking | `unit/integrations/test_key_rotation.py` | provider-specific embed/LLM tests |
| Gemini chat-intent rotates past rate-limited keys | `unit/integrations/llm/test_chat_intent.py` | embed tests |
| Gemini chat-reply rotates past rate-limited keys | `unit/integrations/llm/test_chat_reply.py` | embed tests |

---

## R13 — Project-document ACL (six SQL conditions before embed) + cross-project isolation + empty-allowlist short-circuit

- **Owner:** `tests/unit/integrations/test_project_documents_hybrid.py`
- **Do not re-assert in:** orchestration/API tests
- **Grep phrases:** `acl_condition`, `empty allowlist`, `before_embedding`, `cross-project`, `allowlist`

### Non-owner hits

| Id | File / nodeid | What it asserts | Action |
|---|---|---|---|
| I-031 | `tests/unit/orchestration/test_project_document_worker.py` (comment on ACL join / status transition) | Ingestion status transitions + timing logs | **keep** — does not re-assert six SQL ACL conditions or empty-allowlist short-circuit |
| I-032 | `tests/unit/persistence/test_sqlite_project_documents.py::test_sqlite_chunks_keep_gemini_hybrid_retrieval_and_acl` | SQLite + hybrid store end-to-end retrieve after index | **add §3 row** — SQLite persistence wire of retrieval, not the six SQL conditions unit |
| I-033 | `tests/integration/persistence/test_project_document_repository.py::test_project_document_repository_isolates_owners_and_deduplicates_content_digest` | Owner isolation + content digest dedupe + job claim | **add §3 row** — Postgres project/document ownership (broader than retrieval ACL) |

No orchestration/API test re-asserts the six ACL SQL predicates. **No delete under R13.**

### Drafted new §3 rows

| Invariant | Owned by | Do not re-assert in |
|---|---|---|
| SQLite project-document chunks support hybrid retrieve after index (persistence wire) | `unit/persistence/test_sqlite_project_documents.py` | hybrid ACL unit |
| Postgres project documents isolate owners and dedupe by content digest | `integration/persistence/test_project_document_repository.py` | hybrid ACL unit |

---

## R14 — Eval report is metadata-only (no query/answer/chunk text)

- **Owner:** one test per script in `tests/unit/scripts/`
- **Grep phrases:** `metadata_only`, `metadata-only`, `LOCAL_ONLY_FIELDS`, forbidden `query`/`answer`/`chunk`

### Non-owner hits

Ownership is intentionally distributed (“one test per script”). Hits inside `unit/scripts/` are **in-owner** by §3 wording.

| Id | File / nodeid | What it asserts | Action |
|---|---|---|---|
| I-034 | Cross-script duplicate *wording* only | Each script pins its own report schema | **keep** — §3 already assigns one owner test per script; not cross-file duplicates of a single owner |

Outside `unit/scripts/`, workflow telemetry metadata-only is covered under R06/I-011 (different surface).

**No delete under R14.** Optional Task 6 cleanup: enumerate the exact owning nodeid per script in README §3 (currently vague).

### Drafted §3 clarification (optional)

| Invariant | Owned by | Do not re-assert in |
|---|---|---|
| Eval report metadata-only (enumerate nodeids) | `unit/scripts/test_evaluate_retrieval.py::test_score_report_schema_is_recursive_closed_and_metadata_only`; `…/test_evaluate_chat_rag.py::test_cli_writes_a_metadata_only_report`; `…/test_evaluate_chat_routing.py::test_chat_routing_dry_run_passes_and_report_is_metadata_only`; `…/test_evaluate_ingestion_latency.py::test_cli_writes_metadata_only_report`; `…/test_build_email_evaluation_report.py` (forbidden-token scan); `…/test_evaluate_email_golden.py` (module docstring / golden immutability) | other layers |

---

## R15 — OAuth grant identity binding (resolver decides `user_id`)

- **Owner:** `tests/unit/integrations/gmail/test_provider.py` (`test_oauth_completion_persists_the_resolved_internal_principal`)
- **Grep phrases:** `principal_resolver`, `internal-user`, `resolved_internal_principal`, `GmailOAuthGrant`

### Non-owner hits

| Id | File / nodeid | What it asserts | Action |
|---|---|---|---|
| I-035 | `tests/integration/api/test_principal_boundary.py` (connection/run user scoping) | API principal boundary / mailbox ownership | **add §3 row** — HTTP principal boundary ≠ OAuth grant→resolver binding |
| I-036 | `tests/unit/test_identity.py` | Identity resolve helpers | **add §3 row** — identity principal minting, not OAuth grant binding |

No second OAuth-completion test that re-binds grant email vs resolver `user_id`. **No delete under R15.**

### Drafted new §3 rows

| Invariant | Owned by | Do not re-assert in |
|---|---|---|
| Mail-todo API scopes runs/connections to the verified principal | `integration/api/test_principal_boundary.py` | gmail OAuth unit |
| Identity helper resolves/mints `VerifiedPrincipal.user_id` | `unit/test_identity.py` | — |

---

## R16 — Broken `SSL_CERT_FILE` cannot poison a run

- **Owner:** `tests/conftest.py` (autouse sanitizer)
- **Grep phrases:** `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`

### Non-owner hits

None outside `conftest.py`. **Protected owner. No candidates.**

---

## R17 — GET `/sessions/{id}/messages` omits `rag_evidence.content` unless `include_content=true`

- **Owner:** `tests/integration/api/test_chat_api.py` (`test_list_messages_omits_rag_evidence_content_unless_requested`)
- **Do not re-assert in:** frontend mapper except one preview-only case
- **Grep phrases:** `include_content`, `rag_evidence`, `omits_rag_evidence_content`

### Non-owner hits

| Id | File / nodeid | What it asserts | Action |
|---|---|---|---|
| I-037 | `frontend/src/dashboard/hooks/useStreamingChat.test.tsx` — `keeps list-history RAG evidence when the payload omits chunk content` | Mapper keeps preview and empty `content` when list omits chunk body | **keep (wire-up exception already named in §3)** — the preview-only case |
| I-038 | `frontend/src/dashboard/components/RagEvidencePanel.test.tsx` — `falls back to preview when list history omitted chunk content` | UI falls back to `preview` when `content` is empty | **add §3 row** — panel display policy, not API omit |

Domain `test_chat_contracts` rag_evidence round-trips **include** content on the contract — different invariant (serialization), not the GET omit gate.

### Drafted new §3 row

| Invariant | Owned by | Do not re-assert in |
|---|---|---|
| RagEvidencePanel falls back to `preview` when `content` is empty | `frontend/.../RagEvidencePanel.test.tsx` | chat API omit test |

---

## R18 — Chat submissions persist one lifecycle row before generation and update it by idempotency key

- **Owners:** `tests/unit/features/ai_chat/test_controller.py` + `tests/unit/persistence/test_chat_history_migration.py`
- **Do not re-assert in:** frontend hook tests except API-shape mapping
- **Grep phrases:** `begin_turn`, `idempotency_key`, `GENERATING`, `lifecycle`, `UNIQUE (session_id, idempotency_key)`

### Non-owner hits

| Id | File / nodeid | What it asserts | Action |
|---|---|---|---|
| I-039 | `frontend/.../useStreamingChat.test.tsx` — `retries the same logical turn without duplicating its user prompt` | Retry reuses same `idempotency_key`; one user bubble | **keep (wire-up exception already named in §3)** — API-shape / hook mapping |
| I-040 | `tests/unit/persistence/test_local_chat_history_repository.py::test_local_chat_history_persists_idempotent_lifecycle_and_latest_turn` | SQLite history begin/replay/conflict | **add §3 row** — SQLite repository behaviour |
| I-041 | `tests/unit/persistence/test_chat_history_repository.py` (begin/complete lifecycle) | In-memory/fake history repository lifecycle | **add §3 row** — repository port behaviour beyond migration SQL |
| I-042 | `tests/integration/persistence/test_chat_session_repository.py::test_chat_history_begin_is_idempotent_and_completion_updates_in_place` | Postgres history begin idempotent + complete in place | **add §3 row** — Postgres repository |

### Drafted new §3 rows

| Invariant | Owned by | Do not re-assert in |
|---|---|---|
| Local SQLite chat history begin is idempotent per `(session, key)` and completion updates in place | `unit/persistence/test_local_chat_history_repository.py` | controller |
| Postgres chat history begin is idempotent and completion updates in place | `integration/persistence/test_chat_session_repository.py` | controller / frontend |

---

## R19 — No test outside the `live` tier opens a non-loopback socket

- **Owner:** `tests/unit/test_network_guard.py`
- **Grep phrases:** `non-loopback`, `network_guard`, `192.0.2.1`, `guarded_connect`

### Non-owner hits

`tests/conftest.py` installs the runtime guard — **protected**, complementary harness (enforcement vs unit assertion of the guard). **keep** (protected). No delete.

---

## R20 — App boot never reaches the embedding API (`RAG_STORE_PROVIDER` pinned)

- **Owner:** `tests/conftest.py`
- **Do not re-assert in:** API/workflow tests (README)
- **Grep phrases:** `RAG_STORE_PROVIDER`

### Non-owner hits

| Id | File / nodeid | What it asserts | Action |
|---|---|---|---|
| I-043 | `tests/unit/integrations/test_bootstrap.py` (sets provider for factory cases) | Factory behaviour under explicit providers | **keep** — exercises bootstrap (R11), does not re-assert suite pin |
| — | API/workflow tests | No re-assert of the suite pin found | — |

**No delete.** Owner protected.

---

## R21 — `import cowork_agent` resolves to this checkout's `src`, not the venv editable install

- **Owner:** `tests/unit/test_xdist_harness.py`
- **Grep phrases:** `editable`, `sys.path`, checkout `src`

### Non-owner hits

`tests/conftest.py` also prepends checkout `src` — **protected** enforcement twin of the harness assertion. **keep** (protected). No delete.

---

## R22 — `import cowork_agent` (and email workflow / Gemini provider) does not import `langfuse`

- **Owner:** `tests/unit/test_xdist_harness.py`
- **Grep phrases:** `langfuse`

### Non-owner hits

`conftest.py` references langfuse module names in socket/guard comments / cleanup — not a second assert of import-graph. **No delete candidates.**

---

## R23 — `-p no:xdist` must not usage-error; default run still fans out to 4 workers

- **Owner:** `tests/unit/test_xdist_harness.py`
- **Grep phrases:** `no:xdist`, `four workers`, `reconcile`

### Non-owner hits

`tests/xdist_plugin.py` is implementation under test, not a second test module. **No candidates.**

---

## R24 — Postgres pre-flight only ever makes a *negative* cheap; unexpected socket error still falls through to `psycopg.connect`

- **Owner:** `tests/unit/test_pg_probe.py`
- **Do not re-assert in:** the persistence modules themselves
- **Grep phrases:** `_tcp_port_answers`, `preflight_timeout`, `server_available`

### Non-owner hits

Persistence modules **call** `server_available` (skip gate) — they do not re-assert pre-flight semantics. **No candidates.** Owner protected.

---

## R25 — Embedding key rotation backs off between attempts (2.0 s Gemini) and paces batches (0.2 s Jina)

- **Owner:** `tests/unit/integrations/rag/test_embeddings.py` (`slept` fixture)
- **Grep phrases:** `slept`, `2.0`, `0.2`, `backoff`

### Non-owner hits

None that assert the same sleep schedule. LLM rotation tests (I-029/I-030) do not pin 2.0/0.2 via `slept`. **No candidates.**

---

## R26 — OpenRouter hops to Google Gemini only on `OpenRouterAPIError` (transport / unusable JSON), never on schema-invalid JSON after repair

- **Owners:** `tests/unit/integrations/llm/test_last_resort.py` + `tests/unit/integrations/llm/test_openrouter.py`
- **Do not re-assert in:** chat_reply / chat_intent except one wire-up each
- **Grep phrases:** `OpenRouterAPIError`, `gemini_json_complete`, `does_not_hop_on_schema_invalid`, `hops_to_gemini`

### Non-owner hits

| Id | File / nodeid | What it asserts | Action |
|---|---|---|---|
| I-044 | `tests/unit/integrations/llm/test_chat_reply.py::test_openrouter_chat_reply_hops_to_gemini_on_openrouter_api_error` | Chat-reply adapter hops on transport error | **keep (wire-up exception already named in §3)** |
| I-045 | `tests/unit/integrations/llm/test_chat_reply.py::test_openrouter_chat_reply_does_not_hop_on_schema_invalid_json` | Chat-reply does not hop on schema-invalid JSON | **keep (wire-up exception already named in §3)** — pair with I-044 as the one chat_reply wire-up |
| I-046 | `tests/unit/integrations/llm/test_chat_intent.py::test_openrouter_intent_hops_to_gemini_on_openrouter_api_error` | Intent classifier hops | **keep (wire-up exception already named in §3)** |
| I-047 | `tests/unit/integrations/llm/test_chat_intent.py::test_openrouter_intent_does_not_hop_on_schema_invalid_json` | Intent does not hop on schema-invalid | **keep (wire-up exception already named in §3)** |
| I-048 | `tests/unit/integrations/llm/test_chat_reply.py::test_openrouter_chat_reply_from_settings_without_last_resort` | Composition without last-resort still behaves | **add §3 row** — optional last-resort wiring, not the hop predicate itself |

### Drafted new §3 row

| Invariant | Owned by | Do not re-assert in |
|---|---|---|
| OpenRouter chat-reply from_settings without last-resort stays conservative on transport error | `unit/integrations/llm/test_chat_reply.py` | last_resort / openrouter owners |

---

## Summary counts

| Action | Count (candidate rows I-xxx) |
|---|---|
| **delete** | 2 (`I-001`, `I-024`) |
| **add §3 row** | 29 (`I-004`–`I-006`, `I-008`–`I-016`, `I-019`–`I-021`, `I-025`, `I-027`–`I-030`, `I-032`–`I-033`, `I-035`–`I-036`, `I-038`, `I-040`–`I-042`, `I-048`) |
| **keep (wire-up / protected / non-duplicate)** | 17 (`I-002`, `I-003`, `I-007`, `I-017`, `I-018`, `I-022`, `I-023`, `I-026`, `I-031`, `I-034`, `I-037`, `I-039`, `I-043`, `I-044`–`I-047`) |

Exact I-id inventory: **I-001 … I-048** (48 recorded hits; 2+29+17=48).

| Delete candidates (nodeids) | §3 owner quoted |
|---|---|
| `tests/integration/email_action_plan/test_workflow.py::test_result_has_explicit_empty_state_message` | R01 compat_mapper empty-state |
| `tests/unit/integrations/rag/test_hybrid.py::test_hybrid_retrieve_with_document_ids_does_not_return_excluded` | R10 `allowed_chunk_indices` |

Zero deletions performed. Human checkpoint required before Task 6.

### Proposed new §3 rows (draft index)

Collected above under each R-section. Approximate **24 drafted rows** (some I-ids share a drafted row; I-002 intentionally does not add a row).

---

## Method notes

- Grepped `tests/` (and frontend where §3 names frontend exceptions) for owner phrases; read both sides before classifying.
- Live e2e duplicates → **keep** (protected), not delete.
- HashingEmbedder / coverage % / runtime / testmon not used as evidence.
- `processedEmails` in compat_mapper kept per plan worked example (R02).
