# Reference Report: Database Updates & Vector Store Migration

**Project:** `EMAIL-AGENT-v1` (Cowork Agent)  
**Date:** August 10, 2026  
**Status:** Completed & Verified  

---

## 1. Executive Summary

This report documents the current database architecture and recent updates in `EMAIL-AGENT-v1`. The system utilizes a dual-database model comprising:
1. **SQLite Relational Store:** Manages mailbox connection credentials, OAuth state, run metadata, and chat profiles.
2. **Qdrant Cloud Vector Store:** Serves vector embeddings, semantic chunks, and pre-scoring payload ACL filters for RAG knowledge retrieval.

---

## 2. Vector Database Update: Qdrant Cloud Integration

### 2.1 Overview & Migration Context
The production `SemanticMemoryPort` retrieval adapter was migrated from the legacy in-memory `HybridSemanticMemory` / `InRepoSemanticMemory` (dense + BM25 + RRF) to **Qdrant Cloud** (`QdrantSemanticMemory`).

- **Target Collection:** `company_knowledge` (configurable via `QDRANT_COLLECTION`).
- **Adapter Location:** [`src/cowork_agent/integrations/rag/qdrant.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/qdrant.py)
- **Dependency:** `qdrant-client>=1.9,<2`

### 2.2 Vector Store Schema & Payload Structure

| Field | Type | Description |
|---|---|---|
| **Point ID** | `UUID5` | Deterministically generated UUID from document ID and chunk section. |
| **Vector** | `List[float]` | Cosine distance (`Distance.COSINE`), default dimension `768` (dynamically validated against active embedder). |
| `tenant_id` | `String` | **Mandatory ACL field.** Used in pre-scoring payload filter to isolate multi-tenant data. |
| `document_id` | `String` | Unique identifier for source markdown document. |
| `section` | `String` | Header / section anchor name within document. |
| `text` | `String` | Raw chunk text snippet used for context injection into LLM prompt. |
| `document_title` | `String` | Human-readable document title. |
| `source_url` | `String` | Canonical source URI for chunk citation. |

### 2.3 Key Operational Invariants & Settings

- **Pre-Scoring ACL Filtering:** All queries pass a payload filter (`tenant_id == request.filters.tenant_scope`) directly into `AsyncQdrantClient.query_points()`. Multi-tenant isolation occurs inside the Qdrant engine **before** vector scoring.
- **Reindexing Guard (`QDRANT_REINDEX`):** Defaults to `false`. When `false`, startup checks if the collection exists and contains points before attempting re-ingestion, preventing worker processes from wiping shared cloud collections on boot.
- **Graceful Fallback:** If Qdrant is disabled (`QDRANT_ENABLED=false`) or encounters connection errors, `build_semantic_memory()` logs a warning and falls back to `NullSemanticMemory()`.
- **Offline Testing:** Unit and integration test suites run against an in-memory instance (`QdrantClient(":memory:")`) paired with a deterministic `HashingEmbedder`.

---

## 3. Relational Database Architecture (SQLite)

### 3.1 Overview
The relational database operates via SQLite located at `.data/mail_todo.db` (configurable via `GMAIL_CONNECTION_DB_PATH`). Schema changes are managed via versioned SQL migrations in [`src/cowork_agent/persistence/migrations/`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/persistence/migrations).

### 3.2 Migration History & Schema Table Summary

```
src/cowork_agent/persistence/migrations/
├── 001_mail_todo.sql          # Core mailbox & OAuth schema
├── 001_mail_todo.down.sql
├── 002_chat_profiles.sql      # Multi-persona & prompt policies schema
└── 002_chat_profiles.down.sql
```

#### Migration `001_mail_todo.sql`
- **`mailbox_connections`:** Stores encrypted OAuth tokens, scopes, and connection status per user/tenant.
- **`oauth_states`:** Transient state table for OAuth 2.0 PKCE / state verification with TTL expiry.
- **`runs` & `run_results`:** Metadata tracking background email processing runs and output action plans.

#### Migration `002_chat_profiles.sql`
- **`chat_profiles`:** Stores customized system prompt policies, temperature parameters, and agent persona configurations.

---

## 4. Configuration Summary (`.env.example`)

```env
# Relational Store (SQLite)
GMAIL_CONNECTION_DB_PATH=.data/mail_todo.db

# Vector Store (Qdrant Cloud)
QDRANT_URL=replace-with-qdrant-cloud-url
QDRANT_API_KEY=replace-with-qdrant-cloud-api-key
QDRANT_COLLECTION=company_knowledge
QDRANT_ENABLED=false
QDRANT_VECTOR_SIZE=768
QDRANT_REINDEX=false
```

---

## 5. Verification Checklist

- [x] `QdrantSemanticMemory` pre-scoring ACL isolation verified in `tests/integration/test_qdrant_integration.py`.
- [x] Graceful fallback to `NullSemanticMemory` on connection failure verified.
- [x] Relational migrations `001` and `002` applied clean in SQLite repo.
- [x] Zero raw email body or attachment content persisted in either relational or vector DBs (Invariant 1 compliant).
