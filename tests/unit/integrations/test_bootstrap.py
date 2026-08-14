"""Tests for the RAG bootstrap factory (PRD-v4 provider selection).

The factory returns a SemanticMemoryPort: Hybrid(Turbovec|Qdrant) on success,
or NullSemanticMemory when the provider is unknown, disabled, or fails.
A broken index may never block a digest run.
"""

import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest
from qdrant_client import AsyncQdrantClient

from cowork_agent.config import JinaEmbeddingSettings, QdrantSettings
from cowork_agent.domain.target_contracts import (
    RetrievalFilters,
    RetrievalLimits,
    RetrievalStatus,
    SemanticRetrievalRequest,
)
from cowork_agent.integrations.rag import bootstrap
from cowork_agent.integrations.rag.embeddings import JinaEmbeddingAdapter
from cowork_agent.integrations.rag.fakes import HashingEmbedder
from cowork_agent.integrations.rag.hybrid import HybridSemanticMemory
from cowork_agent.integrations.rag.knowledge_base import KnowledgeChunk, KnowledgeDocument
from cowork_agent.integrations.rag.null_memory import NullSemanticMemory
from cowork_agent.integrations.rag.qdrant import QdrantSemanticMemory
from cowork_agent.integrations.rag.turbovec_memory import TURBOVEC_AVAILABLE, TurbovecSemanticMemory

COLLECTION = "bootstrap_company_knowledge"


def _jina_settings() -> JinaEmbeddingSettings:
    return JinaEmbeddingSettings.from_env(
        {"JINA_API_KEY": "key-1", "JINA_EMBEDDING_MODEL": "jina-embeddings-v5-omni-small"},
        load_env_file=False,
    )


def _qdrant_settings(**overrides: str) -> QdrantSettings:
    environment = {
        "QDRANT_URL": "http://localhost:6333",
        "QDRANT_API_KEY": "test-key",
        "QDRANT_COLLECTION": COLLECTION,
        "QDRANT_ENABLED": "true",
        "QDRANT_VECTOR_SIZE": "64",
        **overrides,
    }
    return QdrantSettings.from_env(environment, load_env_file=False)


@pytest.fixture
def local_qdrant(monkeypatch: pytest.MonkeyPatch) -> AsyncQdrantClient:
    """Route the bootstrap's client and embedder to offline test doubles."""
    client = AsyncQdrantClient(":memory:")
    monkeypatch.setenv("RAG_STORE_PROVIDER", "qdrant")
    monkeypatch.setattr(bootstrap, "AsyncQdrantClient", lambda **kwargs: client)
    monkeypatch.setattr(
        bootstrap, "JinaEmbeddingAdapter", lambda settings: HashingEmbedder()
    )
    return client


def test_disabled_qdrant_provider_yields_null_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_STORE_PROVIDER", "qdrant")
    monkeypatch.setattr(
        bootstrap, "JinaEmbeddingAdapter", lambda settings: HashingEmbedder()
    )
    memory = asyncio.run(
        bootstrap.build_semantic_memory(
            _jina_settings(), _qdrant_settings(QDRANT_ENABLED="false")
        )
    )

    assert isinstance(memory, NullSemanticMemory)


def test_enabled_qdrant_ingests_the_corpus_and_returns_the_adapter(
    local_qdrant: AsyncQdrantClient,
) -> None:
    memory = asyncio.run(
        bootstrap.build_semantic_memory(_jina_settings(), _qdrant_settings())
    )

    assert isinstance(memory, HybridSemanticMemory)
    assert isinstance(memory.dense, QdrantSemanticMemory)
    assert asyncio.run(local_qdrant.count(COLLECTION)).count > 0


def test_a_populated_collection_is_not_re_ingested_on_the_next_boot(
    local_qdrant: AsyncQdrantClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    asyncio.run(bootstrap.build_semantic_memory(_jina_settings(), _qdrant_settings()))
    embed_calls: list[int] = []

    original_embed = JinaEmbeddingAdapter.embed

    async def _spy_embed(
        self: JinaEmbeddingAdapter,
        texts: tuple[str, ...],
        *args: object,
        **kwargs: object,
    ) -> tuple[tuple[float, ...], ...]:
        embed_calls.append(len(texts))
        return await original_embed(self, texts, *args, **kwargs)

    monkeypatch.setattr(JinaEmbeddingAdapter, "embed", _spy_embed)
    asyncio.run(bootstrap.build_semantic_memory(_jina_settings(), _qdrant_settings()))

    assert embed_calls == []


def test_reindex_forces_ingestion_even_when_the_collection_is_populated(
    local_qdrant: AsyncQdrantClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    asyncio.run(bootstrap.build_semantic_memory(_jina_settings(), _qdrant_settings()))
    ingested: list[str] = []
    original = bootstrap.ingest_corpus

    async def _spy(client: object, collection: str, *args: object, **kwargs: object) -> int:
        ingested.append(collection)
        return await original(client, collection, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(bootstrap, "ingest_corpus", _spy)
    asyncio.run(
        bootstrap.build_semantic_memory(
            _jina_settings(), _qdrant_settings(QDRANT_REINDEX="true")
        )
    )

    assert ingested == [COLLECTION]


def test_an_unreachable_qdrant_degrades_to_in_repo_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _explode(**kwargs: object) -> AsyncQdrantClient:
        raise ConnectionError("connection refused")

    monkeypatch.setenv("RAG_STORE_PROVIDER", "qdrant")
    monkeypatch.setattr(bootstrap, "AsyncQdrantClient", _explode)
    monkeypatch.setattr(
        bootstrap, "JinaEmbeddingAdapter", lambda settings: HashingEmbedder()
    )

    memory = asyncio.run(
        bootstrap.build_semantic_memory(_jina_settings(), _qdrant_settings())
    )

    assert isinstance(memory, NullSemanticMemory)


def test_a_vector_size_mismatch_degrades_to_in_repo_memory(
    local_qdrant: AsyncQdrantClient,
) -> None:
    memory = asyncio.run(
        bootstrap.build_semantic_memory(
            _jina_settings(), _qdrant_settings(QDRANT_VECTOR_SIZE="768")
        )
    )

    assert isinstance(memory, NullSemanticMemory)


def test_a_missing_corpus_degrades_to_null_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AsyncQdrantClient(":memory:")
    monkeypatch.setenv("RAG_STORE_PROVIDER", "qdrant")
    monkeypatch.setattr(bootstrap, "AsyncQdrantClient", lambda **kwargs: client)
    monkeypatch.setattr(
        bootstrap, "JinaEmbeddingAdapter", lambda settings: HashingEmbedder()
    )
    def _missing(*args: object, **kwargs: object) -> Sequence[object]:
        raise ValueError("Knowledge corpus directory not found")

    monkeypatch.setattr(bootstrap, "load_corpus", _missing)

    memory = asyncio.run(
        bootstrap.build_semantic_memory(_jina_settings(), _qdrant_settings())
    )

    assert isinstance(memory, NullSemanticMemory)


def _tiny_corpus() -> tuple[KnowledgeDocument, ...]:
    return (
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


def _stub_turbovec_factory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RAG_STORE_PROVIDER", "turbovec")
    monkeypatch.setattr(bootstrap, "load_corpus", lambda *args, **kwargs: _tiny_corpus())
    monkeypatch.setattr(bootstrap, "JinaEmbeddingAdapter", lambda settings: HashingEmbedder())
    monkeypatch.setattr(bootstrap, "TURBOVEC_SNAPSHOT_PATH", tmp_path / "index.tvim")


def test_turbovec_provider_builds_turbovec_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    if not TURBOVEC_AVAILABLE:
        pytest.skip("turbovec package not installed")
    _stub_turbovec_factory(monkeypatch, tmp_path)

    memory = asyncio.run(bootstrap.build_semantic_memory(_jina_settings(), _qdrant_settings()))

    assert isinstance(memory, HybridSemanticMemory)
    assert isinstance(memory.dense, TurbovecSemanticMemory)


def test_unset_provider_defaults_to_turbovec(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    if not TURBOVEC_AVAILABLE:
        pytest.skip("turbovec package not installed")
    monkeypatch.delenv("RAG_STORE_PROVIDER", raising=False)
    monkeypatch.setattr(bootstrap, "load_corpus", lambda *args, **kwargs: _tiny_corpus())
    monkeypatch.setattr(bootstrap, "JinaEmbeddingAdapter", lambda settings: HashingEmbedder())
    monkeypatch.setattr(bootstrap, "TURBOVEC_SNAPSHOT_PATH", tmp_path / "index.tvim")

    memory = asyncio.run(
        bootstrap.build_semantic_memory(
            _jina_settings(), _qdrant_settings(QDRANT_ENABLED="true")
        )
    )

    assert isinstance(memory, HybridSemanticMemory)
    assert isinstance(memory.dense, TurbovecSemanticMemory)


def test_unknown_provider_degrades_to_null(
    local_qdrant: AsyncQdrantClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAG_STORE_PROVIDER", "pinecone")

    memory = asyncio.run(
        bootstrap.build_semantic_memory(_jina_settings(), _qdrant_settings())
    )

    assert isinstance(memory, NullSemanticMemory)
    assert not isinstance(memory, QdrantSemanticMemory)


def test_explicit_qdrant_provider_uses_qdrant(
    local_qdrant: AsyncQdrantClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAG_STORE_PROVIDER", "qdrant")

    memory = asyncio.run(bootstrap.build_semantic_memory(_jina_settings(), _qdrant_settings()))

    assert isinstance(memory, HybridSemanticMemory)
    assert isinstance(memory.dense, QdrantSemanticMemory)


def test_turbovec_provider_failure_degrades_to_null(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_STORE_PROVIDER", "turbovec")

    def _boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("turbovec unavailable")

    monkeypatch.setattr(bootstrap, "load_corpus", _boom)

    memory = asyncio.run(
        bootstrap.build_semantic_memory(_jina_settings(), _qdrant_settings())
    )

    assert isinstance(memory, NullSemanticMemory)


def test_null_factory_retrieve_is_structured_no_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_STORE_PROVIDER", "none")
    memory = asyncio.run(
        bootstrap.build_semantic_memory(
            _jina_settings(), _qdrant_settings(QDRANT_ENABLED="false")
        )
    )
    response = asyncio.run(memory.retrieve(_retrieval_request()))

    assert isinstance(memory, NullSemanticMemory)
    assert response.retrieval_status is RetrievalStatus.NO_RESULTS
    assert response.chunks == ()


def test_turbovec_factory_retrieve_returns_citation_shaped_chunks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    if not TURBOVEC_AVAILABLE:
        pytest.skip("turbovec package not installed")
    _stub_turbovec_factory(monkeypatch, tmp_path)

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
