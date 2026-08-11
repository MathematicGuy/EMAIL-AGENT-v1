"""Unit tests for Query Guard and Reranker Thresholding."""

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
from cowork_agent.integrations.rag.jina_reranker import FakeJinaReranker
from cowork_agent.integrations.rag.knowledge_base import KnowledgeChunk, KnowledgeDocument
from cowork_agent.integrations.rag.query_guard import is_retrieval_query


class DummyEmbedder:

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return tuple((0.1, 0.2) for _ in texts)


def test_is_retrieval_query_detects_greetings_and_filler() -> None:
    assert not is_retrieval_query("hê lô")
    assert not is_retrieval_query("hello")
    assert not is_retrieval_query("hi")
    assert not is_retrieval_query("xin chào")
    assert not is_retrieval_query("test")
    assert not is_retrieval_query("a")

    assert is_retrieval_query("quy trình nghỉ phép công ty")
    assert is_retrieval_query("hướng dẫn nộp bảo hiểm xã hội")


def test_hybrid_filters_out_greeting_query() -> None:
    chunks = (
        KnowledgeChunk(
            chunk_id="c1",
            document_id="d1",
            document_title="D1",
            section=None,
            text="chủ đề bảo hiểm xã hội",
            source_url="d1.md",
            tenant_id="local",
        ),
    )
    memory = HybridSemanticMemory(
        documents=(KnowledgeDocument("k", "K", "k.md", chunks),),
        embedder=DummyEmbedder(),
        min_score_default=0.0,
    )
    asyncio.run(memory.build_index())

    request = SemanticRetrievalRequest(
        run_id="r1",
        tenant_id="local",
        user_id="u1",
        query="hê lô",
        knowledge_gaps=(),
        filters=RetrievalFilters(tenant_scope="local", document_status=("ready",)),
        limits=RetrievalLimits(top_k=5, min_score=0.0, timeout_ms=1000),
    )

    response = asyncio.run(memory.retrieve(request))
    assert response.retrieval_status is RetrievalStatus.NO_RESULTS
    assert response.chunks == ()


def test_hybrid_filters_out_low_rerank_score_chunks() -> None:
    chunks = (
        KnowledgeChunk(
            chunk_id="c1",
            document_id="d1",
            document_title="D1",
            section=None,
            text="thủ tục hành chính thuế",
            source_url="d1.md",
            tenant_id="local",
        ),
    )
    # Low score candidate (0.083 < 0.25 min_rerank_score)
    reranker = FakeJinaReranker(scores={"c1": 0.083})

    memory = HybridSemanticMemory(
        documents=(KnowledgeDocument("k", "K", "k.md", chunks),),
        embedder=DummyEmbedder(),
        reranker=reranker,
        min_rerank_score=0.25,
        min_score_default=0.0,
    )
    asyncio.run(memory.build_index())

    request = SemanticRetrievalRequest(
        run_id="r1",
        tenant_id="local",
        user_id="u1",
        query="quy trình đóng thuế",
        knowledge_gaps=(),
        filters=RetrievalFilters(tenant_scope="local", document_status=("ready",)),
        limits=RetrievalLimits(top_k=5, min_score=0.0, timeout_ms=1000),
    )

    response = asyncio.run(memory.retrieve(request))
    assert response.retrieval_status is RetrievalStatus.NO_RESULTS
    assert response.chunks == ()
