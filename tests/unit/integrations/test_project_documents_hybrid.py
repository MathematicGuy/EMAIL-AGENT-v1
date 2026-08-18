"""Private project retrieval stays inside one project, one user, one workspace.

Replaces the pre-ADR-008 Qdrant suite. The isolation argument changed shape: a
payload filter no longer separates tenants, a per-project ``.tvim`` plus a SQL
allowlist does. These tests pin both halves -- the six ACL conditions Postgres
must apply, and the fact that the dense leg only ever sees IDs that survived it.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from cowork_agent.domain.project_documents import ProjectDocumentQuery
from cowork_agent.integrations.rag.project_documents import (
    CanonicalProjectDocumentRetriever,
    HybridProjectDocumentStore,
    ProjectDocumentChunk,
    ProjectDocumentEvidence,
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
    """Stands in for Postgres: records the ACL arguments, returns fixed IDs."""

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
    """Stands in for the per-project ``.tvim``."""

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


def test_retrieval_applies_every_acl_condition_before_embedding() -> None:
    async def scenario() -> None:
        chunks = FakeChunks()
        embedder = RecordingEmbedder()
        now = datetime.now(UTC)
        await _store(chunks, FakeIndexes(), embedder).retrieve(
            query="policy",
            workspace_id=WORKSPACE,
            user_id=USER,
            project_id=PROJECT,
            now=now,
            document_ids=("document-1",),
        )

        # The SQL gate runs first; the query is embedded only afterwards.
        assert chunks.eligible_calls[0] == {
            "workspace_id": WORKSPACE,
            "user_id": USER,
            "project_id": PROJECT,
            "document_ids": ("document-1",),
            "now": now,
            "query": "policy",
            "lexical_limit": 20,
        }
        assert embedder.calls == [(("policy",), "retrieval.query")]

    asyncio.run(scenario())


def test_a_ranked_chunk_brings_the_rest_of_its_section_with_it() -> None:
    """An article cut across chunks must arrive whole, in reading order."""

    async def scenario() -> None:
        chunks = FakeChunks(allowlist=(11, 12), siblings=((11, 11), (11, 12), (12, 11), (12, 12)))
        evidence = await _store(chunks, FakeIndexes(), RecordingEmbedder()).retrieve(
            query="Điều 4 gồm những gì",
            workspace_id=WORKSPACE,
            user_id=USER,
            project_id=PROJECT,
            now=datetime.now(UTC),
            document_ids=("document-1",),
        )

        assert [item.chunk_id for item in evidence] == ["chunk-11", "chunk-12"]
        # Siblings are authorized by intersection, never by a second ACL pass.
        assert chunks.sibling_calls[0]["allowlist"] == (11, 12)

    asyncio.run(scenario())


def test_a_section_too_large_for_the_headroom_leaves_its_chunk_alone() -> None:
    """Widening must never evict a chunk the ranking actually chose."""

    async def scenario() -> None:
        oversized = tuple((11, sibling) for sibling in range(11, 40))
        chunks = FakeChunks(allowlist=(11, 12), siblings=oversized)
        evidence = await _store(chunks, FakeIndexes(), RecordingEmbedder()).retrieve(
            query="Điều 4 gồm những gì",
            workspace_id=WORKSPACE,
            user_id=USER,
            project_id=PROJECT,
            now=datetime.now(UTC),
            document_ids=("document-1",),
            limit=2,
        )

        # Fused order, untouched: neither chunk was widened, neither was dropped.
        assert [item.chunk_id for item in evidence] == ["chunk-12", "chunk-11"]

    asyncio.run(scenario())


def test_a_chunk_with_no_section_is_returned_on_its_own() -> None:
    async def scenario() -> None:
        chunks = FakeChunks(allowlist=(11, 12), siblings=())
        evidence = await _store(chunks, FakeIndexes(), RecordingEmbedder()).retrieve(
            query="policy",
            workspace_id=WORKSPACE,
            user_id=USER,
            project_id=PROJECT,
            now=datetime.now(UTC),
            document_ids=("document-1",),
        )

        assert [item.chunk_id for item in evidence] == ["chunk-12", "chunk-11"]

    asyncio.run(scenario())


def test_no_eligible_chunk_means_no_vector_search_at_all() -> None:
    """An empty allowlist must short-circuit: a .tvim search would be unfiltered."""

    async def scenario() -> None:
        chunks = FakeChunks(allowlist=(), lexical=())
        indexes = FakeIndexes()
        evidence = await _store(chunks, indexes, RecordingEmbedder()).retrieve(
            query="policy",
            workspace_id=WORKSPACE,
            user_id=USER,
            project_id=PROJECT,
            now=datetime.now(UTC),
            document_ids=("document-1",),
        )

        assert evidence == ()
        assert indexes.searches == []

    asyncio.run(scenario())


def test_dense_leg_only_sees_ids_that_passed_the_sql_gate() -> None:
    async def scenario() -> None:
        chunks = FakeChunks(allowlist=(11, 12), lexical=(12,))
        indexes = FakeIndexes()
        evidence = await _store(chunks, indexes, RecordingEmbedder()).retrieve(
            query="policy",
            workspace_id=WORKSPACE,
            user_id=USER,
            project_id=PROJECT,
            now=datetime.now(UTC),
            document_ids=("document-1",),
        )

        search = indexes.searches[0]
        assert search["project_id"] == PROJECT
        assert search["allowlist"] == (11, 12)
        assert {item.chunk_id for item in evidence} == {"chunk-11", "chunk-12"}

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("workspace_id", "user_id", "project_id"),
    [("", USER, PROJECT), (WORKSPACE, "", PROJECT), (WORKSPACE, USER, "")],
)
def test_retrieval_refuses_an_incomplete_tenant_scope(
    workspace_id: str, user_id: str, project_id: str
) -> None:
    async def scenario() -> None:
        with pytest.raises(ValueError):
            await _store(FakeChunks(), FakeIndexes(), RecordingEmbedder()).retrieve(
                query="policy",
                workspace_id=workspace_id,
                user_id=user_id,
                project_id=project_id,
                now=datetime.now(UTC),
                document_ids=("document-1",),
            )

    asyncio.run(scenario())


def test_indexing_persists_text_before_vectors_and_scopes_the_index_by_project() -> None:
    async def scenario() -> None:
        chunks = FakeChunks()
        indexes = FakeIndexes()
        count = await _store(chunks, indexes, RecordingEmbedder()).index(
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
        # Postgres assigns vector_id, so it must be written first.
        assert chunks.replaced[0]["document_id"] == "document-1"
        assert indexes.added[0]["project_id"] == PROJECT
        assert indexes.added[0]["vector_ids"] == [11, 12]

    asyncio.run(scenario())


def test_deletion_purges_the_index_before_the_text_it_points_at() -> None:
    async def scenario() -> None:
        chunks = FakeChunks()
        indexes = FakeIndexes()
        assert await _store(chunks, indexes, RecordingEmbedder()).delete_document(
            workspace_id=WORKSPACE,
            user_id=USER,
            project_id=PROJECT,
            document_id="document-1",
        )

        assert indexes.removed == [{"project_id": PROJECT, "vector_ids": (11, 12)}]
        assert chunks.deleted == ["document-1"]

    asyncio.run(scenario())


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


def test_retry_reuses_the_authorized_query_vector() -> None:
    class Repository:
        def __init__(self) -> None:
            self.calls: list[tuple[object, ...]] = []

        async def list_ready_for_scope(
            self, *args: object, **kwargs: object
        ) -> tuple[ProjectDocument, ...]:
            del kwargs
            self.calls.append(args)
            return (_ready_document(),)

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
            # The lexical leg cannot work from an embedding alone.
            assert kwargs["query"] == "policy"
            self.query_calls += 1
            if self.query_calls == 1:
                raise OSError("transient index error")
            return ()

    async def scenario() -> None:
        repository = Repository()
        vectors = Vectors()
        retriever = CanonicalProjectDocumentRetriever(
            repository, vectors, top_k=5, min_score=0.2, timeout_ms=1_000  # type: ignore[arg-type]
        )
        response = await retriever.retrieve(
            ProjectDocumentQuery(
                user_id=USER,
                project_id=PROJECT,
                query="policy",
                document_ids=("document-1",),
            )
        )

        assert response.degraded is False
        assert vectors.embed_calls == 1
        assert vectors.query_calls == 2
        assert repository.calls == [
            ("local", USER, PROJECT),
            ("local", USER, PROJECT),
        ]

    asyncio.run(scenario())


def test_retriever_drops_evidence_deleted_during_vector_query() -> None:
    class Repository:
        def __init__(self) -> None:
            self.calls = 0

        async def list_ready_for_scope(
            self, *args: object, **kwargs: object
        ) -> tuple[ProjectDocument, ...]:
            del args, kwargs
            self.calls += 1
            return (_ready_document(),) if self.calls == 1 else ()

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
                user_id=USER,
                project_id=PROJECT,
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
        async def list_ready_for_scope(
            self, *args: object, **kwargs: object
        ) -> tuple[object, ...]:
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
                user_id=USER,
                project_id=PROJECT,
                query="policy",
            )
        )
        assert response.degraded is True
        assert response.reason_code == "retrieval_timeout"

    asyncio.run(scenario())


def test_missing_project_index_degrades_instead_of_rebuilding() -> None:
    from cowork_agent.integrations.rag.project_index import ProjectIndexUnavailable

    class Repository:
        async def list_ready_for_scope(
            self, *args: object, **kwargs: object
        ) -> tuple[ProjectDocument, ...]:
            del args, kwargs
            return (_ready_document(),)

    class Vectors:
        async def embed_query(self, query: str) -> tuple[float, ...]:
            del query
            return (1.0, 0.0)

        async def retrieve_vector(self, **kwargs: object) -> tuple[object, ...]:
            del kwargs
            raise ProjectIndexUnavailable("missing .tvim")

    async def scenario() -> None:
        retriever = CanonicalProjectDocumentRetriever(
            Repository(), Vectors(), top_k=5, min_score=0.2, timeout_ms=1_000  # type: ignore[arg-type]
        )
        response = await retriever.retrieve(
            ProjectDocumentQuery(
                user_id=USER,
                project_id=PROJECT,
                query="policy",
                document_ids=("document-1",),
            )
        )

        assert response.evidence == ()
        assert response.degraded is True
        assert response.reason_code == "index_unavailable"

    asyncio.run(scenario())
