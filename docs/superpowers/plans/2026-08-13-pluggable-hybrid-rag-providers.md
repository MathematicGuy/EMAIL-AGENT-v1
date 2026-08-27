# Pluggable Hybrid RAG Providers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `build_semantic_memory()` a configuration-driven factory that returns one `SemanticMemoryPort` backed by Turbovec, Qdrant, or Null, then compose BM25 + RRF hybrid search around the selected dense store.

**Architecture:** `RAG_STORE_PROVIDER` is the single switch (default `turbovec`). `turbovec` builds `TurbovecSemanticMemory` over `data/extracted/*.md`. `qdrant` builds `QdrantSemanticMemory`. Explicit `none`/`off`/`null` or a failed constructor degrades to `NullSemanticMemory`. Hybrid search wraps the selected dense store with BM25 + RRF; the project-document plane (ADR-007 / ADR-008 `HybridProjectDocumentStore`) stays out of this factory.

**Tech Stack:** Python 3.11+, `SemanticMemoryPort`, Turbovec 4-bit `IdMapIndex`, Qdrant `AsyncQdrantClient`, `BM25SearchAdapter`, `ReciprocalRankFusion`, pytest, ruff, mypy.

## Global Constraints

- Work only in `.worktree/pluggable-rag-provider` on `feature/pluggable-rag-providers`. Do not touch the dirty `main` working tree.
- Do not ingest email bodies or attachments (ADR-003).
- Do not route user project PDFs through this factory (ADR-007 / ADR-008 `HybridProjectDocumentStore`).
- Ask before changing SQL migrations. Preserve the Qdrant auto-fallback when `RAG_STORE_PROVIDER` is unset and `QDRANT_ENABLED=true`.
- Tests target the `SemanticMemoryPort` returned by `build_semantic_memory()`, plus the Hybrid constructor seam for injection. Use `HashingEmbedder` (there is no `FakeEmbeddingAdapter`).
- Never log API keys or embedded text.

## File map

| File | Role |
|------|------|
| `src/cowork_agent/integrations/rag/bootstrap.py` | Provider factory + optional Hybrid wrap |
| `src/cowork_agent/integrations/rag/hybrid.py` | BM25 + RRF wrapper; accept injected dense port |
| `tests/unit/integrations/test_bootstrap.py` | Factory selection + degrade + retrieve contract |
| `tests/unit/integrations/rag/test_hybrid.py` | Injected-dense Hybrid still fuses BM25 |
| `docs/architectures/c3-api-retrieval.md` | Document the switch after behavior exists |

Out of scope this plan: Pinecone/Milvus/pgvector, live `.tvim` cloud sync, project-document provider switching.

## Provider resolution (locked)

```text
provider = RAG_STORE_PROVIDER.strip().lower()

turbovec          → TurbovecSemanticMemory (then Hybrid in Task 4)
qdrant            → Qdrant if settings.enabled else Null
<unknown>         → log warning, Null
"" (unset/auto)   → Qdrant if settings.enabled else Null
any constructor
exception         → log error, Null
```

`QDRANT_ENABLED` still gates Qdrant in both explicit `qdrant` and auto mode. Explicit `turbovec` ignores Qdrant flags.

---

### Task 1: Explicit provider selection in the factory

**Files:**
- Modify: `src/cowork_agent/integrations/rag/bootstrap.py`
- Test: `tests/unit/integrations/test_bootstrap.py`

**Interfaces:**
- Consumes: `RAG_STORE_PROVIDER`, `JinaEmbeddingSettings`, `QdrantSettings`
- Produces: `SemanticMemoryPort` that is Turbovec, Qdrant, or Null

- [ ] **Step 1: Write failing factory tests**

Add to `tests/unit/integrations/test_bootstrap.py`:

```python
def test_turbovec_provider_builds_turbovec_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cowork_agent.integrations.rag.knowledge_base import KnowledgeChunk, KnowledgeDocument
    from cowork_agent.integrations.rag.turbovec_memory import (
        TURBOVEC_AVAILABLE,
        TurbovecSemanticMemory,
    )

    if not TURBOVEC_AVAILABLE:
        pytest.skip("turbovec package not installed")

    documents = (
        KnowledgeDocument(
            "doc",
            "Doc",
            "doc.md",
            (
                KnowledgeChunk(
                    "doc#0",
                    "doc",
                    "Doc",
                    None,
                    "alpha travel policy",
                    "doc.md",
                    "local",
                ),
            ),
        ),
    )
    monkeypatch.setenv("RAG_STORE_PROVIDER", "turbovec")
    monkeypatch.setattr(bootstrap, "load_corpus", lambda *args, **kwargs: documents)
    monkeypatch.setattr(bootstrap, "JinaEmbeddingAdapter", lambda settings: HashingEmbedder())
    monkeypatch.setattr(bootstrap, "TURBOVEC_SNAPSHOT_PATH", tmp_path / "index.tvim")

    memory = asyncio.run(bootstrap.build_semantic_memory(_jina_settings(), _qdrant_settings()))

    assert isinstance(memory, TurbovecSemanticMemory)


def test_unknown_provider_degrades_to_null(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_STORE_PROVIDER", "pinecone")
    monkeypatch.setattr(bootstrap, "JinaEmbeddingAdapter", lambda settings: HashingEmbedder())

    memory = asyncio.run(bootstrap.build_semantic_memory(_jina_settings(), _qdrant_settings()))

    assert isinstance(memory, NullSemanticMemory)


def test_explicit_qdrant_provider_uses_qdrant(
    local_qdrant: AsyncQdrantClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAG_STORE_PROVIDER", "qdrant")

    memory = asyncio.run(bootstrap.build_semantic_memory(_jina_settings(), _qdrant_settings()))

    assert isinstance(memory, QdrantSemanticMemory)


def test_turbovec_provider_failure_degrades_to_null(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_STORE_PROVIDER", "turbovec")

    def _boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("turbovec unavailable")

    monkeypatch.setattr(bootstrap, "load_corpus", _boom)

    memory = asyncio.run(bootstrap.build_semantic_memory(_jina_settings(), _qdrant_settings()))

    assert isinstance(memory, NullSemanticMemory)
```

Add `from pathlib import Path` to the existing imports.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/unit/integrations/test_bootstrap.py -q`

Expected: `test_unknown_provider_degrades_to_null` FAIL — current factory ignores unknown `RAG_STORE_PROVIDER` and still builds Qdrant when `QDRANT_ENABLED=true`.

- [ ] **Step 3: Implement provider resolution**

Replace the body of `build_semantic_memory` so it normalizes `RAG_STORE_PROVIDER` and branches as locked above. Keep `_build_qdrant_memory` / `_ensure_corpus` unchanged. Keep the existing Turbovec try/except. For unknown providers, log a warning and return `NullSemanticMemory()` without touching Qdrant.

```python
async def build_semantic_memory(
    settings: JinaEmbeddingSettings,
    qdrant_settings: QdrantSettings | None = None,
) -> SemanticMemoryPort:
    """Best-effort RAG store selected by RAG_STORE_PROVIDER."""
    provider = os.getenv("RAG_STORE_PROVIDER", "").strip().lower()
    resolved = QdrantSettings.from_env() if qdrant_settings is None else qdrant_settings

    if provider == "turbovec":
        return await _build_turbovec_memory(settings)
    if provider and provider != "qdrant":
        logger.warning("Unknown RAG_STORE_PROVIDER=%s; degrading to NullSemanticMemory", provider)
        return NullSemanticMemory()
    if provider == "qdrant" or resolved.enabled:
        if not resolved.enabled:
            logger.warning(
                "RAG_STORE_PROVIDER=qdrant but Qdrant is disabled; degrading to NullSemanticMemory"
            )
            return NullSemanticMemory()
        try:
            return await _build_qdrant_memory(resolved, JinaEmbeddingAdapter(settings))
        except Exception as exc:
            logger.error(
                "Qdrant memory error (%s: %s); degrading to NullSemanticMemory",
                type(exc).__name__,
                exc,
            )
            return NullSemanticMemory()

    logger.warning("No RAG store configured. Returning NullSemanticMemory.")
    return NullSemanticMemory()
```

Extract the existing Turbovec try/except into `_build_turbovec_memory(settings)` so `build_semantic_memory` stays a dispatcher.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/unit/integrations/test_bootstrap.py -q`

Expected: PASS, including the older Qdrant ingest / degrade cases.

- [ ] **Step 5: Commit**

```bash
git add src/cowork_agent/integrations/rag/bootstrap.py tests/unit/integrations/test_bootstrap.py
git commit -m "feat(rag): honor RAG_STORE_PROVIDER in semantic memory factory"
```

---

### Task 2: Retrieve-contract tests at the factory seam

**Files:**
- Test: `tests/unit/integrations/test_bootstrap.py`

**Interfaces:**
- Observes: `SemanticRetrievalResponse` from `memory.retrieve(...)` only

- [ ] **Step 1: Write failing contract tests**

```python
def _retrieval_request(*, tenant_scope: str = "local") -> SemanticRetrievalRequest:
    return SemanticRetrievalRequest(
        run_id="run-1",
        tenant_id="local",
        user_id="user@example.com",
        query="alpha travel policy",
        knowledge_gaps=(),
        filters=RetrievalFilters(tenant_scope=tenant_scope, document_status=("ready",)),
        limits=RetrievalLimits(top_k=3, min_score=0.0, timeout_ms=1500),
    )


def test_turbovec_factory_retrieve_returns_citation_shaped_chunks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # same corpus/env stubs as Task 1 turbovec test
    memory = asyncio.run(bootstrap.build_semantic_memory(_jina_settings(), _qdrant_settings()))
    response = asyncio.run(memory.retrieve(_retrieval_request()))

    assert response.retrieval_status is RetrievalStatus.SUCCESS
    assert response.chunks
    chunk = response.chunks[0]
    assert chunk.chunk_id
    assert chunk.document_id
    assert chunk.text
    assert chunk.relevance_score is not None


def test_qdrant_factory_retrieve_returns_the_same_response_shape(
    local_qdrant: AsyncQdrantClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAG_STORE_PROVIDER", "qdrant")
    memory = asyncio.run(bootstrap.build_semantic_memory(_jina_settings(), _qdrant_settings()))
    response = asyncio.run(memory.retrieve(_retrieval_request()))

    assert response.retrieval_status in {RetrievalStatus.SUCCESS, RetrievalStatus.NO_RESULTS}
    assert isinstance(response.chunks, tuple)
    assert isinstance(response.query_id, str)
    assert response.latency_ms >= 0
    assert response.tenant_id == "local"


def test_null_factory_retrieve_is_structured_no_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_STORE_PROVIDER", "")
    memory = asyncio.run(
        bootstrap.build_semantic_memory(_jina_settings(), _qdrant_settings(QDRANT_ENABLED="false"))
    )
    response = asyncio.run(memory.retrieve(_retrieval_request()))

    assert isinstance(memory, NullSemanticMemory)
    assert response.retrieval_status is RetrievalStatus.NO_RESULTS
    assert response.chunks == ()
```

Import `RetrievalFilters`, `RetrievalLimits`, `RetrievalStatus`, `SemanticRetrievalRequest` from `cowork_agent.domain.target_contracts`.

The Qdrant retrieve test may already pass against the real corpus in `:memory:`. The Turbovec test should pass once Task 1 stubs `load_corpus` to the tiny document. If the live `data/extracted` corpus is used instead, the assertion still holds as long as retrieve returns citation fields.

- [ ] **Step 2: Verify RED or GREEN**

Run: `python -m pytest tests/unit/integrations/test_bootstrap.py -q`

Expected: GREEN if Task 1 already returns working ports; otherwise implement only missing retrieve wiring (do not add new providers).

- [ ] **Step 3: Commit if any test file change remains**

```bash
git add tests/unit/integrations/test_bootstrap.py
git commit -m "test(rag): assert SemanticMemoryPort retrieve contract per provider"
```

---

### Task 3: Inject a dense SemanticMemoryPort into Hybrid

**Files:**
- Modify: `src/cowork_agent/integrations/rag/hybrid.py`
- Test: `tests/unit/integrations/rag/test_hybrid.py`

**Interfaces:**
- `HybridSemanticMemory(..., dense: SemanticMemoryPort | None = None)`
- Existing `documents` + `embedder` + `dense_backend` constructors stay valid for eval scripts

- [ ] **Step 1: Write failing injection test**

Add to `tests/unit/integrations/rag/test_hybrid.py`:

```python
class RecordingDense:
    def __init__(self, chunks: Sequence[object]) -> None:
        self.calls = 0
        self._chunks = chunks

    async def retrieve(self, request: SemanticRetrievalRequest) -> object:
        from cowork_agent.domain.target_contracts import SemanticChunk, SemanticRetrievalResponse

        self.calls += 1
        del request
        return SemanticRetrievalResponse(
            query_id="q_dense",
            tenant_id="local",
            chunks=(
                SemanticChunk(
                    chunk_id="a",
                    document_id="a",
                    document_title="A",
                    section=None,
                    text="dense exclusive",
                    source_url="a.md",
                    document_version=None,
                    relevance_score=0.9,
                    rerank_score=None,
                ),
            ),
            retrieval_status=RetrievalStatus.SUCCESS,
            latency_ms=1,
        )


def test_hybrid_uses_injected_dense_port_and_still_fuses_bm25() -> None:
    chunks = (
        KnowledgeChunk("a", "a", "A", None, "dense exclusive", "a.md", "local"),
        KnowledgeChunk("b", "b", "B", None, "lexical alpha", "b.md", "local"),
        KnowledgeChunk("c", "c", "C", None, "lexical alpha", "c.md", "local"),
    )
    documents = (KnowledgeDocument("knowledge", "Knowledge", "knowledge.md", chunks),)
    dense = RecordingDense(chunks)
    memory = HybridSemanticMemory(documents, FixedEmbedder(), dense=dense, min_score_default=0.0)

    response = asyncio.run(memory.retrieve(_request()))

    assert dense.calls >= 1
    assert [chunk.chunk_id for chunk in response.chunks] == ["b", "c", "a"]
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/unit/integrations/rag/test_hybrid.py::test_hybrid_uses_injected_dense_port_and_still_fuses_bm25 -q`

Expected: FAIL with `TypeError: unexpected keyword argument 'dense'`.

- [ ] **Step 3: Implement injection**

In `HybridSemanticMemory.__init__`, add `dense: object | None = None` after `embedder`. If `dense` is not `None`, assign `self._dense = dense` and skip constructing `InRepoSemanticMemory` / `TurbovecSemanticMemory`. Otherwise keep the current `dense_backend` branch so `scripts/evaluate_retrieval.py` still works.

Change `build_index` to:

```python
async def build_index(self) -> None:
    build = getattr(self._dense, "build_index", None)
    if build is not None:
        await build()
```

This is required because `QdrantSemanticMemory` has no `build_index`.

Do not emit the deprecation warning when `dense=` is supplied (that path is the new production wrapper). Keep the warning for the eval/legacy constructors.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/unit/integrations/rag/test_hybrid.py tests/unit/integrations/rag/test_advanced_retrieval.py tests/unit/integrations/rag/test_query_guard.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cowork_agent/integrations/rag/hybrid.py tests/unit/integrations/rag/test_hybrid.py
git commit -m "feat(rag): let HybridSemanticMemory wrap an injected dense port"
```

---

### Task 4: Compose Hybrid around the selected dense store

**Files:**
- Modify: `src/cowork_agent/integrations/rag/bootstrap.py`
- Modify: `tests/unit/integrations/test_bootstrap.py`

**Interfaces:**
- Factory return type stays `SemanticMemoryPort`
- Concrete type after this task: `HybridSemanticMemory` wrapping Turbovec or Qdrant; Null stays unwrapped

- [ ] **Step 1: Update factory tests to the composition seam**

Change the Task 1 type assertions:

```python
from cowork_agent.integrations.rag.hybrid import HybridSemanticMemory

assert isinstance(memory, HybridSemanticMemory)
assert isinstance(memory.dense, TurbovecSemanticMemory)  # or QdrantSemanticMemory
```

Add a public read-only `dense` attribute on `HybridSemanticMemory` (`self.dense = self._dense`) if tests need it. Prefer asserting retrieve behavior over type when both are possible.

Add:

```python
def test_null_provider_is_not_wrapped_in_hybrid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_STORE_PROVIDER", "")
    memory = asyncio.run(
        bootstrap.build_semantic_memory(_jina_settings(), _qdrant_settings(QDRANT_ENABLED="false"))
    )
    assert isinstance(memory, NullSemanticMemory)
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/unit/integrations/test_bootstrap.py -q`

Expected: FAIL — factory still returns the bare dense adapter.

- [ ] **Step 3: Wrap in the factory**

After a dense store is successfully built, load the same corpus already used for ingest/index and return:

```python
hybrid = HybridSemanticMemory(documents, embedder, dense=dense)
await hybrid.build_index()  # no-op for Qdrant; required if Turbovec was not built yet
return hybrid
```

Avoid embedding the corpus twice: Turbovec `build_index()` must run once before wrap, then Hybrid `build_index()` sees an already-built dense and should be cheap (Turbovec `build_index` reloads snapshot if `index_path` exists). For Qdrant, skip a second ingest.

Refactor `_build_turbovec_memory` to return `(documents, embedder, memory)` or accept an optional preloaded corpus so Hybrid can reuse it for BM25 without a second `load_corpus` call.

- [ ] **Step 4: Verify GREEN**

Run:

```
python -m pytest tests/unit/integrations/test_bootstrap.py tests/unit/integrations/rag/test_hybrid.py tests/unit/integrations/test_qdrant.py -q
```

Expected: PASS. Existing `isinstance(..., QdrantSemanticMemory)` bootstrap tests must be updated in this same commit.

- [ ] **Step 5: Commit**

```bash
git add src/cowork_agent/integrations/rag/bootstrap.py src/cowork_agent/integrations/rag/hybrid.py tests/unit/integrations/test_bootstrap.py
git commit -m "feat(rag): wrap selected dense store with BM25 RRF hybrid"
```

---

### Task 5: Docs + quality gate

**Files:**
- Modify: `docs/architectures/c3-api-retrieval.md` (provider ladder only)
- Modify: `AGENTS.md` only if the one-line semantic-store sentence is now wrong

- [ ] **Step 1: Align the architecture ladder with the factory**

Document: `RAG_STORE_PROVIDER=turbovec|qdrant`, auto fallback to Qdrant when enabled, else Null; both live providers are Hybrid(dense + BM25 + RRF); project documents remain a separate Qdrant collection.

- [ ] **Step 2: Run the quality gate from the worktree**

```
python -m pytest tests/unit/integrations/test_bootstrap.py tests/unit/integrations/rag/test_hybrid.py tests/unit/integrations/rag/test_turbovec_memory.py tests/unit/integrations/test_qdrant.py tests/unit/integrations/test_chat_semantic_memory.py -q
python -m ruff check src/cowork_agent/integrations/rag/bootstrap.py src/cowork_agent/integrations/rag/hybrid.py tests/unit/integrations/test_bootstrap.py tests/unit/integrations/rag/test_hybrid.py
python -m mypy src/cowork_agent/integrations/rag/bootstrap.py src/cowork_agent/integrations/rag/hybrid.py
```

Expected: all pass.

- [ ] **Step 3: Commit docs if they changed**

```bash
git add docs/architectures/c3-api-retrieval.md AGENTS.md
git commit -m "docs: describe pluggable RAG_STORE_PROVIDER factory"
```

---

## Self-review

**Spec coverage**
- Unified `SemanticMemoryPort` — already exists; factory + callers unchanged.
- `RAG_STORE_PROVIDER=turbovec|qdrant|unset` — Task 1.
- Hybrid BM25 + RRF around either dense store — Tasks 3–4.
- Project documents stay isolated — no factory change (explicit out of scope).
- Failure → `NullSemanticMemory` — Task 1 plus existing Qdrant degrade tests.
- Tests at `build_semantic_memory()` seam — Tasks 1–2, 4.
- Email ingest / third-party DBs / live `.tvim` sync — out of scope, no tasks.

**Backward compatibility**
- Unset + `QDRANT_ENABLED=true` still selects Qdrant (Agents.md fallback). After Task 4 that Qdrant is Hybrid-wrapped, which changes ranking. That is an intentional PRD behavior change; golden-set eval (`scripts/evaluate_retrieval.py`) already has dedicated hybrid modes and is not the factory.

**Placeholder scan:** none.

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Hybrid wrap changes production Qdrant ranking | Med | Task 4 is a separate commit; can ship Tasks 1–3 first |
| Full-corpus `load_corpus` in unit tests is slow | Low | Stub `load_corpus` to one chunk for Turbovec factory tests |
| `turbovec` extra missing in some venvs | Low | `pytest.skip` when `TURBOVEC_AVAILABLE` is false |
| Dirty `main` worktree confusion | High | All edits under `.worktree/pluggable-rag-provider` |
