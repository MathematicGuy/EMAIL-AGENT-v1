"""Tests for the Qdrant Semantic Memory adapter (Qdrant migration, Tasks B/C).

Every test runs against ``AsyncQdrantClient(":memory:")`` with the
deterministic ``HashingEmbedder``: no network, no API key, no cloud project.
The embedder is a bag-of-tokens hash, not a semantic model, so assertions
cover ACL, thresholds, and limits — never which document ranks first.
"""

import asyncio
from pathlib import Path

import pytest
from qdrant_client import AsyncQdrantClient

from cowork_agent.domain.target_contracts import (
    RetrievalFilters,
    RetrievalLimits,
    RetrievalStatus,
    SemanticRetrievalRequest,
)
from cowork_agent.integrations.rag.fakes import HashingEmbedder, SlowEmbedder
from cowork_agent.integrations.rag.knowledge_base import load_corpus
from cowork_agent.integrations.rag.qdrant import QdrantSemanticMemory, ingest_corpus

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = REPO_ROOT / "data" / "extracted"
COLLECTION = "test_company_knowledge"


def _request(
    *,
    tenant_scope: str = "local",
    query: str = "đăng ký tạm trú",
    top_k: int = 5,
    min_score: float = 0.0,
) -> SemanticRetrievalRequest:
    return SemanticRetrievalRequest(
        run_id="run-1",
        tenant_id="local",
        user_id="user@example.com",
        query=query,
        knowledge_gaps=(),
        filters=RetrievalFilters(tenant_scope=tenant_scope, document_status=("ready",)),
        limits=RetrievalLimits(top_k=top_k, min_score=min_score, timeout_ms=1500),
    )


async def _memory(
    client: AsyncQdrantClient,
    *,
    tenant_id: str = "local",
    embedder: HashingEmbedder | SlowEmbedder | None = None,
) -> QdrantSemanticMemory:
    documents = load_corpus(CORPUS_DIR, tenant_id=tenant_id)
    await ingest_corpus(client, COLLECTION, documents, HashingEmbedder())
    return QdrantSemanticMemory(client, COLLECTION, embedder or HashingEmbedder())


def test_ingest_corpus_creates_the_collection_and_upserts_every_chunk() -> None:
    async def scenario() -> None:
        client = AsyncQdrantClient(":memory:")
        documents = load_corpus(CORPUS_DIR, tenant_id="local")
        expected = sum(len(document.chunks) for document in documents)

        count = await ingest_corpus(client, COLLECTION, documents, HashingEmbedder())

        assert count == expected
        assert await client.collection_exists(COLLECTION)
        assert (await client.count(COLLECTION)).count == expected

    asyncio.run(scenario())


def test_ingest_corpus_is_idempotent_for_the_same_corpus() -> None:
    async def scenario() -> None:
        client = AsyncQdrantClient(":memory:")
        documents = load_corpus(CORPUS_DIR, tenant_id="local")

        first = await ingest_corpus(client, COLLECTION, documents, HashingEmbedder())
        second = await ingest_corpus(client, COLLECTION, documents, HashingEmbedder())

        assert first == second
        assert (await client.count(COLLECTION)).count == second

    asyncio.run(scenario())


def test_ingest_corpus_rejects_a_vector_size_the_embedder_cannot_produce() -> None:
    async def scenario() -> None:
        client = AsyncQdrantClient(":memory:")
        documents = load_corpus(CORPUS_DIR, tenant_id="local")

        with pytest.raises(ValueError, match="does not match the embedder"):
            await ingest_corpus(
                client, COLLECTION, documents, HashingEmbedder(), vector_size=768
            )

    asyncio.run(scenario())


def test_ingest_corpus_rejects_an_empty_corpus() -> None:
    async def scenario() -> None:
        client = AsyncQdrantClient(":memory:")

        with pytest.raises(ValueError, match="non-empty corpus"):
            await ingest_corpus(client, COLLECTION, (), HashingEmbedder())

    asyncio.run(scenario())


def test_retrieve_returns_grounded_chunks_for_the_owning_tenant() -> None:
    async def scenario() -> None:
        memory = await _memory(AsyncQdrantClient(":memory:"))

        response = await memory.retrieve(_request())

        assert response.retrieval_status is RetrievalStatus.SUCCESS
        assert response.chunks
        assert response.tenant_id == "local"
        for chunk in response.chunks:
            assert chunk.chunk_id and chunk.document_id and chunk.text
            assert chunk.source_url.startswith("data/extracted/")
            assert chunk.rerank_score is None

    asyncio.run(scenario())


def test_retrieve_isolates_tenants_via_the_payload_filter() -> None:
    async def scenario() -> None:
        memory = await _memory(AsyncQdrantClient(":memory:"), tenant_id="tenant-a")

        response = await memory.retrieve(_request(tenant_scope="tenant-b"))

        assert response.retrieval_status is RetrievalStatus.NO_RESULTS
        assert response.chunks == ()

    asyncio.run(scenario())


def test_retrieve_denies_an_empty_tenant_scope_without_embedding() -> None:
    async def scenario() -> None:
        # SlowEmbedder raises on any call, so reaching it would fail the test.
        memory = await _memory(AsyncQdrantClient(":memory:"), embedder=SlowEmbedder())

        response = await memory.retrieve(_request(tenant_scope=""))

        assert response.retrieval_status is RetrievalStatus.AUTHORIZATION_DENIED
        assert response.chunks == ()

    asyncio.run(scenario())


def test_retrieve_reports_timeout_when_the_embedder_times_out() -> None:
    async def scenario() -> None:
        memory = await _memory(AsyncQdrantClient(":memory:"), embedder=SlowEmbedder())

        response = await memory.retrieve(_request())

        assert response.retrieval_status is RetrievalStatus.TIMEOUT
        assert response.chunks == ()

    asyncio.run(scenario())


def test_retrieve_truncates_to_top_k() -> None:
    async def scenario() -> None:
        memory = await _memory(AsyncQdrantClient(":memory:"))

        response = await memory.retrieve(_request(top_k=2))

        assert len(response.chunks) <= 2

    asyncio.run(scenario())


def test_retrieve_applies_the_min_score_threshold() -> None:
    async def scenario() -> None:
        memory = await _memory(AsyncQdrantClient(":memory:"))

        unfiltered = await memory.retrieve(_request(top_k=20, min_score=0.0))
        assert unfiltered.chunks
        threshold = max(chunk.relevance_score for chunk in unfiltered.chunks)

        filtered = await memory.retrieve(_request(top_k=20, min_score=threshold))

        assert all(chunk.relevance_score >= threshold for chunk in filtered.chunks)
        assert len(filtered.chunks) < len(unfiltered.chunks) or len(unfiltered.chunks) == 1

    asyncio.run(scenario())


def test_retrieve_degrades_to_empty_results_when_the_collection_is_missing() -> None:
    async def scenario() -> None:
        client = AsyncQdrantClient(":memory:")
        memory = QdrantSemanticMemory(client, "never_created", HashingEmbedder())

        response = await memory.retrieve(_request())

        assert response.retrieval_status is RetrievalStatus.NO_RESULTS
        assert response.chunks == ()

    asyncio.run(scenario())
