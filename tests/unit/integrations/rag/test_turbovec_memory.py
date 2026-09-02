"""Unit tests for TurbovecSemanticMemory adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import date

import pytest

from cowork_agent.domain.target_contracts import (
    RetrievalFilters,
    RetrievalLimits,
    RetrievalStatus,
    SemanticRetrievalRequest,
)
from cowork_agent.integrations.rag.knowledge_base import KnowledgeChunk, KnowledgeDocument
from cowork_agent.integrations.rag.turbovec_memory import TURBOVEC_AVAILABLE, TurbovecSemanticMemory

pytestmark = pytest.mark.skipif(not TURBOVEC_AVAILABLE, reason="turbovec package not installed")


class DummyEmbedder:
    """Mock embedder returning 3D vectors (requires dim padding to 8D)."""

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        results: list[tuple[float, ...]] = []
        for text in texts:
            if "alpha" in text:
                results.append((1.0, 0.0, 0.0))
            elif "beta" in text:
                results.append((0.0, 1.0, 0.0))
            else:
                results.append((0.0, 0.0, 1.0))
        return tuple(results)


def _make_document(doc_id: str, texts: list[str]) -> KnowledgeDocument:
    chunks = tuple(
        KnowledgeChunk(
            chunk_id=f"{doc_id}#{idx}",
            document_id=doc_id,
            document_title=doc_id.upper(),
            section=None,
            text=text,
            source_url=f"{doc_id}.md",
        )
        for idx, text in enumerate(texts)
    )
    return KnowledgeDocument(
        document_id=doc_id,
        title=doc_id.upper(),
        source_url=f"{doc_id}.md",
        chunks=chunks,
    )


def test_turbovec_build_and_retrieve() -> None:
    doc = _make_document("doc1", ["alpha project guidelines", "beta server setup"])
    embedder = DummyEmbedder()
    memory = TurbovecSemanticMemory([doc], embedder, bit_width=4)

    asyncio.run(memory.build_index())

    request = SemanticRetrievalRequest(
        run_id="run_1",
        user_id="user_1",
        query="alpha project",
        knowledge_gaps=(),
        filters=RetrievalFilters(document_status=()),
        limits=RetrievalLimits(top_k=2, min_score=0.1, timeout_ms=5000),
    )

    response = asyncio.run(memory.retrieve(request))
    assert response.retrieval_status == RetrievalStatus.SUCCESS
    assert len(response.chunks) >= 1
    assert response.chunks[0].chunk_id == "doc1#0"
    assert "alpha" in response.chunks[0].text


def test_turbovec_snapshot_persistence(tmp_path_factory) -> None:
    tmp_dir = tmp_path_factory.mktemp("turbovec_test")
    snapshot_file = tmp_dir / "test_snapshot.tvim"
    doc = _make_document("doc1", ["alpha documentation", "beta manual"])
    embedder = DummyEmbedder()

    # First instance builds index and saves snapshot
    mem1 = TurbovecSemanticMemory([doc], embedder, bit_width=4, index_path=snapshot_file)
    asyncio.run(mem1.build_index())
    assert snapshot_file.exists()

    # Second instance loads directly from snapshot
    mem2 = TurbovecSemanticMemory([doc], embedder, bit_width=4, index_path=snapshot_file)
    asyncio.run(mem2.build_index())

    request = SemanticRetrievalRequest(
        run_id="run_4",
        user_id="user_1",
        query="beta manual",
        knowledge_gaps=(),
        filters=RetrievalFilters(document_status=()),
        limits=RetrievalLimits(top_k=1, min_score=0.1, timeout_ms=5000),
    )

    response = asyncio.run(mem2.retrieve(request))
    assert response.retrieval_status == RetrievalStatus.SUCCESS
    assert response.chunks[0].chunk_id == "doc1#1"


class CountingDummyEmbedder(DummyEmbedder):
    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        self.calls += 1
        return await super().embed(texts)


def test_turbovec_retrieve_with_years_on_undated_corpus_returns_no_results_without_embed() -> None:
    doc = _make_document("doc1", ["alpha project guidelines"])
    embedder = CountingDummyEmbedder()
    memory = TurbovecSemanticMemory([doc], embedder, bit_width=4)
    asyncio.run(memory.build_index())
    embeds_after_build = embedder.calls

    request = SemanticRetrievalRequest(
        run_id="run_5",
        user_id="user_1",
        query="alpha project",
        knowledge_gaps=(),
        filters=RetrievalFilters(years=(1999,)),
        limits=RetrievalLimits(top_k=2, min_score=0.1, timeout_ms=5000),
    )

    response = asyncio.run(memory.retrieve(request))

    assert response.retrieval_status == RetrievalStatus.NO_RESULTS
    assert response.chunks == ()
    assert embedder.calls == embeds_after_build


def test_turbovec_retrieve_copies_document_date_onto_semantic_chunk() -> None:
    dated = date(2026, 8, 7)
    chunk = KnowledgeChunk(
        chunk_id="doc1#0",
        document_id="doc1",
        document_title="DOC1",
        section=None,
        text="alpha project guidelines",
        source_url="doc1.md",
        document_date=dated,
    )
    doc = KnowledgeDocument("doc1", "DOC1", "doc1.md", (chunk,))
    memory = TurbovecSemanticMemory([doc], DummyEmbedder(), bit_width=4)
    asyncio.run(memory.build_index())

    request = SemanticRetrievalRequest(
        run_id="run_6",
        user_id="user_1",
        query="alpha project",
        knowledge_gaps=(),
        filters=RetrievalFilters(document_status=()),
        limits=RetrievalLimits(top_k=1, min_score=0.1, timeout_ms=5000),
    )

    response = asyncio.run(memory.retrieve(request))
    assert response.chunks
    assert response.chunks[0].document_date == dated
