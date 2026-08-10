# Implementation Plan — Qdrant Vector Store Migration

Migrate the production `SemanticMemoryPort` retrieval adapter from the in-memory `HybridSemanticMemory` store to Qdrant Cloud (`QdrantSemanticMemory`) as specified in `docs/master-comparison.md` and `.wgm/specs/qdrant_vector_store_migration.md`.

**Target Qdrant Cloud endpoint:** `https://83b53413-f827-42a3-92b8-123bb1cae649.us-west-1-0.aws.cloud.qdrant.io`
**Collection:** `company_knowledge`
**Tests use:** `QdrantClient(":memory:")` for isolated, deterministic offline test runs.

---

## Tasks

### Task A: Dependencies and Settings Configuration
- **Files:** `pyproject.toml`, `src/cowork_agent/config.py`
- **Validation command:** `python -m pytest tests/unit/test_config.py -q --basetemp=./.pytest-tmp`
- **Criteria:**
  - Add `qdrant-client[async]>=1.9,<2` to `[project.dependencies]` in `pyproject.toml`.
  - Add `QdrantSettings` dataclass/pydantic model to `config.py` reading from env: `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_COLLECTION` (default `company_knowledge`), `QDRANT_ENABLED` (bool, default `false`), `QDRANT_VECTOR_SIZE` (int, default `768`).
  - Add a unit test in `tests/unit/test_config.py` proving the settings load from env vars.
- **Status:** pending

### Task B: Implement QdrantSemanticMemory Adapter + Corpus Ingestion
- **Files:** `src/cowork_agent/integrations/rag/qdrant.py`, `src/cowork_agent/integrations/rag/__init__.py`
- **Validation command:** `python -m pytest tests/unit/integrations/test_qdrant.py -q --basetemp=./.pytest-tmp`
- **Criteria:**
  - Create `QdrantSemanticMemory` class implementing `SemanticMemoryPort`.
  - Constructor accepts `QdrantClient`, collection name, `EmbeddingPort`, top_k, min_score.
  - `retrieve()` calls `client.query_points()` with **payload filter `tenant_id == request.filters.tenant_scope`** BEFORE any embedding/scoring — this is a security invariant.
  - Convert `ScoredPoint` results to `SemanticChunk` domain objects with `relevance_score`.
  - Create `ingest_corpus(client, collection, documents, embedder)` helper: recreates collection if needed using `VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE)`, then upserts `PointStruct` batches (chunk_id as UUID5, payload includes `tenant_id`, `document_id`, `section`, `text`, `source_url`, `document_title`).
  - All tests use `QdrantClient(":memory:")` — no network required.
- **Status:** pending

### Task C: Wire Qdrant Adapter into Bootstrap Layer
- **Files:** `src/cowork_agent/integrations/rag/bootstrap.py`
- **Validation command:** `python -m pytest tests/unit/integrations/test_bootstrap.py -q --basetemp=./.pytest-tmp`
- **Criteria:**
  - Update `build_semantic_memory(settings)` to accept both `GeminiSettings` and `QdrantSettings`.
  - If `QdrantSettings.enabled` is true, construct `QdrantClient(url=settings.url, api_key=settings.api_key)`, call `ingest_corpus(...)`, return `QdrantSemanticMemory(...)`.
  - Any `Exception` during Qdrant init (connection refused, bad key, etc.) logs a warning and returns `NullSemanticMemory()` — graceful degradation invariant.
  - Add unit tests for the enabled/disabled/error paths.
- **Status:** pending

### Task D: Integration Tests & Fallback Verification
- **Files:** `tests/integration/test_qdrant_integration.py`
- **Validation command:** `python -m pytest tests/integration/test_qdrant_integration.py -q --basetemp=./.pytest-tmp`
- **Criteria:**
  - Test 1: Ingest corpus into `:memory:` Qdrant → retrieve with correct tenant → assert `SUCCESS` + non-empty chunks.
  - Test 2: Retrieve with **wrong** `tenant_scope` → assert `NO_RESULTS` + zero chunks (ACL isolation proof).
  - Test 3: `min_score` threshold → assert only chunks above threshold returned.
  - Test 4: `top_k` limit → assert returned count ≤ top_k.
  - Test 5: Simulate unreachable Qdrant in `build_semantic_memory` → assert returns `NullSemanticMemory`.
- **Status:** pending

### Task E: Demo-Validation Task (Full Workflow Verification)
- **Files:** `tests/integration/email_action_plan/test_workflow.py`
- **Validation command:** `python -m pytest tests/integration/email_action_plan/test_workflow.py -q --basetemp=./.pytest-tmp`
- **Criteria:**
  - Using `:memory:` Qdrant + `HashingEmbedder` (deterministic, no Gemini API), run a full `DigestWorker` email action plan workflow with `route = RETRIEVE_RAG`.
  - Assert final `ActionPlanOutput` is non-empty and citations reference valid chunk IDs from the ingested corpus.
- **Status:** pending

### Task F: Deprecate HybridSemanticMemory as Default
- **Files:** `src/cowork_agent/integrations/rag/hybrid.py`, `src/cowork_agent/integrations/rag/memory.py`, `src/cowork_agent/integrations/rag/__init__.py`
- **Validation command:** `python -m pytest tests/unit/integrations/ -q --basetemp=./.pytest-tmp`
- **Criteria:**
  - Add `warnings.warn("HybridSemanticMemory is deprecated...", DeprecationWarning, stacklevel=2)` at construction time to both `InRepoSemanticMemory` and `HybridSemanticMemory`.
  - Remove both from `__all__` in `__init__.py` (they remain importable directly for legacy tests but are no longer exported).
  - `bootstrap.py` must not reference `HybridSemanticMemory` anymore; it uses only `QdrantSemanticMemory` or `NullSemanticMemory`.
- **Status:** pending

### Task G: Update .env.example with Qdrant Cloud Config
- **Files:** `.env.example`
- **Validation command:** `python -m pytest tests/unit/ -q --basetemp=./.pytest-tmp`
- **Criteria:**
  - Add a `# Vector Store (Qdrant Cloud)` section to `.env.example` with commented-out keys: `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_COLLECTION=company_knowledge`, `QDRANT_ENABLED=true`, `QDRANT_VECTOR_SIZE=768`.
  - **Do NOT commit real API keys** to `.env.example` — use placeholder values only (e.g. `QDRANT_API_KEY=<your-qdrant-cloud-api-key>`).
  - Unit tests still pass (no code changes, only env documentation).
- **Status:** pending
