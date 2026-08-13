"""Project document vectors remain isolated from company knowledge."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    Range,
    VectorParams,
)

from cowork_agent.domain.project_documents import ProjectDocumentQuery
from cowork_agent.integrations.rag.fakes import HashingEmbedder
from cowork_agent.integrations.rag.project_documents import (
    CanonicalProjectDocumentRetriever,
    ProjectDocumentChunk,
    ProjectDocumentEvidence,
    ProjectDocumentVectorStore,
)
from cowork_agent.persistence.repositories.projects import ProjectDocument


class RecordingEmbedder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def embed(
        self, texts: tuple[str, ...], *, task: str = "retrieval.query"
    ) -> tuple[tuple[float, ...], ...]:
        del task
        self.calls.append(texts)
        return ((1.0, 0.0),)


class RecordingClient:
    def __init__(self) -> None:
        self.query_calls: list[dict[str, object]] = []

    async def query_points(self, **kwargs: object) -> SimpleNamespace:
        self.query_calls.append(kwargs)
        return SimpleNamespace(points=[])


def test_project_retrieval_builds_all_acl_conditions_before_embedding() -> None:
    async def scenario() -> None:
        client = RecordingClient()
        embedder = RecordingEmbedder()
        store = ProjectDocumentVectorStore(
            client, "project_documents", embedder, vector_size=2
        )  # type: ignore[arg-type]

        result = await store.retrieve(
            query="quarterly forecast",
            workspace_id="workspace-1",
            user_id="user-1",
            project_id="project-1",
            now=datetime(2026, 8, 12, tzinfo=UTC),
            document_ids=("document-1",),
        )

        assert result == ()
        assert embedder.calls == [("quarterly forecast",)]
        query_filter = client.query_calls[0]["query_filter"]
        assert isinstance(query_filter, Filter)
        conditions = query_filter.must
        assert conditions is not None
        assert FieldCondition(
            key="workspace_id", match=MatchValue(value="workspace-1")
        ) in conditions
        assert FieldCondition(key="user_id", match=MatchValue(value="user-1")) in conditions
        assert FieldCondition(key="project_id", match=MatchValue(value="project-1")) in conditions
        assert FieldCondition(
            key="document_id", match=MatchAny(any=["document-1"])
        ) in conditions
        assert FieldCondition(
            key="document_status", match=MatchValue(value="ready")
        ) in conditions
        assert FieldCondition(key="expires_at_epoch", range=Range(gt=1786492800.0)) in conditions

    asyncio.run(scenario())


def test_project_vectors_use_a_separate_collection_and_delete_by_document_scope() -> None:
    async def scenario() -> None:
        from qdrant_client import AsyncQdrantClient

        client = AsyncQdrantClient(":memory:")
        store = ProjectDocumentVectorStore(
            client, "project_documents", HashingEmbedder(), vector_size=64
        )
        await store.index(
            workspace_id="workspace-1",
            user_id="user-1",
            project_id="project-1",
            document_id="document-1",
            filename="budget.docx",
            expires_at=datetime.now(UTC) + timedelta(days=1),
            chunks=(ProjectDocumentChunk("chunk-1", "Budget forecast", 1, 1),),
        )

        assert await client.collection_exists("project_documents")
        assert not await client.collection_exists("company_knowledge")
        assert await store.delete_document(
            workspace_id="workspace-1",
            user_id="user-1",
            project_id="project-1",
            document_id="document-1",
        )
        assert (await client.count("project_documents")).count == 0

    asyncio.run(scenario())


def test_project_vectors_are_not_retrievable_until_marked_ready() -> None:
    async def scenario() -> None:
        from qdrant_client import AsyncQdrantClient

        client = AsyncQdrantClient(":memory:")
        store = ProjectDocumentVectorStore(
            client, "project_documents", HashingEmbedder(), vector_size=64
        )
        scope = {
            "workspace_id": "workspace-1",
            "user_id": "user-1",
            "project_id": "project-1",
            "document_id": "document-1",
        }
        await store.index(
            **scope,
            filename="budget.docx",
            expires_at=datetime.now(UTC) + timedelta(days=1),
            chunks=(ProjectDocumentChunk("chunk-1", "Budget forecast", 1, 1),),
        )

        before = await store.retrieve(
            query="Budget forecast",
            **{key: scope[key] for key in ("workspace_id", "user_id", "project_id")},
            now=datetime.now(UTC),
            document_ids=(scope["document_id"],),
        )
        await store.mark_document_ready(**scope)
        after = await store.retrieve(
            query="Budget forecast",
            **{key: scope[key] for key in ("workspace_id", "user_id", "project_id")},
            now=datetime.now(UTC),
            document_ids=(scope["document_id"],),
        )

        assert before == ()
        assert len(after) == 1
        assert after[0].document_id == "document-1"

    asyncio.run(scenario())


def test_project_collection_rejects_a_wrong_dimension_at_startup() -> None:
    async def scenario() -> None:
        from qdrant_client import AsyncQdrantClient

        client = AsyncQdrantClient(":memory:")
        await client.create_collection(
            collection_name="project_documents",
            vectors_config=VectorParams(size=64, distance=Distance.COSINE),
        )
        store = ProjectDocumentVectorStore(
            client, "project_documents", HashingEmbedder(), vector_size=3072
        )

        with pytest.raises(ValueError, match="size=64"):
            await store.ensure_collection()

    asyncio.run(scenario())


def test_project_collection_backfills_indexes_for_an_existing_collection() -> None:
    async def scenario() -> None:
        from qdrant_client import AsyncQdrantClient

        client = AsyncQdrantClient(":memory:")
        await client.create_collection(
            collection_name="project_documents",
            vectors_config=VectorParams(size=64, distance=Distance.COSINE),
        )
        store = ProjectDocumentVectorStore(
            client, "project_documents", HashingEmbedder(), vector_size=64
        )

        await store.ensure_collection()
        await client.count(
            collection_name="project_documents",
            count_filter=Filter(must=[
                FieldCondition(key="workspace_id", match=MatchValue(value="workspace-1"))
            ]),
            exact=True,
        )

    asyncio.run(scenario())


def test_qdrant_retry_reuses_the_authorized_query_vector() -> None:
    class Repository:
        async def list_ready_for_scope(
            self, *args: object, **kwargs: object
        ) -> tuple[ProjectDocument, ...]:
            del args, kwargs
            return (
                ProjectDocument(
                    "document-1", "project-1", "user-1", "policy.pdf",
                    "application/pdf", 1, "0" * 64, "private/source", "ready",
                    datetime.now(UTC) + timedelta(days=1),
                ),
            )

    class Vectors:
        def __init__(self) -> None:
            self.embed_calls = 0
            self.query_calls = 0

        async def embed_query(self, query: str) -> tuple[float, ...]:
            assert query == "policy"
            self.embed_calls += 1
            return (1.0, 0.0)

        async def retrieve_vector(self, **kwargs: object) -> tuple[object, ...]:
            assert kwargs["vector"] == (1.0, 0.0)
            self.query_calls += 1
            if self.query_calls == 1:
                raise OSError("transient qdrant error")
            return ()

    async def scenario() -> None:
        vectors = Vectors()
        retriever = CanonicalProjectDocumentRetriever(
            Repository(), vectors, top_k=5, min_score=0.2, timeout_ms=1_000  # type: ignore[arg-type]
        )
        response = await retriever.retrieve(
            ProjectDocumentQuery(
                user_id="user-1",
                project_id="project-1",
                query="policy",
                document_ids=("document-1",),
            )
        )

        assert response.degraded is False
        assert vectors.embed_calls == 1
        assert vectors.query_calls == 2

    asyncio.run(scenario())


def test_retriever_drops_evidence_deleted_during_vector_query() -> None:
    ready_document = ProjectDocument(
        "document-1", "project-1", "user-1", "policy.pdf",
        "application/pdf", 1, "0" * 64, "private/source", "ready",
        datetime.now(UTC) + timedelta(days=1),
    )

    class Repository:
        def __init__(self) -> None:
            self.calls = 0

        async def list_ready_for_scope(
            self, *args: object, **kwargs: object
        ) -> tuple[ProjectDocument, ...]:
            del args, kwargs
            self.calls += 1
            return (ready_document,) if self.calls == 1 else ()

    class Vectors:
        async def embed_query(self, query: str) -> tuple[float, ...]:
            del query
            return (1.0, 0.0)

        async def retrieve_vector(self, **kwargs: object) -> tuple[ProjectDocumentEvidence, ...]:
            del kwargs
            return (
                ProjectDocumentEvidence(
                    "chunk-1", "document-1", "policy.pdf", "stale text", 1, 1, None, 0.9
                ),
            )

    async def scenario() -> None:
        repository = Repository()
        retriever = CanonicalProjectDocumentRetriever(
            repository, Vectors(), top_k=5, min_score=0.2, timeout_ms=1_000  # type: ignore[arg-type]
        )
        response = await retriever.retrieve(
            ProjectDocumentQuery(
                user_id="user-1",
                project_id="project-1",
                query="policy",
                document_ids=("document-1",),
            )
        )

        assert response.evidence == ()
        assert response.degraded is True
        assert response.reason_code == "document_not_ready"
        assert repository.calls == 2

    asyncio.run(scenario())


def test_retriever_enforces_one_deadline_across_postgres_and_vector_work() -> None:
    class SlowRepository:
        async def list_ready_for_scope(self, *args: object, **kwargs: object) -> tuple[object, ...]:
            del args, kwargs
            await asyncio.sleep(1)
            return ()

    class Vectors:
        async def embed_query(self, query: str) -> tuple[float, ...]:
            raise AssertionError(f"embedding must not run after PostgreSQL timeout: {query}")

    async def scenario() -> None:
        retriever = CanonicalProjectDocumentRetriever(
            SlowRepository(), Vectors(), top_k=5, min_score=0.2, timeout_ms=10  # type: ignore[arg-type]
        )
        response = await retriever.retrieve(
            ProjectDocumentQuery(
                user_id="user-1",
                project_id="project-1",
                query="policy",
            )
        )
        assert response.degraded is True
        assert response.reason_code == "retrieval_timeout"

    asyncio.run(scenario())
