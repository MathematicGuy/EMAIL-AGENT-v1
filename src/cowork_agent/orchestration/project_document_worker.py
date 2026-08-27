"""Durable worker for private project source extraction and vector indexing."""

from __future__ import annotations

import hashlib
import logging
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from cowork_agent.integrations.knowledge_ingestion.project_documents import (
    ExtractedProjectDocument,
    ProjectDocumentExtractionError,
)
from cowork_agent.integrations.rag.project_documents import ProjectDocumentChunk
from cowork_agent.integrations.storage.supabase import StorageUnavailable
from cowork_agent.observability import (
    ProjectDocumentTimingOutcome,
    log_project_document_timing,
    safe_provider_label,
)
from cowork_agent.persistence.repositories.projects import ProjectDocument

logger = logging.getLogger(__name__)


class ProjectDocumentRepository(Protocol):
    async def claim_job(self, document_id: str) -> ProjectDocument | None: ...

    async def transition_document(
        self,
        document_id: str,
        *,
        from_status: str,
        to_status: str,
        page_count: int | None = None,
        ocr_page_count: int | None = None,
        chunk_count: int | None = None,
        error_code: str | None = None,
    ) -> bool: ...

    async def finish_job(
        self, document_id: str, *, status: str, error_code: str | None = None
    ) -> bool: ...

    async def retry_job(
        self,
        document_id: str,
        *,
        from_status: str,
        error_code: str,
        max_attempts: int,
        delay_seconds: int,
    ) -> bool: ...


class ProjectDocumentCleanupRepository(Protocol):
    async def claim_cleanup(self, document_id: str) -> ProjectDocument | None: ...

    async def finish_cleanup(self, document_id: str, *, error_code: str | None = None) -> bool: ...


class PrivateSourceStorage(Protocol):
    async def download_to(self, object_key: str, target: Path) -> None: ...

    async def delete(self, object_key: str) -> None: ...


class DocumentExtractor(Protocol):
    def extract(self, path: Path, media_type: str) -> ExtractedProjectDocument: ...


class ProjectVectorStore(Protocol):
    async def index(
        self,
        *,
        workspace_id: str,
        user_id: str,
        project_id: str,
        document_id: str,
        filename: str,
        expires_at: datetime,
        chunks: tuple[ProjectDocumentChunk, ...],
    ) -> int: ...

    async def delete_document(
        self,
        *,
        workspace_id: str,
        user_id: str,
        project_id: str,
        document_id: str,
    ) -> bool: ...


class ProjectDocumentIngestionWorker:
    """Stateful source handling; private bytes live only in a temporary file."""

    def __init__(
        self,
        repository: ProjectDocumentRepository,
        storage: PrivateSourceStorage,
        extractor: DocumentExtractor,
        vectors: ProjectVectorStore,
        *,
        max_pages: int = 100,
        max_attempts: int = 3,
        retry_delay_seconds: int = 30,
    ) -> None:
        if max_attempts < 1 or retry_delay_seconds < 1:
            raise ValueError("ingestion retry limits must be positive")
        self._repository = repository
        self._storage = storage
        self._extractor = extractor
        self._vectors = vectors
        self._max_pages = max_pages
        self._max_attempts = max_attempts
        self._retry_delay_seconds = retry_delay_seconds

    async def execute(self, document_id: str) -> None:
        document = await self._repository.claim_job(document_id)
        if document is None:
            return
        worker_started = perf_counter()
        worker_outcome: ProjectDocumentTimingOutcome = "error"
        state = "extracting"
        vectors_written = False
        try:
            with tempfile.TemporaryDirectory(prefix="cowork-project-doc-") as directory:
                source = Path(directory) / _source_name(document.media_type)
                stage_started = perf_counter()
                stage_outcome: ProjectDocumentTimingOutcome = "error"
                try:
                    await self._storage.download_to(document.storage_key, source)
                    stage_outcome = "success"
                finally:
                    log_project_document_timing(
                        logger,
                        stage="source_download",
                        started=stage_started,
                        outcome=stage_outcome,
                        document_id=document.id,
                        provider=safe_provider_label(self._storage),
                    )
                _verify_source(source, document)
                stage_started = perf_counter()
                stage_outcome = "error"
                try:
                    extracted = self._extractor.extract(source, document.media_type)
                    if extracted.page_count > self._max_pages:
                        raise ProjectDocumentExtractionError("page_limit_exceeded")
                    chunks = tuple(
                        ProjectDocumentChunk(
                            chunk_id=str(
                                uuid5(
                                    NAMESPACE_URL,
                                    ":".join(
                                        (
                                            document.id,
                                            str(index),
                                            chunk.section or "",
                                            str(chunk.page_start),
                                            str(chunk.page_end),
                                            chunk.text,
                                        )
                                    ),
                                )
                            ),
                            text=chunk.text,
                            page_start=chunk.page_start,
                            page_end=chunk.page_end,
                            section=chunk.section,
                        )
                        for index, chunk in enumerate(extracted.chunks)
                    )
                    if not chunks:
                        raise ProjectDocumentExtractionError("empty_extraction")
                    stage_outcome = "success"
                finally:
                    log_project_document_timing(
                        logger,
                        stage="extraction_chunking",
                        started=stage_started,
                        outcome=stage_outcome,
                        document_id=document.id,
                        provider=safe_provider_label(self._extractor),
                    )
                if not await self._repository.transition_document(
                    document.id, from_status="extracting", to_status="indexing"
                ):
                    return
                state = "indexing"
                count = await self._vectors.index(
                    workspace_id=document.workspace_id,
                    user_id=document.user_id,
                    project_id=document.project_id,
                    document_id=document.id,
                    filename=document.filename,
                    expires_at=document.expires_at,
                    chunks=chunks,
                )
                vectors_written = True
                # Readiness is no longer published into the vector store: the
                # ACL query joins project_documents.status, so this transition
                # is the only thing that makes a document retrievable.
                stage_started = perf_counter()
                stage_outcome = "error"
                try:
                    ready = await self._repository.transition_document(
                        document.id,
                        from_status="indexing",
                        to_status="ready",
                        page_count=extracted.page_count,
                        chunk_count=count,
                    )
                    if ready:
                        stage_outcome = "success"
                finally:
                    log_project_document_timing(
                        logger,
                        stage="ready_transition",
                        started=stage_started,
                        outcome=stage_outcome,
                        document_id=document.id,
                        provider=safe_provider_label(self._repository),
                    )
                if ready:
                    await self._repository.finish_job(document.id, status="completed")
                    worker_outcome = "success"
                else:
                    await self._delete_vectors_best_effort(document)
        except ProjectDocumentExtractionError as exc:
            logger.warning(
                "Project document extraction failed; document_id=%s error_code=%s",
                document.id,
                exc.code,
            )
            if vectors_written:
                await self._delete_vectors_best_effort(document)
            await self._fail(document.id, state, exc.code)
        except StorageUnavailable as exc:
            logger.warning(
                "Project document storage operation failed; document_id=%s error_type=%s",
                document.id,
                type(exc).__name__,
            )
            if vectors_written:
                await self._delete_vectors_best_effort(document)
            await self._retry_or_fail(document.id, state, "source_download_failed")
        except (OSError, ValueError) as exc:
            logger.warning(
                "Project document indexing failed; document_id=%s error_type=%s",
                document.id,
                type(exc).__name__,
            )
            if vectors_written:
                await self._delete_vectors_best_effort(document)
            if state == "indexing":
                await self._retry_or_fail(document.id, state, "index_unavailable")
            else:
                await self._fail(document.id, state, "ingestion_failed")
        except Exception as exc:
            logger.warning(
                "Project document processing failed; document_id=%s state=%s error_type=%s",
                document.id,
                state,
                type(exc).__name__,
            )
            if vectors_written:
                await self._delete_vectors_best_effort(document)
            await self._retry_or_fail(
                document.id,
                state,
                "index_unavailable" if state == "indexing" else "source_download_failed",
            )
        finally:
            log_project_document_timing(
                logger,
                stage="worker_execution",
                started=worker_started,
                outcome=worker_outcome,
                document_id=document.id,
            )

    async def _retry_or_fail(self, document_id: str, state: str, code: str) -> None:
        if await self._repository.retry_job(
            document_id,
            from_status=state,
            error_code=code,
            max_attempts=self._max_attempts,
            delay_seconds=self._retry_delay_seconds,
        ):
            return
        await self._fail(document_id, state, code)

    async def _fail(self, document_id: str, state: str, code: str) -> None:
        if await self._repository.transition_document(
            document_id, from_status=state, to_status="failed", error_code=code
        ):
            await self._repository.finish_job(document_id, status="failed", error_code=code)

    async def _delete_vectors(self, document: ProjectDocument) -> None:
        await self._vectors.delete_document(
            workspace_id=document.workspace_id,
            user_id=document.user_id,
            project_id=document.project_id,
            document_id=document.id,
        )

    async def _delete_vectors_best_effort(self, document: ProjectDocument) -> None:
        try:
            await self._delete_vectors(document)
        except Exception:
            # PostgreSQL readiness remains authoritative. A deterministic retry
            # overwrites the same point IDs; durable deletion also purges them.
            return


class ProjectDocumentCleanupWorker:
    """Durably purge a retrieval-ineligible document from both private stores."""

    def __init__(
        self,
        repository: ProjectDocumentCleanupRepository,
        storage: PrivateSourceStorage,
        vectors: ProjectVectorStore,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._vectors = vectors

    async def execute(self, document_id: str) -> None:
        document = await self._repository.claim_cleanup(document_id)
        if document is None:
            return
        try:
            await self._vectors.delete_document(
                workspace_id=document.workspace_id,
                user_id=document.user_id,
                project_id=document.project_id,
                document_id=document.id,
            )
            await self._storage.delete(document.storage_key)
        except Exception:
            await self._repository.finish_cleanup(
                document.id, error_code="cleanup_dependency_failed"
            )
            return
        await self._repository.finish_cleanup(document.id)


def _source_name(media_type: str) -> str:
    if media_type == "application/pdf":
        return "source.pdf"
    if media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return "source.docx"
    raise ProjectDocumentExtractionError("unsupported_media_type")


def _verify_source(path: Path, document: ProjectDocument) -> None:
    try:
        if path.stat().st_size != document.byte_size:
            raise ProjectDocumentExtractionError("source_metadata_mismatch")
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
    except OSError as exc:
        raise ProjectDocumentExtractionError("source_download_failed") from exc
    if digest != document.content_sha256:
        raise ProjectDocumentExtractionError("source_metadata_mismatch")
    _verify_media_type(path, document.media_type)


def _verify_media_type(path: Path, declared_media_type: str) -> None:
    try:
        if declared_media_type == "application/pdf":
            with path.open("rb") as source:
                if source.read(5) != b"%PDF-":
                    raise ProjectDocumentExtractionError("source_media_type_mismatch")
            return
        if declared_media_type == (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ):
            if not zipfile.is_zipfile(path):
                raise ProjectDocumentExtractionError("source_media_type_mismatch")
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise ProjectDocumentExtractionError("source_media_type_mismatch")
            return
    except ProjectDocumentExtractionError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise ProjectDocumentExtractionError("source_media_type_mismatch") from exc
    raise ProjectDocumentExtractionError("unsupported_media_type")
