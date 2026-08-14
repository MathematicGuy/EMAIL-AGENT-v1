"""Local PDF/DOCX ingestion orchestration for the Markdown RAG corpus."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from cowork_agent.config import KnowledgeIngestionSettings

from .docx_extractor import DocxExtractor
from .manifest import ManifestStore, sha256_file, write_markdown_atomically
from .models import IngestionOutcome, ManifestEntry, PdfInspection
from .ocr import MistralOcrExtractor
from .pdf_inspector import PdfInspector

_SUPPORTED_SUFFIXES = {".docx", ".pdf"}
_MANIFEST_NAME = "ingestion-manifest.json"


class KnowledgeIngestionService:
    """Convert local, administrator-supplied documents to a safe Markdown corpus."""

    def __init__(
        self,
        settings: KnowledgeIngestionSettings,
        docx_extractor: DocxExtractor | None = None,
        pdf_inspector: PdfInspector | None = None,
        ocr_extractor: MistralOcrExtractor | None = None,
    ) -> None:
        self._settings = settings
        self._docx_extractor = docx_extractor or DocxExtractor()
        self._pdf_inspector = pdf_inspector or PdfInspector()
        self._ocr_extractor = ocr_extractor

    def ingest(
        self,
        source_dir: Path,
        output_dir: Path,
        force: bool,
        *,
        dry_run: bool = False,
    ) -> tuple[IngestionOutcome, ...]:
        """Ingest supported source files, returning one outcome per discovered input."""
        source = source_dir.resolve()
        output = output_dir.resolve()
        _validate_directories(source, output)
        sources, rejected = _discover(source)
        collisions = _colliding_sources(sources, source)
        manifest = ManifestStore(output / _MANIFEST_NAME)
        outcomes: list[IngestionOutcome] = [
            IngestionOutcome(
                path.relative_to(source).as_posix(), "failed", reason_code="symlink_not_allowed"
            )
            for path in rejected
        ]
        for path in sources:
            relative = path.relative_to(source).as_posix()
            output_name = _output_name(path, source)
            if path in collisions:
                outcomes.append(
                    IngestionOutcome(relative, "failed", reason_code="output_name_collision")
                )
                continue
            outcomes.append(
                self._ingest_one(path, relative, output, output_name, manifest, force, dry_run)
            )
        return tuple(sorted(outcomes, key=lambda outcome: outcome.source))

    def _ingest_one(
        self,
        path: Path,
        relative: str,
        output_dir: Path,
        output_name: str,
        manifest: ManifestStore,
        force: bool,
        dry_run: bool,
    ) -> IngestionOutcome:
        try:
            size = path.stat().st_size
        except OSError:
            return IngestionOutcome(relative, "failed", reason_code="source_unreadable")
        if size > self._settings.max_bytes:
            return IngestionOutcome(relative, "failed", reason_code="file_too_large")
        try:
            digest = sha256_file(path)
        except OSError:
            return IngestionOutcome(relative, "failed", reason_code="source_unreadable")
        if not force and manifest.should_skip(relative, digest):
            return IngestionOutcome(relative, "skipped", output=output_name)
        try:
            markdown, extractor, page_count = self._extract(path, output_dir)
        except _IngestionError as error:
            return IngestionOutcome(relative, "failed", reason_code=error.reason_code)
        if dry_run:
            return IngestionOutcome(relative, "succeeded", output=output_name)
        try:
            write_markdown_atomically(output_dir / output_name, markdown)
            manifest.record(
                ManifestEntry(
                    source=relative,
                    sha256=digest,
                    status="succeeded",
                    output=output_name,
                    extractor=extractor,
                    page_count=page_count,
                    processed_at=datetime.now(UTC).isoformat(),
                )
            )
        except (OSError, ValueError):
            return IngestionOutcome(relative, "failed", reason_code="output_write_failed")
        return IngestionOutcome(relative, "succeeded", output=output_name)

    def _extract(self, path: Path, output_dir: Path) -> tuple[str, str, int]:
        if self._settings.ocr_enabled:
            ocr = self._ocr_extractor
            if ocr is None:
                ocr = MistralOcrExtractor(
                    api_key=self._settings.api_key,
                    model=self._settings.model,
                    timeout_seconds=self._settings.timeout_seconds,
                    image_dir=output_dir / "images",
                )
            try:
                content = path.read_bytes()
                markdown = ocr.extract(path.name, content)
            except Exception as error:
                raise _IngestionError("ocr_extraction_failed") from error
            if not markdown.strip():
                raise _IngestionError("empty_extraction")
            return markdown, "mistral_ocr", 1

        if path.suffix.lower() == ".docx":
            try:
                result = self._docx_extractor.extract(path)
            except Exception as error:
                raise _IngestionError("docx_extraction_failed") from error
            if not result.markdown.strip():
                raise _IngestionError("empty_extraction")
            return result.markdown, "docx", result.page_count
        try:
            inspection = self._pdf_inspector.inspect(path)
        except Exception as error:
            raise _IngestionError("pdf_inspection_failed") from error
        if inspection.page_count > self._settings.max_pdf_pages:
            raise _IngestionError("pdf_page_limit_exceeded")
        if inspection.pages_needing_ocr:
            raise _IngestionError("mistral_not_configured")
        return _render_pdf(inspection), "pdf_native", inspection.page_count


class _IngestionError(Exception):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code


def _validate_directories(source: Path, output: Path) -> None:
    if not source.is_dir():
        raise ValueError("source_not_found")
    if source == output or source in output.parents or output in source.parents:
        raise ValueError("source_output_nested")


def _discover(source_dir: Path) -> tuple[list[Path], list[Path]]:
    sources: list[Path] = []
    rejected: list[Path] = []
    for path in source_dir.rglob("*"):
        if path.is_symlink():
            if path.suffix.lower() in _SUPPORTED_SUFFIXES:
                rejected.append(path)
        elif path.is_file() and path.suffix.lower() in _SUPPORTED_SUFFIXES:
            sources.append(path)
    return sorted(sources, key=lambda path: path.relative_to(source_dir).as_posix()), sorted(
        rejected, key=lambda path: path.relative_to(source_dir).as_posix()
    )


def _output_name(path: Path, source_dir: Path) -> str:
    relative_stem = path.relative_to(source_dir).with_suffix("").as_posix()
    normalized = unicodedata.normalize("NFKD", relative_stem).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    return f"{slug or 'document'}.md"


def _colliding_sources(paths: Iterable[Path], source_dir: Path) -> set[Path]:
    by_name: dict[str, list[Path]] = {}
    for path in paths:
        by_name.setdefault(_output_name(path, source_dir), []).append(path)
    return {path for group in by_name.values() if len(group) > 1 for path in group}


def _render_pdf(inspection: PdfInspection) -> str:
    pages: list[str] = []
    for number in range(1, inspection.page_count + 1):
        markdown = inspection.native_markdown_by_page.get(number, "").strip()
        if not markdown:
            raise _IngestionError("empty_extraction")
        pages.append(f"<!-- Page {number} -->\n{markdown}")
    return "\n\n".join(pages)
