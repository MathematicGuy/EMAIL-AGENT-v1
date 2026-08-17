"""Immutable data contracts shared by the knowledge ingestion workflow."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class PdfKind(StrEnum):
    TEXT_BASED = "text_based"
    SCANNED = "scanned"
    IMAGE_BASED = "image_based"
    MIXED = "mixed"


@dataclass(frozen=True, slots=True)
class PdfInspection:
    kind: PdfKind
    page_count: int
    pages_needing_ocr: tuple[int, ...]
    native_markdown_by_page: Mapping[int, str]


@dataclass(frozen=True, slots=True)
class OcrPage:
    number: int
    markdown: str


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    markdown: str
    page_count: int


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    source: str
    sha256: str
    status: str
    output: str
    extractor: str = ""
    page_count: int = 0
    processed_at: str = ""
    reason_code: str | None = None
    title: str = ""
    document_date: str = ""


@dataclass(frozen=True, slots=True)
class IngestionOutcome:
    source: str
    status: str
    output: str | None = None
    reason_code: str | None = None
