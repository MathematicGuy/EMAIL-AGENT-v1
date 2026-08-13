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
        ),
        KnowledgeChunk(
            chunk_id="b",
            document_id="b",
            document_title="B",
            section=None,
            text="lexical alpha",
            source_url="b.md",
        ),
        KnowledgeChunk(
            chunk_id="c",
            document_id="c",
            document_title="C",
            section=None,
            text="lexical alpha",
            source_url="c.md",
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


def _request(*, top_k: int = 5) -> SemanticRetrievalRequest:
    return SemanticRetrievalRequest(
        run_id="run-1",
        user_id="user@example.com",
        query="alpha",
        knowledge_gaps=(),
        filters=RetrievalFilters(document_status=("ready",)),
        limits=RetrievalLimits(top_k=top_k, min_score=0.0, timeout_ms=1500),
    )


def test_hybrid_fuses_dense_and_bm25_candidate_union() -> None:
    memory, _ = _memory()

    response = asyncio.run(memory.retrieve(_request()))

    assert response.retrieval_status is RetrievalStatus.SUCCESS
    assert [chunk.chunk_id for chunk in response.chunks] == ["b", "c", "a"]
    assert response.chunks[0].relevance_score > response.chunks[1].relevance_score
    assert response.chunks[1].relevance_score > response.chunks[2].relevance_score


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


class RecordingDense:
    def __init__(self) -> None:
        self.calls = 0

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
    dense = RecordingDense()
    memory = HybridSemanticMemory(documents, FixedEmbedder(), dense=dense, min_score_default=0.0)

    response = asyncio.run(memory.retrieve(_request()))

    assert dense.calls >= 1
    assert response.retrieval_status is RetrievalStatus.SUCCESS
    assert {chunk.chunk_id for chunk in response.chunks} == {"a", "b", "c"}
