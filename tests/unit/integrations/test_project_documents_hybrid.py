"""Private project retrieval stays inside one project, one user, one workspace."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pytest

from cowork_agent.domain.project_documents import ProjectDocumentQuery
from cowork_agent.integrations.rag.project_documents import (
    CanonicalProjectDocumentRetriever,
    HybridProjectDocumentStore,
    ProjectDocumentChunk,
)
from cowork_agent.persistence.repositories.project_document_chunks import (
    EligibleChunks,
    StoredChunk,
)
from cowork_agent.persistence.repositories.projects import ProjectDocument

WORKSPACE = "workspace-1"
USER = "user-1"
PROJECT = "project-1"


class RecordingEmbedder:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], str]] = []

    async def embed(
        self, texts: tuple[str, ...], *, task: str = "retrieval.query"
    ) -> tuple[tuple[float, ...], ...]:
        self.calls.append((texts, task))
        return tuple((1.0, 0.0) for _ in texts)


class FakeChunks:
    def __init__(
        self,
        *,
        allowlist: tuple[int, ...] = (11, 12),
        lexical: tuple[int, ...] = (12,),
        siblings: tuple[tuple[int, int], ...] = (),
    ) -> None:
        self._siblings = siblings
        self.sibling_calls: list[dict[str, object]] = []
        self.eligible_calls: list[dict[str, object]] = []
        self.replaced: list[dict[str, object]] = []
        self.deleted: list[str] = []
        self.hydrated: list[tuple[int, ...]] = []
        self._allowlist = allowlist
        self._lexical = lexical

    async def replace_document_chunks(self, **kwargs: object) -> tuple[tuple[str, int], ...]:
        self.replaced.append(kwargs)
        chunks = kwargs["chunks"]
        return tuple(
            (chunk.chunk_id, 11 + index)
            for index, chunk in enumerate(chunks)  # type: ignore[call-overload]
        )

    async def list_eligible(self, **kwargs: object) -> EligibleChunks:
        self.eligible_calls.append(kwargs)
        return EligibleChunks(allowlist=self._allowlist, lexical=self._lexical)

    async def list_section_siblings(
        self, *, vector_ids: tuple[int, ...], allowlist: tuple[int, ...]
    ) -> tuple[tuple[int, int], ...]:
        self.sibling_calls.append({"vector_ids": vector_ids, "allowlist": allowlist})
        return self._siblings

    async def hydrate(self, vector_ids: tuple[int, ...]) -> tuple[StoredChunk, ...]:
        self.hydrated.append(vector_ids)
        return tuple(
            StoredChunk(
                vector_id=vector_id,
                chunk_id=f"chunk-{vector_id}",
                document_id="document-1",
                filename="policy.pdf",
                text=f"text {vector_id}",
                page_start=1,
                page_end=1,
                section=None,
            )
            for vector_id in vector_ids
        )

    async def list_document_vector_ids(self, document_id: str) -> tuple[int, ...]:
        del document_id
        return self._allowlist

    async def delete_document_chunks(self, document_id: str) -> tuple[int, ...]:
        self.deleted.append(document_id)
        return self._allowlist


class FakeIndexes:
    def __init__(self) -> None:
        self.searches: list[dict[str, object]] = []
        self.added: list[dict[str, object]] = []
        self.removed: list[dict[str, object]] = []

    async def add(self, **kwargs: object) -> None:
        self.added.append(kwargs)

    async def remove(self, **kwargs: object) -> None:
        self.removed.append(kwargs)

    async def search(self, **kwargs: object) -> tuple[tuple[int, float], ...]:
        self.searches.append(kwargs)
        allowlist = kwargs["allowlist"]
        return tuple((vector_id, 0.9) for vector_id in allowlist)  # type: ignore[union-attr]


def _store(
    chunks: FakeChunks, indexes: FakeIndexes, embedder: RecordingEmbedder
) -> HybridProjectDocumentStore:
    return HybridProjectDocumentStore(
        chunks,  # type: ignore[arg-type]
        indexes,  # type: ignore[arg-type]
        embedder,  # type: ignore[arg-type]
        vector_size=2,
    )


def _ready_document() -> ProjectDocument:
    return ProjectDocument(
        id="document-1",
        project_id=PROJECT,
        workspace_id=WORKSPACE,
        user_id=USER,
        filename="policy.pdf",
        media_type="application/pdf",
        byte_size=1,
        content_sha256="0" * 64,
        storage_key="private/source",
        status="ready",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )


def test_project_documents_retrieval_acl_and_section_widening() -> None:
    async def scenario() -> None:
        chunks = FakeChunks(allowlist=(11, 12), siblings=((11, 11), (11, 12), (12, 11), (12, 12)))
        embedder = RecordingEmbedder()
        indexes = FakeIndexes()
        now = datetime.now(UTC)

        evidence = await _store(chunks, indexes, embedder).retrieve(
            query="policy",
            workspace_id=WORKSPACE,
            user_id=USER,
            project_id=PROJECT,
            now=now,
            document_ids=("document-1",),
        )

        assert chunks.eligible_calls[0]["workspace_id"] == WORKSPACE
        assert embedder.calls == [(("policy",), "retrieval.query")]
        assert indexes.searches[0]["allowlist"] == (11, 12)
        assert [item.chunk_id for item in evidence] == ["chunk-11", "chunk-12"]

    asyncio.run(scenario())


def test_project_documents_empty_scope_and_short_circuits() -> None:
    async def scenario() -> None:
        # Incomplete tenant scope raises ValueError
        for w, u, p in (("", USER, PROJECT), (WORKSPACE, "", PROJECT), (WORKSPACE, USER, "")):
            with pytest.raises(ValueError):
                await _store(FakeChunks(), FakeIndexes(), RecordingEmbedder()).retrieve(
                    query="policy",
                    workspace_id=w,
                    user_id=u,
                    project_id=p,
                    now=datetime.now(UTC),
                    document_ids=("document-1",),
                )

        # Empty allowlist short circuits
        empty_chunks = FakeChunks(allowlist=(), lexical=())
        empty_indexes = FakeIndexes()
        res = await _store(empty_chunks, empty_indexes, RecordingEmbedder()).retrieve(
            query="policy",
            workspace_id=WORKSPACE,
            user_id=USER,
            project_id=PROJECT,
            now=datetime.now(UTC),
            document_ids=("document-1",),
        )
        assert res == ()
        assert empty_indexes.searches == []

    asyncio.run(scenario())


def test_project_documents_indexing_lifecycle_and_deletion() -> None:
    async def scenario() -> None:
        chunks = FakeChunks()
        indexes = FakeIndexes()
        store = _store(chunks, indexes, RecordingEmbedder())

        count = await store.index(
            workspace_id=WORKSPACE,
            user_id=USER,
            project_id=PROJECT,
            document_id="document-1",
            filename="policy.pdf",
            expires_at=datetime.now(UTC) + timedelta(days=1),
            chunks=(
                ProjectDocumentChunk("chunk-a", "first page", 1, 1, None),
                ProjectDocumentChunk("chunk-b", "second page", 2, 2, None),
            ),
        )
        assert count == 2
        assert chunks.replaced[0]["document_id"] == "document-1"
        assert indexes.added[0]["vector_ids"] == [11, 12]

        # Deletion
        assert await store.delete_document(
            workspace_id=WORKSPACE,
            user_id=USER,
            project_id=PROJECT,
            document_id="document-1",
        )
        assert indexes.removed == [{"project_id": PROJECT, "vector_ids": (11, 12)}]
        assert chunks.deleted == ["document-1"]

    asyncio.run(scenario())


def test_project_documents_indexing_observability_and_timings(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        with caplog.at_level(logging.INFO):
            await _store(FakeChunks(), FakeIndexes(), RecordingEmbedder()).index(
                workspace_id=WORKSPACE,
                user_id=USER,
                project_id=PROJECT,
                document_id="document-1",
                filename="private-policy.pdf",
                expires_at=datetime.now(UTC) + timedelta(days=1),
                chunks=(ProjectDocumentChunk("chunk-secret", "private text", 1, 1),),
            )

        timing = [
            r.getMessage()
            for r in caplog.records
            if r.getMessage().startswith("project_document_ingestion_timing ")
        ]
        assert len(timing) == 2
        assert all(" document_id=document-1" in m for m in timing)
        assert all(" outcome=success" in m for m in timing)
        assert all("private text" not in m for m in timing)

    asyncio.run(scenario())


def test_canonical_project_document_retriever_resilience_and_timeouts() -> None:
    from cowork_agent.integrations.rag.project_index import ProjectIndexUnavailable

    class Repository:
        def __init__(self) -> None:
            self.calls = 0

        async def list_ready_for_scope(
            self, *args: object, **kwargs: object
        ) -> tuple[ProjectDocument, ...]:
            self.calls += 1
            return (_ready_document(),)

    class TransientVectors:
        def __init__(self) -> None:
            self.embed_calls = 0
            self.query_calls = 0

        async def embed_query(self, query: str) -> tuple[float, ...]:
            self.embed_calls += 1
            return (1.0, 0.0)

        async def retrieve_vector(self, **kwargs: object) -> tuple[object, ...]:
            self.query_calls += 1
            if self.query_calls == 1:
                raise OSError("transient index error")
            return ()

    class UnavailableVectors(TransientVectors):
        async def retrieve_vector(self, **kwargs: object) -> tuple[object, ...]:
            raise ProjectIndexUnavailable("missing .tvim")

    async def scenario() -> None:
        # Retry reuses vector
        repo = Repository()
        vec = TransientVectors()
        retriever = CanonicalProjectDocumentRetriever(
            repo, vec, top_k=5, min_score=0.2, timeout_ms=1000
        )  # type: ignore[arg-type]
        resp = await retriever.retrieve(
            ProjectDocumentQuery(
                user_id=USER, project_id=PROJECT, query="policy", document_ids=("document-1",)
            )
        )
        assert resp.degraded is False
        assert vec.embed_calls == 1 and vec.query_calls == 2

        # Index unavailable degrades
        unavail_retriever = CanonicalProjectDocumentRetriever(
            repo, UnavailableVectors(), top_k=5, min_score=0.2, timeout_ms=1000
        )  # type: ignore[arg-type]
        unavail_resp = await unavail_retriever.retrieve(
            ProjectDocumentQuery(
                user_id=USER, project_id=PROJECT, query="policy", document_ids=("document-1",)
            )
        )
        assert unavail_resp.degraded is True
        assert unavail_resp.reason_code == "index_unavailable"

    asyncio.run(scenario())
