"""Guarded extraction and page-aware chunking for private project sources."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .docx_extractor import DocxExtractor
from .pdf_inspector import PdfInspector

_CHUNK_CHARS = 1_600
_DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class ProjectDocumentExtractionError(RuntimeError):
    """Safe reason code for a source that must never be partially indexed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ExtractedProjectDocument:
    page_count: int
    chunks: tuple[tuple[str, int, int], ...]


class ProjectDocumentExtractor:
    """Reuse native PDF/DOCX guards without writing extracted text to Postgres."""

    def __init__(
        self,
        docx_extractor: DocxExtractor | None = None,
        pdf_inspector: PdfInspector | None = None,
    ) -> None:
        self._docx_extractor = docx_extractor or DocxExtractor()
        self._pdf_inspector = pdf_inspector or PdfInspector()

    def extract(self, path: Path, media_type: str) -> ExtractedProjectDocument:
        try:
            pages: tuple[tuple[int, str], ...]
            if media_type == _DOCX_MEDIA_TYPE:
                result = self._docx_extractor.extract(path)
                pages = ((1, result.markdown),)
            elif media_type == "application/pdf":
                inspection = self._pdf_inspector.inspect(path)
                if inspection.pages_needing_ocr:
                    raise ProjectDocumentExtractionError("ocr_not_configured")
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
        chunks = tuple(
            (text, page, page)
            for page, markdown in pages
            for text in _chunk_page(markdown)
        )
        if not chunks:
            raise ProjectDocumentExtractionError("empty_extraction")
        return ExtractedProjectDocument(page_count=len(pages), chunks=chunks)


def _chunk_page(markdown: str) -> tuple[str, ...]:
    normalized = re.sub(r"\s+", " ", markdown).strip()
    if not normalized:
        return ()
    return tuple(
        normalized[index : index + _CHUNK_CHARS]
        for index in range(0, len(normalized), _CHUNK_CHARS)
    )
