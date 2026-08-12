"""Project document vectors remain isolated from company knowledge."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from qdrant_client.models import FieldCondition, Filter, MatchValue, Range

from cowork_agent.integrations.rag.fakes import HashingEmbedder
from cowork_agent.integrations.rag.project_documents import (
    ProjectDocumentChunk,
    ProjectDocumentVectorStore,
)


class RecordingEmbedder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
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
        store = ProjectDocumentVectorStore(client, "project_documents", embedder)  # type: ignore[arg-type]

        result = await store.retrieve(
            query="quarterly forecast",
            workspace_id="workspace-1",
            user_id="user-1",
            project_id="project-1",
            now=datetime(2026, 8, 12, tzinfo=UTC),
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
        assert FieldCondition(key="document_status", match=MatchValue(value="ready")) in conditions
        assert FieldCondition(key="expires_at_epoch", range=Range(gt=1786492800.0)) in conditions

    asyncio.run(scenario())


def test_project_vectors_use_a_separate_collection_and_delete_by_document_scope() -> None:
    async def scenario() -> None:
        from qdrant_client import AsyncQdrantClient

        client = AsyncQdrantClient(":memory:")
        store = ProjectDocumentVectorStore(client, "project_documents", HashingEmbedder())
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
