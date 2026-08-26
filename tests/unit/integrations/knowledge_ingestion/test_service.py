from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

from docx import Document

from cowork_agent.config import KnowledgeIngestionSettings
from cowork_agent.integrations.knowledge_ingestion.docx_extractor import DocxExtractor
from cowork_agent.integrations.knowledge_ingestion.manifest import ManifestStore
from cowork_agent.integrations.knowledge_ingestion.models import PdfInspection, PdfKind
from cowork_agent.integrations.knowledge_ingestion.service import KnowledgeIngestionService
from cowork_agent.integrations.knowledge_ingestion.text_sanitizer import (
    resolve_title,
    split_frontmatter,
)

_NFD_VIETNAMESE_E = "e\u0302\u0301"
_MANIFEST_NAME = "ingestion-manifest.json"
_DCTERMS_CREATED = "{http://purl.org/dc/terms/}created"
_DCTERMS_MODIFIED = "{http://purl.org/dc/terms/}modified"


class StubPdfInspector:
    def __init__(self, inspection: PdfInspection) -> None:
        self.inspection = inspection
        self.paths: list[Path] = []

    def inspect(self, path: Path) -> PdfInspection:
        self.paths.append(path)
        return self.inspection


def _settings() -> KnowledgeIngestionSettings:
    return KnowledgeIngestionSettings.from_env(
        {"KNOWLEDGE_INGEST_OCR_ENABLED": "false"}
    )


def _write_docx(path: Path, text: str = "Knowledge body") -> None:
    document = Document()
    document.add_heading("Policy", level=1)
    document.add_paragraph(text)
    document.save(path)


def _drop_docx_core_dates(path: Path) -> None:
    with ZipFile(path) as archive:
        contents = {info.filename: archive.read(info.filename) for info in archive.infolist()}
    core = ET.fromstring(contents["docProps/core.xml"])
    drop_tags = {_DCTERMS_CREATED, _DCTERMS_MODIFIED}
    for child in list(core):
        if child.tag in drop_tags:
            core.remove(child)
    contents["docProps/core.xml"] = ET.tostring(core, encoding="utf-8", xml_declaration=True)
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, data in contents.items():
            archive.writestr(name, data)


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

    markdown = (tmp_path / "extracted" / "policy.md").read_text(encoding="utf-8")
    fields, body = split_frontmatter(markdown)

    assert first.status == "succeeded"
    assert first.output == "policy.md"
    assert second.status == "skipped"
    assert "# Policy" in body
    assert fields["title"] == "Policy"


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

    output_md = (tmp_path / "extracted" / "policy-file.md").read_text(encoding="utf-8")
    _, body = split_frontmatter(output_md)

    assert outcome.status == "succeeded"
    assert outcome.output == "policy-file.md"
    assert "<!-- Page 1 -->" in body
    assert "<!-- Page 2 -->" in body
    assert "One" in body
    assert "Two" in body


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
    _, body = split_frontmatter(output_md)
    assert output_md.startswith("---\n")
    assert "# Scanned Content" in body


def test_service_handles_ocr_failure_cleanly(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "broken.pdf").write_bytes(b"bytes")
    settings = KnowledgeIngestionSettings.from_env(
        {"EXTRACTION_MODE": "advance", "MISTRAL_API_KEY": "secret"},
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


def test_service_adaptive_mode_escalates_scanned_pdf_to_ocr(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "mixed.pdf").write_bytes(b"pdf-bytes")
    inspection = PdfInspection(PdfKind.MIXED, 2, (2,), {1: "page 1"})
    settings = KnowledgeIngestionSettings.from_env(
        {"EXTRACTION_MODE": "adaptive", "MISTRAL_API_KEY": "secret"},
    )
    stub_ocr = _StubOcrExtractor("# Escalated OCR Content")
    service = KnowledgeIngestionService(
        settings,
        DocxExtractor(),
        StubPdfInspector(inspection),
        ocr_extractor=stub_ocr,
    )

    outcome, = service.ingest(raw, tmp_path / "extracted", force=False)

    assert outcome.status == "succeeded"
    assert outcome.output == "mixed.md"
    assert stub_ocr.extracted_files == ["mixed.pdf"]
    output_md = (tmp_path / "extracted" / "mixed.md").read_text(encoding="utf-8")
    _, body = split_frontmatter(output_md)
    assert output_md.startswith("---\n")
    assert "# Escalated OCR Content" in body


def test_service_adaptive_mode_uses_native_for_clean_pdf(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "clean.pdf").write_bytes(b"pdf-bytes")
    inspection = PdfInspection(PdfKind.TEXT_BASED, 1, (), {1: "clean digital text"})
    settings = KnowledgeIngestionSettings.from_env(
        {"EXTRACTION_MODE": "adaptive", "MISTRAL_API_KEY": "secret"},
    )
    stub_ocr = _StubOcrExtractor("# Should Not Be Called")
    service = KnowledgeIngestionService(
        settings,
        DocxExtractor(),
        StubPdfInspector(inspection),
        ocr_extractor=stub_ocr,
    )

    outcome, = service.ingest(raw, tmp_path / "extracted", force=False)

    assert outcome.status == "succeeded"
    assert outcome.output == "clean.md"
    assert stub_ocr.extracted_files == []
    output_md = (tmp_path / "extracted" / "clean.md").read_text(encoding="utf-8")
    assert "clean digital text" in output_md


def test_docx_nfd_vietnamese_paragraph_is_written_nfc(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    document = Document()
    document.add_paragraph(_NFD_VIETNAMESE_E)
    document.save(raw / "viet.docx")
    service = KnowledgeIngestionService(
        _settings(), DocxExtractor(), StubPdfInspector(_native_pdf())
    )

    outcome, = service.ingest(raw, tmp_path / "extracted", force=False)

    assert outcome.status == "succeeded"
    output_md = (tmp_path / "extracted" / "viet.md").read_text(encoding="utf-8")
    _, body = split_frontmatter(output_md)
    assert "\u1ebf" in body
    assert _NFD_VIETNAMESE_E not in body


def test_ingested_markdown_starts_with_closed_frontmatter(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    _write_docx(raw / "policy.docx")
    service = KnowledgeIngestionService(
        _settings(), DocxExtractor(), StubPdfInspector(_native_pdf())
    )

    outcome, = service.ingest(raw, tmp_path / "extracted", force=False)

    output_md = (tmp_path / "extracted" / "policy.md").read_text(encoding="utf-8")
    fields, _body = split_frontmatter(output_md)
    assert outcome.status == "succeeded"
    assert output_md.startswith("---\n")
    assert "document_id:" in output_md
    assert "extractor:" in output_md
    assert fields["document_id"] == "policy"
    assert fields["extractor"] == "docx"


def test_manifest_stored_title_matches_resolve_title(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    _write_docx(raw / "policy.docx")
    extracted = tmp_path / "extracted"
    service = KnowledgeIngestionService(
        _settings(), DocxExtractor(), StubPdfInspector(_native_pdf())
    )

    outcome, = service.ingest(raw, extracted, force=False)

    output_md = (extracted / "policy.md").read_text(encoding="utf-8")
    fields, body = split_frontmatter(output_md)
    entry = ManifestStore(extracted / _MANIFEST_NAME).load()["policy.docx"]
    expected_title = resolve_title(body, Path(outcome.output or "").stem)
    assert entry.title == expected_title
    assert fields["title"] == expected_title
    assert fields["processed_at"] == entry.processed_at


def test_unchanged_source_is_skipped_on_second_ingest(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "policy-file.pdf").write_bytes(b"pdf")
    inspection = PdfInspection(PdfKind.TEXT_BASED, 1, (), {1: "One"})
    service = KnowledgeIngestionService(_settings(), DocxExtractor(), StubPdfInspector(inspection))

    first, = service.ingest(raw, tmp_path / "extracted", force=False)
    second, = service.ingest(raw, tmp_path / "extracted", force=False)

    assert first.status == "succeeded"
    assert second.status == "skipped"


def test_txt_ingest_writes_text_extractor_and_sanitized_body(tmp_path: Path) -> None:
    """Skipping sanitize would persist NFD and control characters in the corpus."""
    raw = tmp_path / "raw"
    raw.mkdir()
    payload = f"# Note\n\nHello {_NFD_VIETNAMESE_E}\x00world\n"
    (raw / "note.txt").write_text(payload, encoding="utf-8")
    service = KnowledgeIngestionService(
        _settings(), DocxExtractor(), StubPdfInspector(_native_pdf())
    )

    outcome, = service.ingest(raw, tmp_path / "extracted", force=False)

    output_md = (tmp_path / "extracted" / "note.md").read_text(encoding="utf-8")
    fields, body = split_frontmatter(output_md)
    assert outcome.status == "succeeded"
    assert outcome.output == "note.md"
    assert fields["extractor"] == "text"
    assert "\u1ebf" in body
    assert _NFD_VIETNAMESE_E not in body
    assert "\x00" not in body
    assert "Hello" in body
    assert "world" in body


def test_md_ingest_writes_markdown_extractor_without_nesting_source_frontmatter(
    tmp_path: Path,
) -> None:
    """Keeping source YAML would nest a second --- block under company frontmatter."""
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "policy.md").write_text(
        "---\n"
        "document_id: old-id\n"
        "title: Old Title\n"
        "---\n"
        "\n"
        "# Policy\n"
        "Body text\n",
        encoding="utf-8",
    )
    service = KnowledgeIngestionService(
        _settings(), DocxExtractor(), StubPdfInspector(_native_pdf())
    )

    outcome, = service.ingest(raw, tmp_path / "extracted", force=False)

    output_md = (tmp_path / "extracted" / "policy.md").read_text(encoding="utf-8")
    fields, body = split_frontmatter(output_md)
    assert outcome.status == "succeeded"
    assert fields["extractor"] == "markdown"
    assert not body.lstrip().startswith("---")
    assert "document_id: old-id" not in body
    assert "# Policy" in body
    assert "Body text" in body


def test_advance_mode_does_not_ocr_txt(tmp_path: Path) -> None:
    """Advance mode must not bill OCR for a suffix that is already plain text."""
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "note.txt").write_text("plain text body\n", encoding="utf-8")
    settings = KnowledgeIngestionSettings.from_env(
        {"EXTRACTION_MODE": "advance", "MISTRAL_API_KEY": "secret"},
    )
    stub_ocr = _StubOcrExtractor("# Should Not Be Called")
    service = KnowledgeIngestionService(
        settings,
        DocxExtractor(),
        StubPdfInspector(_native_pdf()),
        ocr_extractor=stub_ocr,
    )

    outcome, = service.ingest(raw, tmp_path / "extracted", force=False)

    assert outcome.status == "succeeded"
    assert stub_ocr.extracted_files == []
    output_md = (tmp_path / "extracted" / "note.md").read_text(encoding="utf-8")
    fields, body = split_frontmatter(output_md)
    assert fields["extractor"] == "text"
    assert "plain text body" in body
    assert "# Should Not Be Called" not in body


def test_invalid_utf8_txt_is_decode_failed_without_output(tmp_path: Path) -> None:
    """Writing a replacement-character Markdown file would hide a corrupt source."""
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "bad.txt").write_bytes(b"\xff\xfe not utf-8")
    service = KnowledgeIngestionService(
        _settings(), DocxExtractor(), StubPdfInspector(_native_pdf())
    )

    outcome, = service.ingest(raw, tmp_path / "extracted", force=False)

    assert outcome.status == "failed"
    assert outcome.reason_code == "decode_failed"
    assert not (tmp_path / "extracted" / "bad.md").exists()


def test_service_records_harvested_docx_created_date(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    document = Document()
    document.add_heading("Policy", level=1)
    document.add_paragraph("Knowledge body")
    document.core_properties.created = datetime(2026, 8, 7, 10, 19, 25)
    document.save(raw / "dated.docx")
    extracted = tmp_path / "extracted"
    service = KnowledgeIngestionService(
        _settings(), DocxExtractor(), StubPdfInspector(_native_pdf())
    )

    outcome, = service.ingest(raw, extracted, force=False)

    assert outcome.status == "succeeded"
    manifest = json.loads((extracted / _MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["dated.docx"]["document_date"] == "2026-08-07"
    fields, _body = split_frontmatter((extracted / "dated.md").read_text(encoding="utf-8"))
    assert "document_date" not in fields


def test_service_records_empty_document_date_when_harvest_is_none(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    path = raw / "undated.docx"
    _write_docx(path)
    _drop_docx_core_dates(path)
    extracted = tmp_path / "extracted"
    service = KnowledgeIngestionService(
        _settings(), DocxExtractor(), StubPdfInspector(_native_pdf())
    )

    outcome, = service.ingest(raw, extracted, force=False)

    assert outcome.status == "succeeded"
    manifest = json.loads((extracted / _MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["undated.docx"]["document_date"] == ""


def test_unchanged_txt_is_skipped_on_second_ingest(tmp_path: Path) -> None:
    """Re-extracting an unchanged .txt would rewrite a stable corpus hash."""
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "note.txt").write_text("same body\n", encoding="utf-8")
    service = KnowledgeIngestionService(
        _settings(), DocxExtractor(), StubPdfInspector(_native_pdf())
    )

    first, = service.ingest(raw, tmp_path / "extracted", force=False)
    second, = service.ingest(raw, tmp_path / "extracted", force=False)

    assert first.status == "succeeded"
    assert second.status == "skipped"


