from __future__ import annotations

import zlib
from datetime import date, datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from docx import Document

from cowork_agent.integrations.knowledge_ingestion.date_harvest import harvest_document_date

_DCTERMS_CREATED = "{http://purl.org/dc/terms/}created"
_DCTERMS_MODIFIED = "{http://purl.org/dc/terms/}modified"


def _write_docx(
    path: Path,
    *,
    created: datetime | None = None,
    modified: datetime | None = None,
    drop: tuple[str, ...] = (),
) -> Path:
    document = Document()
    if created is not None:
        document.core_properties.created = created
    if modified is not None:
        document.core_properties.modified = modified
    document.save(path)
    if drop:
        _drop_docx_core_dates(path, drop)
    return path


def _drop_docx_core_dates(path: Path, names: tuple[str, ...]) -> None:
    from xml.etree import ElementTree as ET

    drop_tags = {
        "created": _DCTERMS_CREATED,
        "modified": _DCTERMS_MODIFIED,
    }
    with ZipFile(path) as archive:
        contents = {info.filename: archive.read(info.filename) for info in archive.infolist()}
    core = ET.fromstring(contents["docProps/core.xml"])
    for child in list(core):
        if child.tag in {drop_tags[name] for name in names}:
            core.remove(child)
    contents["docProps/core.xml"] = ET.tostring(core, encoding="utf-8", xml_declaration=True)
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, data in contents.items():
            archive.writestr(name, data)


def _write_pdf(path: Path, body: bytes) -> Path:
    path.write_bytes(body)
    return path


def _pdf_with_info(info_object: bytes) -> bytes:
    return (
        b"%PDF-1.3\n"
        b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n"
        b"2 0 obj<< /Type /Pages /Kids [] /Count 0 >>endobj\n"
        + info_object
        + b"trailer<< /Root 1 0 R /Info 3 0 R >>\n"
        b"%%EOF\n"
    )


def test_docx_with_created_datetime_returns_that_calendar_date(tmp_path: Path) -> None:
    path = _write_docx(
        tmp_path / "policy.docx",
        created=datetime(2026, 8, 7, 10, 19, 25),
        modified=datetime(2026, 1, 1, 12, 0, 0),
    )

    assert harvest_document_date(path) == date(2026, 8, 7)


def test_docx_with_only_modified_returns_that_date(tmp_path: Path) -> None:
    path = _write_docx(
        tmp_path / "policy.docx",
        modified=datetime(2025, 3, 15, 8, 30, 0),
        drop=("created",),
    )

    assert harvest_document_date(path) == date(2025, 3, 15)


def test_docx_with_empty_core_properties_returns_none(tmp_path: Path) -> None:
    path = _write_docx(tmp_path / "policy.docx", drop=("created", "modified"))

    assert harvest_document_date(path) is None


def test_pdf_creation_date_d_20260807101925z_returns_date(tmp_path: Path) -> None:
    path = _write_pdf(
        tmp_path / "export.pdf",
        _pdf_with_info(
            b"3 0 obj<< /CreationDate (D:20260807101925Z) /ModDate (D:20260101120000Z) >>endobj\n"
        ),
    )

    assert harvest_document_date(path) == date(2026, 8, 7)


def test_pdf_with_only_moddate_returns_that_date(tmp_path: Path) -> None:
    path = _write_pdf(
        tmp_path / "export.pdf",
        _pdf_with_info(b"3 0 obj<< /ModDate (D:20260315120000) >>endobj\n"),
    )

    assert harvest_document_date(path) == date(2026, 3, 15)


def test_pdf_with_no_date_in_info_returns_none(tmp_path: Path) -> None:
    path = _write_pdf(
        tmp_path / "export.pdf",
        _pdf_with_info(b"3 0 obj<< /Title (Untitled) >>endobj\n"),
    )

    assert harvest_document_date(path) is None


def test_txt_path_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("2026-08-07\n", encoding="utf-8")

    assert harvest_document_date(path) is None


def test_missing_path_returns_none(tmp_path: Path) -> None:
    assert harvest_document_date(tmp_path / "missing.pdf") is None


def test_pdf_creation_date_via_indirect_ref_returns_date(tmp_path: Path) -> None:
    path = _write_pdf(
        tmp_path / "export.pdf",
        (
            b"%PDF-1.3\n"
            b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n"
            b"2 0 obj<< /Type /Pages /Kids [] /Count 0 >>endobj\n"
            b"3 0 obj<< /CreationDate 4 0 R /ModDate (D:20260101120000Z) >>endobj\n"
            b"4 0 obj (D:20260807101925Z) endobj\n"
            b"trailer<< /Root 1 0 R /Info 3 0 R >>\n"
            b"%%EOF\n"
        ),
    )

    assert harvest_document_date(path) == date(2026, 8, 7)


def test_pdf_flate_decode_info_object_returns_creation_date(tmp_path: Path) -> None:
    payload = b"<< /CreationDate (D:20260807101925Z) /ModDate (D:20260101120000Z) >>"
    compressed = zlib.compress(payload)
    path = _write_pdf(
        tmp_path / "export.pdf",
        (
            b"%PDF-1.3\n"
            b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n"
            b"2 0 obj<< /Type /Pages /Kids [] /Count 0 >>endobj\n"
            b"3 0 obj<< /Filter /FlateDecode /Length "
            + str(len(compressed)).encode("ascii")
            + b" >>stream\n"
            + compressed
            + b"\nendstream\nendobj\n"
            b"trailer<< /Root 1 0 R /Info 3 0 R >>\n"
            b"%%EOF\n"
        ),
    )

    assert harvest_document_date(path) == date(2026, 8, 7)
