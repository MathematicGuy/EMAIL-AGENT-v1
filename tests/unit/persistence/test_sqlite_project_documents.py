"""SQLite chunks retain the existing Gemini/hybrid retrieval contract."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cowork_agent.domain.project_documents import ProjectDocumentQuery
from cowork_agent.identity import VerifiedPrincipal
from cowork_agent.integrations.rag.project_documents import (
    CanonicalProjectDocumentRetriever,
    HybridProjectDocumentStore,
    ProjectDocumentChunk,
)
from cowork_agent.persistence.repositories.sqlite_project_document_chunks import (
    SQLiteProjectDocumentChunkRepository,
)
from cowork_agent.persistence.repositories.sqlite_projects import SQLiteProjectRepository


class RecordingEmbedder:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], str]] = []

    async def embed(
        self, texts: tuple[str, ...], *, task: str = "retrieval.query"
    ) -> tuple[tuple[float, ...], ...]:
        self.calls.append((texts, task))
        return tuple((1.0, 0.0) for _ in texts)


class LocalIndex:
    def __init__(self) -> None:
        self.vector_ids: tuple[int, ...] = ()

    async def add(self, **kwargs: object) -> None:
        vector_ids = kwargs["vector_ids"]
        assert isinstance(vector_ids, list)
        self.vector_ids = tuple(vector_ids)

    async def remove(self, **kwargs: object) -> None:
        del kwargs

    async def search(self, **kwargs: object) -> tuple[tuple[int, float], ...]:
        allowlist = kwargs["allowlist"]
        assert isinstance(allowlist, tuple)
        return tuple((vector_id, 0.95) for vector_id in allowlist)


def test_sqlite_chunks_keep_gemini_hybrid_retrieval_and_acl(tmp_path: Path) -> None:
    async def scenario() -> None:
        metadata = tmp_path / "projects.db"
        projects = SQLiteProjectRepository(metadata)
        chunks = SQLiteProjectDocumentChunkRepository(tmp_path / "chunks.db", metadata)
        await projects.initialize()
        await chunks.initialize()
        principal = VerifiedPrincipal(user_id="owner")
        project = await projects.default_project(principal)
        document, created = await projects.create_or_get_document(
            principal=principal,
            project_id=project.id,
            filename="policy.pdf",
            media_type="application/pdf",
            byte_size=10,
            content_sha256="a" * 64,
            expires_in_seconds=86_400,
        )
        assert created
        await projects.mark_upload_completed(principal, project.id, document.id)
        assert await projects.claim_job(document.id) is not None
        assert await projects.transition_document(
            document.id, from_status="extracting", to_status="indexing"
        )

        embedder = RecordingEmbedder()
        vectors = HybridProjectDocumentStore(
            chunks,
            LocalIndex(),  # type: ignore[arg-type]
            embedder,
            vector_size=2,
        )
        await vectors.index(
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            project_id=project.id,
            document_id=document.id,
            filename=document.filename,
            expires_at=datetime.now(UTC) + timedelta(days=1),
            chunks=(
                ProjectDocumentChunk(
                    chunk_id="chunk-1",
                    text="Remote work policy for 2026",
                    page_start=1,
                    page_end=1,
                ),
            ),
        )
        assert await projects.transition_document(
            document.id,
            from_status="indexing",
            to_status="ready",
            page_count=1,
            chunk_count=1,
        )

        response = await CanonicalProjectDocumentRetriever(
            projects,
            vectors,
            top_k=5,
            min_score=0.2,
            timeout_ms=1_000,
        ).retrieve(
            ProjectDocumentQuery(
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                project_id=project.id,
                query="remote policy",
            )
        )

        assert [item.document_id for item in response.evidence] == [document.id]
        assert embedder.calls == [
            (("Remote work policy for 2026",), "retrieval.passage"),
            (("remote policy",), "retrieval.query"),
        ]

    asyncio.run(scenario())

def test_sqlite_document_job_retries_after_indexing_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        projects = SQLiteProjectRepository(tmp_path / "projects.db")
        await projects.initialize()
        principal = VerifiedPrincipal(user_id="owner")
        project = await projects.default_project(principal)
        document, _ = await projects.create_or_get_document(
            principal=principal,
            project_id=project.id,
            filename="policy.pdf",
            media_type="application/pdf",
            byte_size=10,
            content_sha256="b" * 64,
            expires_in_seconds=86_400,
        )
        await projects.mark_upload_completed(principal, project.id, document.id)
        assert await projects.claim_job(document.id) is not None
        assert await projects.transition_document(
            document.id, from_status="extracting", to_status="indexing"
        )

        assert await projects.retry_job(
            document.id,
            from_status="indexing",
            error_code="index_unavailable",
            max_attempts=3,
            delay_seconds=1,
        )
        retried = await projects.require_document(principal, project.id, document.id)
        assert retried is not None
        assert retried.status == "received"
        assert await projects.next_claimable_job() is None

    asyncio.run(scenario())


def test_sqlite_document_reupload_after_deletion_and_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        projects = SQLiteProjectRepository(tmp_path / "projects.db")
        await projects.initialize()
        principal = VerifiedPrincipal(user_id="owner")
        project = await projects.default_project(principal)

        # 1. Create and delete document
        doc1, created1 = await projects.create_or_get_document(
            principal=principal,
            project_id=project.id,
            filename="policy.pdf",
            media_type="application/pdf",
            byte_size=10,
            content_sha256="c" * 64,
            expires_in_seconds=86_400,
        )
        assert created1
        await projects.begin_deletion(principal, project.id, doc1.id)

        # Re-uploading the same content after deletion must succeed with a new active document
        doc2, created2 = await projects.create_or_get_document(
            principal=principal,
            project_id=project.id,
            filename="policy.pdf",
            media_type="application/pdf",
            byte_size=10,
            content_sha256="c" * 64,
            expires_in_seconds=86_400,
        )
        assert created2
        assert doc2.status == "received"

        # 2. Re-uploading after failure must reset status to received and allow re-ingestion
        await projects.mark_upload_completed(principal, project.id, doc2.id)
        assert await projects.claim_job(doc2.id) is not None
        await projects.finish_job(doc2.id, status="failed", error_code="index_unavailable")
        await projects.transition_document(
            doc2.id, from_status="extracting", to_status="failed", error_code="index_unavailable"
        )

        doc3, created3 = await projects.create_or_get_document(
            principal=principal,
            project_id=project.id,
            filename="policy.pdf",
            media_type="application/pdf",
            byte_size=10,
            content_sha256="c" * 64,
            expires_in_seconds=86_400,
        )
        assert created3
        assert doc3.id == doc2.id
        assert doc3.status == "received"
        assert doc3.error_code is None

    asyncio.run(scenario())
