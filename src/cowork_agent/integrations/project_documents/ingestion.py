"""Lease-safe extraction, OCR, chunking and indexing for one Project document."""

from __future__ import annotations

import asyncio
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from cowork_agent.domain.project_documents import (
    ProjectDocument,
    ProjectDocumentChunk,
    ProjectDocumentFailureReason,
    ProjectDocumentMediaType,
    ProjectDocumentStatus,
)
from cowork_agent.features.user_documents.ports import ProjectDocumentRepositoryPort
from cowork_agent.integrations.knowledge_ingestion.docx_extractor import DocxExtractor
from cowork_agent.integrations.knowledge_ingestion.models import OcrPage
from cowork_agent.integrations.knowledge_ingestion.pdf_inspector import PdfInspector

from .chunking import chunk_pages, render_pages
from .encrypted_store import EncryptedDocumentStore
from .qdrant_store import DocumentEmbeddingError, DocumentVectorStoreError


class OcrClientPort(Protocol):
    async def extract_pages(
        self, pdf_bytes: bytes, pages: tuple[int, ...]
    ) -> tuple[OcrPage, ...]: ...


class DocumentVectorStorePort(Protocol):
    async def upsert_chunks(
        self, chunks: tuple[ProjectDocumentChunk, ...], *, expires_at: datetime
    ) -> int: ...
    async def activate(self, document_id: str) -> None: ...
    async def delete_document(self, document_id: str) -> None: ...


class ProjectDocumentIngestionService:
    def __init__(
        self,
        *,
        documents: ProjectDocumentRepositoryPort,
        store: EncryptedDocumentStore,
        vectors: DocumentVectorStorePort,
        pdf_inspector: PdfInspector | None = None,
        docx_extractor: DocxExtractor | None = None,
        ocr: OcrClientPort | None = None,
        max_pages: int = 100,
        lease_seconds: int = 300,
    ) -> None:
        self._documents = documents
        self._store = store
        self._vectors = vectors
        self._pdf = pdf_inspector or PdfInspector()
        self._docx = docx_extractor or DocxExtractor()
        self._ocr = ocr
        self._max_pages = max_pages
        self._lease_seconds = lease_seconds

    async def process(self, document: ProjectDocument, *, worker_id: str) -> ProjectDocument | None:
        now = datetime.now(UTC)
        if not await self._documents.claim(
            document.document_id,
            worker_id=worker_id,
            now=now,
            lease_until=now + timedelta(seconds=self._lease_seconds),
        ):
            return None
        try:
            current = document
            if current.status is ProjectDocumentStatus.RECEIVED:
                transitioned = await self._documents.transition(
                    document.document_id,
                    from_statuses=(ProjectDocumentStatus.RECEIVED,),
                    to_status=ProjectDocumentStatus.EXTRACTING,
                    at=datetime.now(UTC),
                )
                if transitioned is None:
                    return None
                current = transitioned
            pages, ocr_count = await self._extract(current)
            chunks = chunk_pages(
                document_id=document.document_id,
                project_id=document.project_id,
                tenant_id=document.tenant_id,
                user_id=document.user_id,
                pages=pages,
            )
            self._store.put_markdown(document.document_id, render_pages(pages))
            if current.status is ProjectDocumentStatus.EXTRACTING:
                indexing = await self._documents.transition(
                    document.document_id,
                    from_statuses=(ProjectDocumentStatus.EXTRACTING,),
                    to_status=ProjectDocumentStatus.INDEXING,
                    at=datetime.now(UTC),
                    page_count=len(pages),
                    chunk_count=len(chunks),
                    ocr_page_count=ocr_count,
                )
                if indexing is None:
                    return None
            await self._vectors.upsert_chunks(chunks, expires_at=document.expires_at)
            # Points remain non-retrievable while their payload status is
            # ``indexing``.  Activate them before committing metadata READY so
            # an activation failure can still transition INDEXING -> FAILED.
            await self._vectors.activate(document.document_id)
            ready = await self._documents.transition(
                document.document_id,
                from_statuses=(ProjectDocumentStatus.INDEXING,),
                to_status=ProjectDocumentStatus.READY,
                at=datetime.now(UTC),
                page_count=len(pages),
                chunk_count=len(chunks),
                ocr_page_count=ocr_count,
            )
            if ready is None:
                return None
            return ready
        except _IngestionFailure as exc:
            return await self._fail(document.document_id, exc.reason)
        except DocumentEmbeddingError:
            return await self._fail(
                document.document_id, ProjectDocumentFailureReason.EMBEDDING_FAILED
            )
        except DocumentVectorStoreError:
            return await self._fail(
                document.document_id,
                ProjectDocumentFailureReason.VECTOR_STORE_UNAVAILABLE,
            )
        except Exception:
            return await self._fail(
                document.document_id, ProjectDocumentFailureReason.INTERNAL_ERROR
            )

    async def _extract(self, document: ProjectDocument) -> tuple[dict[int, str], int]:
        source = self._store.read_source(document.document_id)
        suffix = ".pdf" if document.media_type is ProjectDocumentMediaType.PDF else ".docx"
        path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
                handle.write(source)
                path = Path(handle.name)
            if document.media_type is ProjectDocumentMediaType.DOCX:
                try:
                    result = await asyncio.to_thread(self._docx.extract, path)
                except Exception as exc:
                    raise _IngestionFailure(ProjectDocumentFailureReason.INVALID_DOCX) from exc
                if not result.markdown.strip():
                    raise _IngestionFailure(ProjectDocumentFailureReason.EMPTY_DOCUMENT)
                return {1: result.markdown}, 0
            try:
                inspection = await asyncio.to_thread(self._pdf.inspect, path)
            except Exception as exc:
                raise _IngestionFailure(ProjectDocumentFailureReason.INVALID_PDF) from exc
            if inspection.page_count > self._max_pages:
                raise _IngestionFailure(ProjectDocumentFailureReason.PAGE_LIMIT_EXCEEDED)
            pages = dict(inspection.native_markdown_by_page)
            if inspection.pages_needing_ocr:
                if self._ocr is None:
                    raise _IngestionFailure(ProjectDocumentFailureReason.OCR_UNAVAILABLE)
                try:
                    ocr_pages = await self._ocr.extract_pages(source, inspection.pages_needing_ocr)
                except Exception as exc:
                    raise _IngestionFailure(ProjectDocumentFailureReason.OCR_INCOMPLETE) from exc
                if {page.number for page in ocr_pages} != set(inspection.pages_needing_ocr):
                    raise _IngestionFailure(ProjectDocumentFailureReason.OCR_INCOMPLETE)
                pages.update({page.number: page.markdown for page in ocr_pages})
            if set(pages) != set(range(1, inspection.page_count + 1)) or any(
                not value.strip() for value in pages.values()
            ):
                raise _IngestionFailure(ProjectDocumentFailureReason.EMPTY_DOCUMENT)
            return pages, len(inspection.pages_needing_ocr)
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

    async def _fail(
        self, document_id: str, reason: ProjectDocumentFailureReason
    ) -> ProjectDocument | None:
        return await self._documents.transition(
            document_id,
            from_statuses=(
                ProjectDocumentStatus.RECEIVED,
                ProjectDocumentStatus.EXTRACTING,
                ProjectDocumentStatus.INDEXING,
            ),
            to_status=ProjectDocumentStatus.FAILED,
            at=datetime.now(UTC),
            reason_code=reason,
        )


class _IngestionFailure(Exception):
    def __init__(self, reason: ProjectDocumentFailureReason) -> None:
        self.reason = reason
