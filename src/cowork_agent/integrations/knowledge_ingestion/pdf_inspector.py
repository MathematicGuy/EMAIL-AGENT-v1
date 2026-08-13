"""Safe local adapter for PDF classification and native Markdown extraction."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TypeAlias

from pdf_inspector import PagesExtractionResult, PdfResult, detect_pdf, extract_pages_markdown

from .models import PdfInspection, PdfKind

PdfDetector: TypeAlias = Callable[[str], PdfResult]
MarkdownExtractor: TypeAlias = Callable[[str, list[int] | None], PagesExtractionResult]


class PdfInspector:
    """Classify a PDF and extract Markdown for its native-text pages."""

    def __init__(
        self,
        detector: PdfDetector | None = None,
        markdown_extractor: MarkdownExtractor | None = None,
    ) -> None:
        self._detector = detector or detect_pdf
        self._markdown_extractor = markdown_extractor or extract_pages_markdown

    def inspect(self, path: Path) -> PdfInspection:
        try:
            detection = self._detector(str(path))
            kind = PdfKind(detection.pdf_type)
            page_count = detection.page_count
            ocr_pages = tuple(detection.pages_needing_ocr)
        except (TypeError, ValueError) as error:
            raise ValueError("PDF inspection output is invalid") from error
        if page_count < 1 or any(not 1 <= page <= page_count for page in ocr_pages):
            raise ValueError("PDF inspection output is invalid")
        if len(set(ocr_pages)) != len(ocr_pages):
            raise ValueError("PDF inspection output is invalid")
        native_pages = tuple(
            number for number in range(1, page_count + 1) if number not in ocr_pages
        )
        markdown_by_page = self._extract_native_markdown(path, native_pages)
        return PdfInspection(
            kind=kind,
            page_count=page_count,
            pages_needing_ocr=ocr_pages,
            native_markdown_by_page=markdown_by_page,
        )

    def _extract_native_markdown(self, path: Path, pages: tuple[int, ...]) -> Mapping[int, str]:
        if not pages:
            return {}
        try:
            extraction = self._markdown_extractor(str(path), [page - 1 for page in pages])
        except Exception as error:
            raise RuntimeError("PDF Markdown extraction failed") from error
        markdown_by_page: dict[int, str] = {}
        for result in extraction.pages:
            number = result.page + 1
            markdown = result.markdown.strip()
            if (
                number not in pages
                or number in markdown_by_page
                or result.needs_ocr
                or not markdown
            ):
                raise ValueError("PDF Markdown output is invalid")
            markdown_by_page[number] = markdown
        if set(markdown_by_page) != set(pages):
            raise ValueError("PDF Markdown output is invalid")
        return markdown_by_page
