"""Unit tests for Advanced Retrieval components (Multi-Query, HyDE, MMR)."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

import numpy as np

from cowork_agent.domain.target_contracts import (
    RetrievalFilters,
    RetrievalLimits,
    RetrievalStatus,
    SemanticChunk,
    SemanticRetrievalRequest,
)
from cowork_agent.integrations.rag.hybrid import HybridSemanticMemory
from cowork_agent.integrations.rag.knowledge_base import KnowledgeChunk, KnowledgeDocument
from cowork_agent.integrations.rag.mmr import mmr_diversify
from cowork_agent.integrations.rag.query_transform import (
    LLMQueryTransformer,
    RuleBasedQueryTransformer,
)


class FakeEmbedder:
    """Deterministic embedder for unit testing."""

    async def embed(
        self, texts: Sequence[str], *, task: str = "retrieval.query"
    ) -> tuple[tuple[float, ...], ...]:
        del task
        results: list[tuple[float, ...]] = []
        for text in texts:
            if "alpha" in text:
                results.append((1.0, 0.0, 0.0))
            elif "beta" in text:
                results.append((0.0, 1.0, 0.0))
            else:
                results.append((0.5, 0.5, 0.0))
        return tuple(results)


def _chunk(chunk_id: str, text: str) -> SemanticChunk:
    return SemanticChunk(
        chunk_id=chunk_id,
        document_id=chunk_id,
        document_title=chunk_id.upper(),
        section=None,
        text=text,
        source_url=f"{chunk_id}.md",
        document_version=None,
        relevance_score=0.9,
        rerank_score=None,
    )


def test_mmr_diversify_filters_and_orders() -> None:
    chunks = (_chunk("1", "alpha text"), _chunk("2", "alpha repeat"), _chunk("3", "beta text"))
    vecs = [
        np.array([1.0, 0.0, 0.0]),
        np.array([0.99, 0.01, 0.0]),
        np.array([0.2, 0.98, 0.0]),
    ]
    q_vec = np.array([1.0, 0.0, 0.0])

    selected = mmr_diversify(
        chunks=chunks,
        chunk_vectors=vecs,
        query_vector=q_vec,
        top_k=2,
        lambda_mult=0.5,
    )

    assert len(selected) == 2
    # "1" selected first. For step 2:
    # "2" score: 0.5*0.99 - 0.5*0.99 = 0.0
    # "3" score: 0.5*0.2 - 0.5*(0.2) = 0.0 -> with lambda_mult=0.3:
    # "2" score: 0.3*0.99 - 0.7*0.99 = -0.396
    # "3" score: 0.3*0.2 - 0.7*0.2 = -0.08 (higher score -> chosen)
    selected_l3 = mmr_diversify(
        chunks=chunks,
        chunk_vectors=vecs,
        query_vector=q_vec,
        top_k=2,
        lambda_mult=0.3,
    )
    assert [c.chunk_id for c in selected_l3] == ["1", "3"]


def test_rule_based_query_transformer() -> None:
    transformer = RuleBasedQueryTransformer(enable_hyde=True, num_expansions=3, num_hyde=3)
    res = asyncio.run(transformer.transform("xin nghỉ phép", knowledge_gaps=("quy trình",)))

    assert res.original_query == "xin nghỉ phép"
    assert "xin nghỉ phép quy trình" in res.expanded_queries
    assert len(res.hypothetical_docs) == 3
    assert "Tài liệu quy định chi tiết" in res.hypothetical_docs[0]


def test_llm_query_transformer() -> None:
    class DummyLLM:
        async def generate(self, prompt: str) -> Any:
            class Resp:
                text = '["HyDE doc 1", "HyDE doc 2", "HyDE doc 3"]'

            return Resp()

    transformer = LLMQueryTransformer(DummyLLM(), enable_hyde=True, num_expansions=3, num_hyde=3)
    res = asyncio.run(transformer.transform("xin nghỉ phép", knowledge_gaps=("quy trình",)))

    assert res.original_query == "xin nghỉ phép"
    assert len(res.expanded_queries) == 3
    assert res.hypothetical_docs == ("HyDE doc 1", "HyDE doc 2", "HyDE doc 3")


def test_hybrid_with_multi_query_and_mmr() -> None:
    chunks = (
        KnowledgeChunk(
            chunk_id="c1",
            document_id="d1",
            document_title="D1",
            section=None,
            text="alpha quy trình xin nghỉ phép",
            source_url="d1.md",
        ),
        KnowledgeChunk(
            chunk_id="c2",
            document_id="d2",
            document_title="D2",
            section=None,
            text="beta quy trình bàn giao công việc",
            source_url="d2.md",
        ),
    )
    embedder = FakeEmbedder()
    transformer = RuleBasedQueryTransformer(enable_hyde=True)

    memory = HybridSemanticMemory(
        documents=(KnowledgeDocument("k", "K", "k.md", chunks),),
        embedder=embedder,
        query_transformer=transformer,
        enable_mmr=True,
        min_score_default=0.0,
    )
    asyncio.run(memory.build_index())

    request = SemanticRetrievalRequest(
        run_id="r1",
        user_id="u1",
        query="nghỉ phép",
        knowledge_gaps=("quy trình",),
        filters=RetrievalFilters(document_status=("ready",)),
        limits=RetrievalLimits(top_k=2, min_score=0.0, timeout_ms=1000),
    )

    response = asyncio.run(memory.retrieve(request))
    assert response.retrieval_status is RetrievalStatus.SUCCESS
    assert len(response.chunks) > 0
