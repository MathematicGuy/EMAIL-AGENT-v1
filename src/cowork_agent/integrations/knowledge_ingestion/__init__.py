"""Adapters and immutable contracts for administrator knowledge ingestion."""

from .models import (
    ExtractionResult,
    IngestionOutcome,
    ManifestEntry,
    OcrPage,
    PdfInspection,
    PdfKind,
)
from .service import KnowledgeIngestionService

__all__ = [
    "ExtractionResult",
    "IngestionOutcome",
    "KnowledgeIngestionService",
    "ManifestEntry",
    "OcrPage",
    "PdfInspection",
    "PdfKind",
]
