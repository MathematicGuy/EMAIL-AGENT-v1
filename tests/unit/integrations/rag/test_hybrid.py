"""Focused behavior tests for hybrid in-repo semantic retrieval."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from cowork_agent.domain.target_contracts import (
    RetrievalFilters,
    RetrievalLimits,
    RetrievalStatus,
    SemanticRetrievalRequest,
)
from cowork_agent.integrations.rag.hybrid import HybridSemanticMemory
from cowork_agent.integrations.rag.jina_reranker import FakeJinaReranker, JinaRerankerAdapter
from cowork_agent.integrations.rag.knowledge_base import KnowledgeChunk, KnowledgeDocument


class FixedEmbedder:
    """Deterministic vector double that exposes unexpected query embedding."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def embed(
        self, texts: Sequence[str], *, task: str = "retrieval.query"
    ) -> tuple[tuple[float, ...], ...]:
        del task
        values = tuple(texts)
        self.calls.append(values)
        if len(values) == 3:
            return ((1.0, 0.0), (0.0, 1.0), (0.8, 0.6))
        return ((1.0, 0.0),)


def _memory(*, reranker: object | None = None) -> tuple[HybridSemanticMemory, FixedEmbedder]:
    chunks = (
        KnowledgeChunk(
            chunk_id="a",
            document_id="a",
            document_title="A",
            section=None,
            text="dense exclusive",
            source_url="a.md",
            tenant_id="local",
        ),
        KnowledgeChunk(
            chunk_id="b",
            document_id="b",
            document_title="B",
            section=None,
            text="lexical alpha",
            source_url="b.md",
            tenant_id="local",
        ),
        KnowledgeChunk(
            chunk_id="c",
            document_id="c",
            document_title="C",
            section=None,
            text="lexical alpha",
            source_url="c.md",
            tenant_id="local",
        ),
    )
    embedder = FixedEmbedder()
    memory = HybridSemanticMemory(
        (KnowledgeDocument("knowledge", "Knowledge", "knowledge.md", chunks),),
        embedder,
        reranker=reranker,
        min_score_default=0.0,
    )
    asyncio.run(memory.build_index())
    return memory, embedder


def _request(*, tenant_scope: str = "local", top_k: int = 5) -> SemanticRetrievalRequest:
    return SemanticRetrievalRequest(
        run_id="run-1",
        tenant_id="local",
        user_id="user@example.com",
        query="alpha",
        knowledge_gaps=(),
        filters=RetrievalFilters(tenant_scope=tenant_scope, document_status=("ready",)),
        limits=RetrievalLimits(top_k=top_k, min_score=0.0, timeout_ms=1500),
    )


def test_hybrid_fuses_dense_and_bm25_candidate_union() -> None:
    memory, _ = _memory()

    response = asyncio.run(memory.retrieve(_request()))

    assert response.retrieval_status is RetrievalStatus.SUCCESS
    assert [chunk.chunk_id for chunk in response.chunks] == ["b", "c", "a"]
    assert response.chunks[0].relevance_score > response.chunks[1].relevance_score
    assert response.chunks[1].relevance_score > response.chunks[2].relevance_score


def test_hybrid_acl_denial_skips_query_embedding() -> None:
    memory, embedder = _memory()
    assert len(embedder.calls) == 1

    response = asyncio.run(memory.retrieve(_request(tenant_scope="other-tenant")))

    assert response.retrieval_status is RetrievalStatus.NO_RESULTS
    assert response.chunks == ()
    assert len(embedder.calls) == 1


def test_hybrid_applies_reranker_after_rrf_and_keeps_fused_scores() -> None:
    memory, _ = _memory(
        reranker=FakeJinaReranker(scores={"a": 0.99, "b": 0.1, "c": 0.5})
    )

    response = asyncio.run(memory.retrieve(_request()))

    assert [chunk.chunk_id for chunk in response.chunks] == ["a", "c", "b"]
    assert [chunk.rerank_score for chunk in response.chunks] == [0.99, 0.5, 0.1]
    assert response.chunks[0].relevance_score < response.chunks[1].relevance_score


def test_hybrid_preserves_rrf_order_when_reranker_falls_back() -> None:
    memory, _ = _memory(reranker=JinaRerankerAdapter(api_key=None))

    response = asyncio.run(memory.retrieve(_request()))

    assert [chunk.chunk_id for chunk in response.chunks] == ["b", "c", "a"]
    assert all(chunk.rerank_score is None for chunk in response.chunks)


def test_hybrid_truncates_only_after_reranking_all_fused_candidates() -> None:
    memory, _ = _memory(
        reranker=FakeJinaReranker(scores={"a": 0.99, "b": 0.1, "c": 0.5})
    )

    response = asyncio.run(memory.retrieve(_request(top_k=2)))

    assert [chunk.chunk_id for chunk in response.chunks] == ["a", "c"]
