from __future__ import annotations

from pathlib import Path

from docx import Document

from cowork_agent.config import KnowledgeIngestionSettings
from cowork_agent.integrations.knowledge_ingestion.docx_extractor import DocxExtractor
from cowork_agent.integrations.knowledge_ingestion.models import PdfInspection, PdfKind
from cowork_agent.integrations.knowledge_ingestion.service import KnowledgeIngestionService


class StubPdfInspector:
    def __init__(self, inspection: PdfInspection) -> None:
        self.inspection = inspection
        self.paths: list[Path] = []

    def inspect(self, path: Path) -> PdfInspection:
        self.paths.append(path)
        return self.inspection


def _settings() -> KnowledgeIngestionSettings:
    return KnowledgeIngestionSettings.from_env(
        {"KNOWLEDGE_INGEST_OCR_ENABLED": "false"}, load_env_file=False
    )


def _write_docx(path: Path, text: str = "Knowledge body") -> None:
    document = Document()
    document.add_heading("Policy", level=1)
    document.add_paragraph(text)
    document.save(path)


def test_docx_is_written_as_markdown_and_can_be_skipped(tmp_path: Path) -> None:
    """Removing manifest reuse would unnecessarily re-extract an unchanged DOCX."""
    raw = tmp_path / "raw"
    raw.mkdir()
    _write_docx(raw / "policy.docx")
    service = KnowledgeIngestionService(
        _settings(), DocxExtractor(), StubPdfInspector(_native_pdf())
    )

    first, = service.ingest(raw, tmp_path / "extracted", force=False)
    second, = service.ingest(raw, tmp_path / "extracted", force=False)

    assert first.status == "succeeded"
    assert first.output == "policy.md"
    assert second.status == "skipped"
    assert "# Policy" in (tmp_path / "extracted" / "policy.md").read_text(encoding="utf-8")


def test_pdf_needing_ocr_fails_without_creating_markdown_when_ocr_is_disabled(
    tmp_path: Path,
) -> None:
    """Writing native partial text would silently publish incomplete scanned PDFs."""
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "scan.pdf").write_bytes(b"pdf")
    inspection = PdfInspection(PdfKind.MIXED, 2, (2,), {1: "native page"})
    service = KnowledgeIngestionService(_settings(), DocxExtractor(), StubPdfInspector(inspection))

    outcome, = service.ingest(raw, tmp_path / "extracted", force=False)

    assert outcome.status == "failed"
    assert outcome.reason_code == "mistral_not_configured"
    assert not (tmp_path / "extracted" / "scan.md").exists()


def test_native_pdf_uses_stable_page_markers(tmp_path: Path) -> None:
    """Dropping page markers would lose the source-page boundary required for citations."""
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "Policy File.pdf").write_bytes(b"pdf")
    inspection = PdfInspection(PdfKind.TEXT_BASED, 2, (), {1: "One", 2: "Two"})
    service = KnowledgeIngestionService(_settings(), DocxExtractor(), StubPdfInspector(inspection))

    outcome, = service.ingest(raw, tmp_path / "extracted", force=False)

    assert outcome.status == "succeeded"
    assert outcome.output == "policy-file.md"
    assert (tmp_path / "extracted" / "policy-file.md").read_text(encoding="utf-8") == (
        "<!-- Page 1 -->\nOne\n\n<!-- Page 2 -->\nTwo"
    )


def test_duplicate_slug_is_a_safe_failure(tmp_path: Path) -> None:
    """Allowing two sources to overwrite one output would corrupt the corpus."""
    raw = tmp_path / "raw"
    raw.mkdir()
    _write_docx(raw / "A B.docx")
    _write_docx(raw / "A-B.docx")
    service = KnowledgeIngestionService(
        _settings(), DocxExtractor(), StubPdfInspector(_native_pdf())
    )

    outcomes = service.ingest(raw, tmp_path / "extracted", force=False)

    assert [outcome.reason_code for outcome in outcomes] == ["output_name_collision"] * 2
    assert not list((tmp_path / "extracted").glob("*.md"))


def test_pdf_over_page_limit_is_rejected_before_markdown_is_written(tmp_path: Path) -> None:
    """Ignoring configured page limits could make one input exhaust local resources."""
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "long.pdf").write_bytes(b"pdf")
    settings = KnowledgeIngestionSettings.from_env(
        {"KNOWLEDGE_INGEST_OCR_ENABLED": "false", "KNOWLEDGE_INGEST_MAX_PDF_PAGES": "1"},
        load_env_file=False,
    )
    service = KnowledgeIngestionService(
        settings,
        DocxExtractor(),
        StubPdfInspector(PdfInspection(PdfKind.TEXT_BASED, 2, (), {1: "a", 2: "b"})),
    )

    outcome, = service.ingest(raw, tmp_path / "extracted", force=False)

    assert outcome.reason_code == "pdf_page_limit_exceeded"
    assert not (tmp_path / "extracted" / "long.md").exists()


def _native_pdf() -> PdfInspection:
    return PdfInspection(PdfKind.TEXT_BASED, 1, (), {1: "native"})


class _StubOcrExtractor:
    def __init__(self, output: str = "# OCR Result") -> None:
        self.output = output
        self.extracted_files: list[str] = []

    def extract(self, filename: str, content: bytes) -> str:
        self.extracted_files.append(filename)
        return self.output


def test_service_extracts_via_ocr_when_enabled(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "scanned_doc.pdf").write_bytes(b"scanned-pdf-bytes")
    settings = KnowledgeIngestionSettings.from_env(
        {"EXTRACTION_MODE": "advance", "MISTRAL_API_KEY": "secret"},
        load_env_file=False,
    )
    stub_ocr = _StubOcrExtractor("# Scanned Content")
    service = KnowledgeIngestionService(
        settings,
        DocxExtractor(),
        StubPdfInspector(_native_pdf()),
        ocr_extractor=stub_ocr,
    )

    outcome, = service.ingest(raw, tmp_path / "extracted", force=False)

    assert outcome.status == "succeeded"
    assert outcome.output == "scanned-doc.md"
    assert stub_ocr.extracted_files == ["scanned_doc.pdf"]
    output_md = (tmp_path / "extracted" / "scanned-doc.md").read_text(encoding="utf-8")
    assert output_md == "# Scanned Content"


def test_service_handles_ocr_failure_cleanly(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "broken.pdf").write_bytes(b"bytes")
    settings = KnowledgeIngestionSettings.from_env(
        {"EXTRACTION_MODE": "advance", "MISTRAL_API_KEY": "secret"},
        load_env_file=False,
    )

    class _FailingOcr:
        def extract(self, filename: str, content: bytes) -> str:
            raise RuntimeError("API Timeout")

    service = KnowledgeIngestionService(
        settings,
        DocxExtractor(),
        StubPdfInspector(_native_pdf()),
        ocr_extractor=_FailingOcr(),
    )

    outcome, = service.ingest(raw, tmp_path / "extracted", force=False)

    assert outcome.status == "failed"
    assert outcome.reason_code == "ocr_extraction_failed"

