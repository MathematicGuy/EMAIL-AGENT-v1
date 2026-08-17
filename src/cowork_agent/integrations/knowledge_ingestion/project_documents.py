"""Guarded extraction and page-aware chunking for private project sources."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cowork_agent.integrations.rag.markdown_chunking import MarkdownPage, chunk_markdown_pages
from cowork_agent.integrations.rag.structure_normalizer import normalize_structure

from .docx_extractor import DocxExtractor
from .ocr import MistralOcrExtractor
from .pdf_inspector import PdfInspector

_DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class ProjectDocumentExtractionError(RuntimeError):
    """Safe reason code for a source that must never be partially indexed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ExtractedProjectDocument:
    page_count: int
    chunks: tuple[ExtractedProjectDocumentChunk, ...]


@dataclass(frozen=True, slots=True)
class ExtractedProjectDocumentChunk:
    text: str
    page_start: int
    page_end: int
    section: str | None


class ProjectDocumentExtractor:
    """Reuse native PDF/DOCX guards without writing extracted text to Postgres."""

    def __init__(
        self,
        docx_extractor: DocxExtractor | None = None,
        pdf_inspector: PdfInspector | None = None,
        ocr_extractor: MistralOcrExtractor | None = None,
    ) -> None:
        self._docx_extractor = docx_extractor or DocxExtractor()
        self._pdf_inspector = pdf_inspector or PdfInspector()
        self._ocr_extractor = ocr_extractor

    def extract(self, path: Path, media_type: str) -> ExtractedProjectDocument:
        try:
            pages: tuple[tuple[int, str], ...]
            if self._ocr_extractor is not None and media_type in (
                _DOCX_MEDIA_TYPE,
                "application/pdf",
            ):
                markdown = self._ocr_extractor.extract(path.name, path.read_bytes())
                pages = ((1, markdown),)
            elif media_type == _DOCX_MEDIA_TYPE:
                result = self._docx_extractor.extract(path)
                pages = ((1, result.markdown),)
            elif media_type == "application/pdf":
                inspection = self._pdf_inspector.inspect(path)
                if inspection.pages_needing_ocr:
                    raise ProjectDocumentExtractionError("ocr_unavailable")
                pages = tuple(
                    (number, inspection.native_markdown_by_page.get(number, ""))
                    for number in range(1, inspection.page_count + 1)
                )
            else:
                raise ProjectDocumentExtractionError("unsupported_media_type")
        except ProjectDocumentExtractionError:
            raise
        except Exception as exc:
            raise ProjectDocumentExtractionError("native_extraction_failed") from exc
        shared_chunks = chunk_markdown_pages(
            MarkdownPage(markdown=normalize_structure(markdown), page_number=page)
            for page, markdown in pages
        )
        chunks = tuple(
            ExtractedProjectDocumentChunk(
                text=chunk.text,
                page_start=chunk.page_start or 1,
                page_end=chunk.page_end or 1,
                section=chunk.section,
            )
            for chunk in shared_chunks
        )
        if not chunks:
            raise ProjectDocumentExtractionError("empty_extraction")
        return ExtractedProjectDocument(page_count=len(pages), chunks=chunks)
