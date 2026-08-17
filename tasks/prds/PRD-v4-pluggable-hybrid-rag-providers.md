# PRD-v4: Pluggable & Switchable Hybrid RAG Provider Architecture

**Status:** Superseded — company RAG is Turbovec-only (ADR-009). The Qdrant
provider was deleted; `RAG_STORE_PROVIDER` still selects `turbovec` or
degrades to null. Keep this file as history of the factory seam, not as a
plan to restore Qdrant.  
**Location:** `tasks/prds/PRD-v4-pluggable-hybrid-rag-providers.md`  
**Target Domain:** Enterprise Semantic RAG Subsystem (`src/cowork_agent/integrations/rag`)  
**Related ADRs & Specs:**  
- [ADR-003: Ephemeral Email Retrieval](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/tasks/adr/ADR-003-ephemeral-email-retrieval.md)
- [ADR-004: Chat Native Task Episodes](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/tasks/adr/ADR-004-chat-native-task-episodes.md)
- [ADR-007: Project-Scoped User Documents](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/tasks/adr/ADR-007-project-scoped-classifier-gated-user-documents.md)
- [PRD-v1 Core Email & RAG](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/tasks/prds/PRD-v1-Core-Email-and-RAG.md)

---

## Problem Statement

Currently, developers and system operators face friction when deploying or benchmarking the RAG knowledge retrieval subsystem across different runtime environments:
1. **Infrastructure Coupling in Local Environments:** Running full vector retrieval requires an external database server instance (Qdrant), creating setup overhead for local development, edge nodes, or lightweight test suites.
2. **Lack of Seamless Provider Switching:** While both Qdrant and Turbovec implementations exist, switching between them or composing them into hybrid search (Dense Vector + BM25 Lexical + Reciprocal Rank Fusion) requires manual code changes or deprecated wrapper classes.
3. **Inconsistent RAG Behaviors:** Email Action Plan RAG and AI Chat Semantic Memory need a standardized, environment-configurable factory to instantiate the exact vector memory backend (`turbovec` vs `qdrant`) without modifying downstream consumer code or violating tenant boundaries.

## Solution

Implement a unified, configuration-driven **Pluggable Hybrid RAG Provider Architecture**:
- Expose a single configuration key (`RAG_STORE_PROVIDER`) in the central bootstrap factory (`build_semantic_memory`).
- Allow seamless runtime selection between **Turbovec 4-bit Quantized In-Process Hybrid Memory** (ideal for zero-infrastructure local dev and sub-5ms boots) and **Qdrant Vector Database Hybrid Memory** (ideal for multi-tenant cloud clusters).
- Ensure both providers strictly honor the `SemanticMemoryPort` contract (`retrieve(request)`), so Email Action Plan workflows and AI Chat Controllers consume ground-truth knowledge identically regardless of the underlying vector engine.

---

## User Stories

1. As a local developer, I want to run the full application and RAG retrieval without starting a Qdrant Docker container, so that I can develop and test features locally with zero external infrastructure overhead.
2. As a cloud DevOps engineer, I want to configure `RAG_STORE_PROVIDER=qdrant` via environment variables, so that production clusters can scale vector search horizontally across multi-tenant Qdrant nodes.
3. As a local developer, I want Turbovec to load pre-computed binary snapshots (`.tvim`) in under 5 milliseconds on process startup, so that I don't waste time or API credits re-embedding documents on every restart.
4. As an Email Action Plan workflow component, I want to query `SemanticMemoryPort` for company knowledge without knowing whether Turbovec or Qdrant handled the vector search, so that domain business logic remains decoupled from vector database implementations.
5. As an AI Chat Controller, I want to fetch Type 4 Semantic Memory through the unified `SemanticMemoryPort` interface, so that chat replies cite grounded facts regardless of the active vector backend.
6. As an AI engineer, I want both Turbovec and Qdrant providers to combine dense vector search with BM25 lexical search using Reciprocal Rank Fusion (RRF), so that retrieval accuracy remains high for both semantic questions and exact keyword queries.
7. As a security engineer, I want user-uploaded project PDFs in AI Chat to remain strictly isolated in Qdrant with payload metadata filters (ADR-007), so that project documents are never leaked into the general company RAG corpus or shared across users.
8. As a system operator, I want the system to fall back gracefully to `NullSemanticMemory` if the configured vector provider fails or is unconfigured, so that email digest runs and chat sessions degrade safely without throwing unhandled 500 crashes.

---

## Implementation Decisions

- **Unified Interface Contract (`SemanticMemoryPort`):** All vector search backends must implement the strict protocol contract:
  ```python
  class SemanticMemoryPort(Protocol):
      async def retrieve(
          self, request: SemanticRetrievalRequest
      ) -> SemanticRetrievalResponse: ...
  ```
  Downstream callers (`workflow.py` for Email Action Plan and `memory_gateway.py` for AI Chat) will only accept `SemanticMemoryPort`.

- **Configuration-Driven Factory (`bootstrap.py`):** Centralize provider instantiation inside `build_semantic_memory()`. The factory reads `RAG_STORE_PROVIDER`:
  - `RAG_STORE_PROVIDER="turbovec"`: Instantiates `TurbovecSemanticMemory` (4-bit TurboQuant quantized SIMD index backed by `.data/turbovec_index.tvim`).
  - `RAG_STORE_PROVIDER="qdrant"`: Instantiates `QdrantSemanticMemory` connected to the configured Qdrant collection.
  - `RAG_STORE_PROVIDER=""` (unset/disabled): Falls back gracefully to `NullSemanticMemory`.

- **Hybrid Composition (BM25 + Dense + RRF):** Hybrid search is structured as a composite wrapper (`HybridSemanticMemory`) that combines a pluggable dense vector retriever (`Turbovec` or `Qdrant`) with `BM25SearchAdapter` lexical search and `ReciprocalRankFusion` (RRF) candidate re-ranking.

- **Boundary Separation for User Documents (ADR-007 / ADR-008):** User-uploaded project documents (`POST /v1/projects/{id}/documents`) bypass the central company RAG ingestion CLI. Chunk text and ACL live in Postgres (`project_document_chunks`); the dense leg is a per-project Turbovec `.tvim` searched with a SQL-built allowlist (`HybridProjectDocumentStore` in [`project_documents.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/project_documents.py)). Company-plane Qdrant is not on this path.

---

## Testing Decisions

- **High-Level Seam Selection:** All provider tests will execute against the **`SemanticMemoryPort` seam** produced by `build_semantic_memory()`.
  - Testing against this single high-level seam verifies full end-to-end retrieval behavior (Query $\rightarrow$ Embedding $\rightarrow$ Vector/Lexical Search $\rightarrow$ RRF Fusion $\rightarrow$ `SemanticRetrievalResponse`) without coupling tests to internal provider private methods.

- **Test Isolation & Hermeticity:**
  - Unit tests will utilize deterministic fake embedders (`FakeEmbeddingAdapter`) to avoid external network calls to Jina AI.
  - Integration tests will verify that both `turbovec` and `qdrant` providers yield identical `SemanticRetrievalResponse` shapes, status codes (`SUCCESS`, `NO_RESULTS`, `TIMEOUT`), and citation metadata formats.

- **Prior Art in Codebase:**
  - `tests/unit/test_turbovec_memory.py`: Unit test patterns for Turbovec snapshot loading, padding, and search.
  - `tests/integration/rag/test_qdrant_memory.py`: Integration test patterns for Qdrant collection indexing and filtering.
  - `tests/fixtures/rag/retrieval_golden.json`: 100 golden test probe queries used for recall benchmarking.

---

## Out of Scope

- **Ingesting Emails into RAG:** Raw email bodies and email attachments remain strictly ephemeral and are never ingested into vector stores (ADR-003).
- **Custom Vector DB Drivers:** Supporting third-party vector databases outside of Qdrant and Turbovec (e.g. Pinecone, Milvus, Weaviate, pgvector) is out of scope for this version.
- **Dynamic Live Cloud Sync of Turbovec Files:** Dynamic bucket polling or automatic cloud uploading of `.tvim` snapshots during active server execution is handled by external deployment scripts / S3 sync adapters, not the core python runtime.

---

## Further Notes

- **Performance Benchmarks:** Empirical benchmarks on 1,043 text chunks demonstrate that Turbovec 4-bit quantization achieves an **88.64% Recall@5** (a 99.6% precision match vs 32-bit floats) while reducing process RAM by 75% (~3 MB RAM vs ~12 MB RAM) and loading snapshot files in `< 5 ms`.
- **Triage Label:** `ready-for-agent`
