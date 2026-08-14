"""Tests for the Qdrant Semantic Memory adapter (Qdrant migration, Tasks B/C).

Every test runs against ``AsyncQdrantClient(":memory:")`` with the
deterministic ``HashingEmbedder``: no network, no API key, no cloud project.
The embedder is a bag-of-tokens hash, not a semantic model, so assertions
cover ACL, thresholds, and limits — never which document ranks first.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import PayloadSchemaType

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


class _RecordingEmbedder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def embed(
        self, texts: tuple[str, ...], *, task: str = "retrieval.query"
    ) -> tuple[tuple[float, ...], ...]:
        del task
        self.calls.append(tuple(texts))
        return ((1.0, 0.0),)


class _RecordingQdrantClient:
    def __init__(self) -> None:
        self.query_calls: list[dict[str, object]] = []
        self.created_indexes: list[dict[str, object]] = []
        self.created_collections: list[dict[str, object]] = []
        self.upserted_points: list[object] = []

    async def query_points(self, **kwargs: object) -> SimpleNamespace:
        self.query_calls.append(kwargs)
        return SimpleNamespace(points=[])

    async def collection_exists(self, collection_name: str) -> bool:
        return False

    async def create_collection(self, **kwargs: object) -> None:
        self.created_collections.append(kwargs)

    async def create_payload_index(self, **kwargs: object) -> None:
        self.created_indexes.append(kwargs)

    async def upsert(self, *, collection_name: str, points: list[object]) -> None:
        self.upserted_points.extend(points)


def _request(
    *,
    query: str = "đăng ký tạm trú",
    top_k: int = 5,
    min_score: float = 0.0,
    timeout_ms: int = 1500,
) -> SemanticRetrievalRequest:
    return SemanticRetrievalRequest(
        run_id="run-1",
        user_id="user@example.com",
        query=query,
        knowledge_gaps=(),
        filters=RetrievalFilters(document_status=("ready",)),
        limits=RetrievalLimits(top_k=top_k, min_score=min_score, timeout_ms=timeout_ms),
    )


async def _memory(
    client: AsyncQdrantClient,
    *,
    embedder: HashingEmbedder | SlowEmbedder | None = None,
) -> QdrantSemanticMemory:
    documents = load_corpus(CORPUS_DIR)
    await ingest_corpus(client, COLLECTION, documents, HashingEmbedder())
    return QdrantSemanticMemory(client, COLLECTION, embedder or HashingEmbedder())


def test_ingest_corpus_creates_the_collection_and_upserts_every_chunk() -> None:
    async def scenario() -> None:
        client = AsyncQdrantClient(":memory:")
        documents = load_corpus(CORPUS_DIR)
        expected = sum(len(document.chunks) for document in documents)

        count = await ingest_corpus(client, COLLECTION, documents, HashingEmbedder())

        assert count == expected
        assert await client.collection_exists(COLLECTION)
        assert (await client.count(COLLECTION)).count == expected

    asyncio.run(scenario())


def test_ingest_corpus_is_idempotent_for_the_same_corpus() -> None:
    async def scenario() -> None:
        client = AsyncQdrantClient(":memory:")
        documents = load_corpus(CORPUS_DIR)

        first = await ingest_corpus(client, COLLECTION, documents, HashingEmbedder())
        second = await ingest_corpus(client, COLLECTION, documents, HashingEmbedder())

        assert first == second
        assert (await client.count(COLLECTION)).count == second

    asyncio.run(scenario())


def test_ingest_corpus_rejects_a_vector_size_the_embedder_cannot_produce() -> None:
    async def scenario() -> None:
        client = AsyncQdrantClient(":memory:")
        documents = load_corpus(CORPUS_DIR)

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


def test_retrieve_returns_chunks_grounded_in_the_ingested_corpus() -> None:
    async def scenario() -> None:
        memory = await _memory(AsyncQdrantClient(":memory:"))

        response = await memory.retrieve(_request())

        assert response.retrieval_status is RetrievalStatus.SUCCESS
        assert response.chunks
        for chunk in response.chunks:
            assert chunk.chunk_id and chunk.document_id and chunk.text
            assert chunk.source_url.startswith("data/extracted/")
            assert chunk.rerank_score is None

    asyncio.run(scenario())


def test_retrieve_denies_an_empty_status_allowlist_before_embedding() -> None:
    async def scenario() -> None:
        embedder = _RecordingEmbedder()
        client = _RecordingQdrantClient()
        memory = QdrantSemanticMemory(client, COLLECTION, embedder)  # type: ignore[arg-type]
        request = SemanticRetrievalRequest(
            run_id="run-1",
            user_id="user@example.com",
            query="approved policy",
            knowledge_gaps=(),
            filters=RetrievalFilters(document_status=()),
            limits=RetrievalLimits(top_k=5, min_score=0.0, timeout_ms=1500),
        )

        response = await memory.retrieve(request)

        assert response.retrieval_status is RetrievalStatus.AUTHORIZATION_DENIED
        assert embedder.calls == []
        assert client.query_calls == []

    asyncio.run(scenario())


@pytest.mark.parametrize("statuses", [("published",), ("ready", "published")])
def test_retrieve_denies_nonapproved_status_allowlists_before_embedding(
    statuses: tuple[str, ...],
) -> None:
    async def scenario() -> None:
        embedder = _RecordingEmbedder()
        client = _RecordingQdrantClient()
        memory = QdrantSemanticMemory(client, COLLECTION, embedder)  # type: ignore[arg-type]
        request = _request()
        request = SemanticRetrievalRequest(
            run_id=request.run_id,
            user_id=request.user_id,
            query=request.query,
            knowledge_gaps=request.knowledge_gaps,
            filters=RetrievalFilters(document_status=statuses),
            limits=request.limits,
        )

        response = await memory.retrieve(request)

        assert response.retrieval_status is RetrievalStatus.AUTHORIZATION_DENIED
        assert embedder.calls == []
        assert client.query_calls == []

    asyncio.run(scenario())


def test_retrieve_builds_status_filter_and_propagates_limits() -> None:
    async def scenario() -> None:
        embedder = _RecordingEmbedder()
        client = _RecordingQdrantClient()
        memory = QdrantSemanticMemory(client, COLLECTION, embedder)
        request = _request(top_k=3, min_score=0.7)
        request = SemanticRetrievalRequest(
            run_id=request.run_id,
            user_id=request.user_id,
            query=request.query,
            knowledge_gaps=request.knowledge_gaps,
            filters=RetrievalFilters(
                document_status=("ready",),
            ),
            limits=request.limits,
        )

        await memory.retrieve(request)

        call = client.query_calls[0]
        assert call["limit"] == 3
        assert call["score_threshold"] == 0.7
        assert call["timeout"] == 1
        query_filter = call["query_filter"]
        assert query_filter.must is not None
        assert len(query_filter.must) == 1
        (status_condition,) = query_filter.must
        assert status_condition.key == "document_status"
        assert status_condition.match.any == ["ready"]

    asyncio.run(scenario())


def test_ingest_stamps_ready_status_indexes_filters_and_allowlisted_payload() -> None:
    async def scenario() -> None:
        client = _RecordingQdrantClient()
        documents = load_corpus(CORPUS_DIR)

        count = await ingest_corpus(client, COLLECTION, documents, HashingEmbedder())

        assert count == len(client.upserted_points)
        assert {call["field_name"] for call in client.created_indexes} == {
            "document_status",
        }
        assert all(
            call["field_schema"] is PayloadSchemaType.KEYWORD
            for call in client.created_indexes
        )
        assert client.upserted_points
        for point in client.upserted_points:
            assert set(point.payload) == {
                "document_status",
                "chunk_id",
                "document_id",
                "document_title",
                "section",
                "text",
                "source_url",
            }
            assert point.payload["document_status"] == "ready"

    asyncio.run(scenario())


def test_retrieve_reports_timeout_when_the_embedder_times_out() -> None:
    async def scenario() -> None:
        memory = await _memory(AsyncQdrantClient(":memory:"), embedder=SlowEmbedder())

        response = await memory.retrieve(_request())

        assert response.retrieval_status is RetrievalStatus.TIMEOUT
        assert response.chunks == ()

    asyncio.run(scenario())


def test_retrieve_enforces_a_subsecond_deadline_across_embedding_and_query() -> None:
    class DelayedEmbedder:
        async def embed(
            self, texts: tuple[str, ...], *, task: str = "retrieval.query"
        ) -> tuple[tuple[float, ...], ...]:
            del texts, task
            await asyncio.sleep(0.05)
            return ((1.0, 0.0),)

    async def scenario() -> None:
        client = _RecordingQdrantClient()
        memory = QdrantSemanticMemory(client, COLLECTION, DelayedEmbedder())  # type: ignore[arg-type]

        response = await memory.retrieve(_request(timeout_ms=10))

        assert response.retrieval_status is RetrievalStatus.TIMEOUT
        assert client.query_calls == []

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

        assert response.retrieval_status is RetrievalStatus.TIMEOUT
        assert response.chunks == ()

    asyncio.run(scenario())
