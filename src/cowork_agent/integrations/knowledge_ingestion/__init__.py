"""Adapters and immutable contracts for administrator knowledge ingestion."""

from .models import (
    ExtractionResult,
    IngestionOutcome,
    ManifestEntry,
    OcrPage,
    PdfInspection,
    PdfKind,
)

__all__ = [
    "ExtractionResult",
    "IngestionOutcome",
    "ManifestEntry",
    "OcrPage",
    "PdfInspection",
    "PdfKind",
]
