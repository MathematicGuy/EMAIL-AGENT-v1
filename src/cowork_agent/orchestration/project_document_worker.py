"""Durable worker for private project source extraction and vector indexing."""

from __future__ import annotations

import hashlib
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Protocol

from cowork_agent.integrations.knowledge_ingestion.project_documents import (
    ExtractedProjectDocument,
    ProjectDocumentExtractionError,
)
from cowork_agent.integrations.rag.project_documents import ProjectDocumentChunk
from cowork_agent.integrations.storage.supabase import StorageUnavailable
from cowork_agent.persistence.repositories.projects import ProjectDocument


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


class PrivateSourceStorage(Protocol):
    async def download_to(self, object_key: str, target: Path) -> None: ...


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


class ProjectDocumentIngestionWorker:
    """Stateful source handling; private bytes live only in a temporary file."""

    def __init__(
        self,
        repository: ProjectDocumentRepository,
        storage: PrivateSourceStorage,
        extractor: DocumentExtractor,
        vectors: ProjectVectorStore,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._extractor = extractor
        self._vectors = vectors

    async def execute(self, document_id: str) -> None:
        document = await self._repository.claim_job(document_id)
        if document is None:
            return
        state = "extracting"
        try:
            with tempfile.TemporaryDirectory(prefix="cowork-project-doc-") as directory:
                source = Path(directory) / _source_name(document.media_type)
                await self._storage.download_to(document.storage_key, source)
                _verify_source(source, document)
                extracted = self._extractor.extract(source, document.media_type)
                chunks = tuple(
                    ProjectDocumentChunk(f"{index + 1}", text, start, end)
                    for index, (text, start, end) in enumerate(extracted.chunks)
                )
                if not chunks:
                    raise ProjectDocumentExtractionError("empty_extraction")
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
                if await self._repository.transition_document(
                    document.id,
                    from_status="indexing",
                    to_status="ready",
                    page_count=extracted.page_count,
                    chunk_count=count,
                ):
                    await self._repository.finish_job(document.id, status="completed")
        except ProjectDocumentExtractionError as exc:
            await self._fail(document.id, state, exc.code)
        except StorageUnavailable:
            await self._fail(document.id, state, "source_download_failed")
        except (OSError, ValueError):
            await self._fail(document.id, state, "ingestion_failed")

    async def _fail(self, document_id: str, state: str, code: str) -> None:
        if await self._repository.transition_document(
            document_id, from_status=state, to_status="failed", error_code=code
        ):
            await self._repository.finish_job(document_id, status="failed", error_code=code)


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
