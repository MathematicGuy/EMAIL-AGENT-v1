"""Tests for the RAG bootstrap factory.

The factory returns Hybrid(Turbovec) on success, or NullSemanticMemory when
the provider is unknown, retired, disabled, or fails. A broken index may
never block a digest run.
"""

import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest

from cowork_agent.config import JinaEmbeddingSettings
from cowork_agent.domain.target_contracts import (
    RetrievalFilters,
    RetrievalLimits,
    RetrievalStatus,
    SemanticRetrievalRequest,
)
from cowork_agent.integrations.rag import bootstrap
from cowork_agent.integrations.rag.fakes import HashingEmbedder
from cowork_agent.integrations.rag.hybrid import HybridSemanticMemory
from cowork_agent.integrations.rag.jina_reranker import FakeJinaReranker
from cowork_agent.integrations.rag.knowledge_base import KnowledgeChunk, KnowledgeDocument
from cowork_agent.integrations.rag.null_memory import NullSemanticMemory
from cowork_agent.integrations.rag.reranker import RerankerAdapter
from cowork_agent.integrations.rag.turbovec_memory import TURBOVEC_AVAILABLE, TurbovecSemanticMemory

_BUILD_RERANKER = bootstrap._build_reranker


def _jina_settings() -> JinaEmbeddingSettings:
    return JinaEmbeddingSettings.from_env(
        {"JINA_API_KEY": "key-1", "JINA_EMBEDDING_MODEL": "jina-embeddings-v5-omni-small"},
    )


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
                ),
            ),
        ),
    )


def _retrieval_request() -> SemanticRetrievalRequest:
    return SemanticRetrievalRequest(
        run_id="run-1",
        user_id="user@example.com",
        query="alpha travel policy",
        knowledge_gaps=(),
        filters=RetrievalFilters(document_status=("ready",)),
        limits=RetrievalLimits(top_k=3, min_score=0.0, timeout_ms=1500),
    )


def _stub_turbovec_factory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RAG_STORE_PROVIDER", "turbovec")
    monkeypatch.setattr(bootstrap, "load_corpus", lambda *args, **kwargs: _tiny_corpus())
    monkeypatch.setattr(bootstrap, "JinaEmbeddingAdapter", lambda settings: HashingEmbedder())
    monkeypatch.setattr(bootstrap, "TURBOVEC_SNAPSHOT_PATH", tmp_path / "index.tvim")


@pytest.fixture(autouse=True)
def _disable_optional_reranker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep bootstrap tests offline even when a developer has a Cohere key."""
    monkeypatch.setattr(bootstrap, "_build_reranker", lambda: None)


def test_turbovec_provider_builds_turbovec_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    if not TURBOVEC_AVAILABLE:
        pytest.skip("turbovec package not installed")
    _stub_turbovec_factory(monkeypatch, tmp_path)

    memory = asyncio.run(bootstrap.build_semantic_memory(_jina_settings()))

    assert isinstance(memory, HybridSemanticMemory)
    assert isinstance(memory.dense, TurbovecSemanticMemory)


def test_configured_reranker_is_wired_into_hybrid_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    if not TURBOVEC_AVAILABLE:
        pytest.skip("turbovec package not installed")
    _stub_turbovec_factory(monkeypatch, tmp_path)
    reranker = FakeJinaReranker(scores={"doc#0": 0.91})
    monkeypatch.setattr(bootstrap, "_build_reranker", lambda: reranker)

    memory = asyncio.run(bootstrap.build_semantic_memory(_jina_settings()))
    response = asyncio.run(memory.retrieve(_retrieval_request()))

    assert isinstance(memory, HybridSemanticMemory)
    assert response.chunks[0].rerank_score == 0.91


def test_cohere_reranker_factory_uses_default_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COHERE_API_KEY", "cohere-test-key")
    monkeypatch.setattr(bootstrap, "_build_reranker", _BUILD_RERANKER)

    reranker = bootstrap._build_reranker()

    assert isinstance(reranker, RerankerAdapter)


def test_unset_provider_defaults_to_turbovec(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    if not TURBOVEC_AVAILABLE:
        pytest.skip("turbovec package not installed")
    monkeypatch.delenv("RAG_STORE_PROVIDER", raising=False)
    monkeypatch.setattr(bootstrap, "load_corpus", lambda *args, **kwargs: _tiny_corpus())
    monkeypatch.setattr(bootstrap, "JinaEmbeddingAdapter", lambda settings: HashingEmbedder())
    monkeypatch.setattr(bootstrap, "TURBOVEC_SNAPSHOT_PATH", tmp_path / "index.tvim")

    memory = asyncio.run(bootstrap.build_semantic_memory(_jina_settings()))

    assert isinstance(memory, HybridSemanticMemory)
    assert isinstance(memory.dense, TurbovecSemanticMemory)


def test_unknown_provider_degrades_to_null(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_STORE_PROVIDER", "pinecone")

    memory = asyncio.run(bootstrap.build_semantic_memory(_jina_settings()))

    assert isinstance(memory, NullSemanticMemory)


def test_retired_qdrant_provider_degrades_to_null(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_STORE_PROVIDER", "qdrant")

    memory = asyncio.run(bootstrap.build_semantic_memory(_jina_settings()))

    assert isinstance(memory, NullSemanticMemory)


def test_turbovec_provider_failure_degrades_to_null(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_STORE_PROVIDER", "turbovec")

    def _boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("turbovec unavailable")

    monkeypatch.setattr(bootstrap, "load_corpus", _boom)

    memory = asyncio.run(bootstrap.build_semantic_memory(_jina_settings()))

    assert isinstance(memory, NullSemanticMemory)


def test_null_factory_retrieve_is_structured_no_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_STORE_PROVIDER", "none")
    memory = asyncio.run(bootstrap.build_semantic_memory(_jina_settings()))
    response = asyncio.run(memory.retrieve(_retrieval_request()))

    assert isinstance(memory, NullSemanticMemory)
    assert response.retrieval_status is RetrievalStatus.UNAVAILABLE
    assert response.chunks == ()


def test_turbovec_factory_retrieve_returns_citation_shaped_chunks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    if not TURBOVEC_AVAILABLE:
        pytest.skip("turbovec package not installed")
    _stub_turbovec_factory(monkeypatch, tmp_path)

    memory = asyncio.run(bootstrap.build_semantic_memory(_jina_settings()))
    response = asyncio.run(memory.retrieve(_retrieval_request()))

    assert response.retrieval_status is RetrievalStatus.SUCCESS
    assert response.chunks
    chunk = response.chunks[0]
    assert chunk.chunk_id
    assert chunk.document_id
    assert chunk.text
    assert chunk.relevance_score is not None


def test_a_missing_corpus_degrades_to_null_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_STORE_PROVIDER", "turbovec")

    def _missing(*args: object, **kwargs: object) -> Sequence[object]:
        raise ValueError("Knowledge corpus directory not found")

    monkeypatch.setattr(bootstrap, "load_corpus", _missing)

    memory = asyncio.run(bootstrap.build_semantic_memory(_jina_settings()))

    assert isinstance(memory, NullSemanticMemory)
