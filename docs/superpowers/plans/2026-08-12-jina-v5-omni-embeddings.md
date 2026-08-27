# Jina v5 Omni Embeddings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Gemini RAG embeddings with Jina `jina-embeddings-v5-omni-small` without changing LLM provider behaviour.

**Architecture:** Add Jina-specific configuration and an injectable HTTPS adapter behind the existing RAG embedding seam. Evolve the seam to name corpus-passage versus query use; Qdrant and the local fallback pass the appropriate Jina retrieval task.

**Tech Stack:** Python 3.11+, stdlib `urllib`, `asyncio`, Qdrant client, pytest, Ruff, mypy.

## Global Constraints

- Never log credentials or embedded text.
- Model default: `jina-embeddings-v5-omni-small`; dimensions default: `1024`.
- Use `retrieval.passage` for index vectors and `retrieval.query` for query vectors.
- Rebuild a pre-existing Gemini-vector Qdrant collection using `QDRANT_REINDEX=true` once.

---

### Task 1: Add typed configuration and task-aware embedding seam

**Files:**
- Modify: `src/cowork_agent/config.py`
- Modify: `src/cowork_agent/integrations/rag/embeddings.py`
- Modify: `src/cowork_agent/integrations/rag/fakes.py`
- Test: `tests/unit/integrations/rag/test_embeddings.py`

**Interfaces:**
- Produces: `JinaEmbeddingSettings.from_env()` and `EmbeddingPort.embed(texts, *, task="retrieval.query")`.

- [ ] **Step 1: Write failing tests**

```python
def test_jina_embedding_settings_default_to_v5_omni_small() -> None:
    settings = JinaEmbeddingSettings.from_env({"JINA_API_KEY": "test-key"}, load_env_file=False)
    assert (settings.model, settings.dimensions) == ("jina-embeddings-v5-omni-small", 1024)


def test_hashing_embedder_accepts_retrieval_task() -> None:
    assert len(asyncio.run(HashingEmbedder().embed(["text"], task="retrieval.passage"))) == 1
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/unit/integrations/rag/test_embeddings.py -q`

Expected: FAIL because `JinaEmbeddingSettings` and the `task` keyword are absent.

- [ ] **Step 3: Implement minimally**

Add secret-safe Jina settings (`JINA_API_KEY`, `JINA_EMBEDDING_MODEL`, `JINA_EMBEDDING_DIMENSIONS`, `JINA_EMBEDDING_TIMEOUT_SECONDS`) with defaults above. Add optional `task` to the protocol and deterministic fakes; fakes ignore it.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/unit/integrations/rag/test_embeddings.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/cowork_agent/config.py src/cowork_agent/integrations/rag/embeddings.py src/cowork_agent/integrations/rag/fakes.py tests/unit/integrations/rag/test_embeddings.py; git commit -m "feat: add Jina embedding configuration"`

### Task 2: Add the Jina v5 HTTP embedding adapter

**Files:**
- Modify: `src/cowork_agent/integrations/rag/embeddings.py`
- Test: `tests/unit/integrations/rag/test_embeddings.py`

**Interfaces:**
- Produces: `JinaEmbeddingAdapter(settings, transport=...)` implementing `EmbeddingPort`.

- [ ] **Step 1: Write failing adapter tests**

```python
def test_jina_adapter_posts_v5_model_and_passage_task() -> None:
    transport = RecordingJinaTransport({"data": [{"index": 0, "embedding": [0.1] * 1024}]})
    result = asyncio.run(adapter(transport).embed(["policy"], task="retrieval.passage"))
    assert result == ((0.1,) * 1024,)
    assert transport.payload["model"] == "jina-embeddings-v5-omni-small"
    assert transport.payload["task"] == "retrieval.passage"
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/unit/integrations/rag/test_embeddings.py -q`

Expected: FAIL because `JinaEmbeddingAdapter` is absent.

- [ ] **Step 3: Implement minimally**

Use a fixed `POST https://api.jina.ai/v1/embeddings` transport with bearer auth and timeout. Submit `model`, `input`, `task`, `dimensions`, and `embedding_type: float`; validate complete ordered indexes, finite numeric vectors, and exact configured dimensions. Do not log requests, responses, or credentials.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/unit/integrations/rag/test_embeddings.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/cowork_agent/integrations/rag/embeddings.py tests/unit/integrations/rag/test_embeddings.py; git commit -m "feat: embed RAG content with Jina v5"`

### Task 3: Wire Jina through RAG and document the one-time migration

**Files:**
- Modify: `src/cowork_agent/app.py`
- Modify: `src/cowork_agent/integrations/rag/bootstrap.py`
- Modify: `src/cowork_agent/integrations/rag/memory.py`
- Modify: `src/cowork_agent/integrations/rag/hybrid.py`
- Modify: `src/cowork_agent/integrations/rag/qdrant.py`
- Modify: `src/cowork_agent/config.py`, `.env.example`, `README.md`
- Test: `tests/unit/integrations/rag/test_rag.py`, `tests/integration/test_qdrant_integration.py`

**Interfaces:**
- Consumes: Tasks 1-2.
- Produces: semantic memory independent of Gemini embedding keys.

- [ ] **Step 1: Write failing bootstrap/task-selection tests**

```python
def test_bootstrap_uses_jina_settings_without_gemini_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = JinaEmbeddingSettings.from_env({"JINA_API_KEY": "test-key"}, load_env_file=False)
    monkeypatch.setattr(bootstrap, "JinaEmbeddingAdapter", lambda _: HashingEmbedder())
    assert not isinstance(
        asyncio.run(bootstrap.build_semantic_memory(settings, disabled_qdrant())),
        NullSemanticMemory,
    )
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/unit/integrations/rag/test_rag.py tests/integration/test_qdrant_integration.py -q`

Expected: FAIL because bootstrap uses `GeminiSettings` and `GeminiEmbeddingAdapter`.

- [ ] **Step 3: Implement minimally**

Pass `JinaEmbeddingSettings` from app startup to bootstrap. Use `retrieval.passage` in corpus ingestion/index builds and `retrieval.query` in Qdrant, local dense retrieval, and MMR query calls. Change Qdrant's default size to 1024. Document the new `.env` variables and the temporary `QDRANT_REINDEX=true` migration.

- [ ] **Step 4: Verify GREEN and repository checks**

Run: `python -m pytest tests/unit/integrations/rag tests/integration/test_qdrant_integration.py -q; python -m ruff check .; python -m mypy src`

Expected: all commands exit 0.

- [ ] **Step 5: Commit**

Run: `git add src/cowork_agent/app.py src/cowork_agent/config.py src/cowork_agent/integrations/rag/bootstrap.py src/cowork_agent/integrations/rag/memory.py src/cowork_agent/integrations/rag/hybrid.py src/cowork_agent/integrations/rag/qdrant.py .env.example README.md tests/unit/integrations/rag/test_rag.py tests/integration/test_qdrant_integration.py; git commit -m "feat: use Jina embeddings for RAG"`
