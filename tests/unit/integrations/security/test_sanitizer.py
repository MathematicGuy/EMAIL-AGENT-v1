"""Unit tests for Active Content Stripper and Document Sanitizer."""

from __future__ import annotations

import io
import zipfile

from cowork_agent.integrations.security.sanitizer import (
    inspect_pdf_active_content,
    sanitize_document_bytes,
    sanitize_openxml_bytes,
)


def _create_synthetic_macro_excel() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        macro_ct = (
            b'<Types><Override ContentType="application/vnd.ms-excel.'
            b'sheet.macroEnabled.main+xml"/></Types>'
        )
        zf.writestr("[Content_Types].xml", macro_ct)
        zf.writestr(
            "xl/workbook.xml",
            b'<workbook><sheets><sheet name="Sheet1"/></sheets></workbook>',
        )
        zf.writestr("xl/vbaProject.bin", b"MOCK_VBA_COMPILED_MACRO_BINARY_DATA")
        zf.writestr("xl/vbaData.xml", b"<vbaData>Auto_Open</vbaData>")
        zf.writestr("xl/embeddings/oleObject1.bin", b"MOCK_EMBEDDED_EXECUTABLE_OLE")
    return buf.getvalue()


def _create_clean_openxml_doc() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        clean_ct = (
            b'<Types><Override ContentType="application/vnd.openxmlformats-'
            b'officedocument.wordprocessingml.document.main+xml"/></Types>'
        )
        zf.writestr("[Content_Types].xml", clean_ct)
        zf.writestr(
            "word/document.xml",
            b'<w:document><w:body><w:p><w:r><w:t>Hello world'
            b'</w:t></w:r></w:p></w:body></w:document>',
        )
    return buf.getvalue()


def test_sanitize_openxml_macro_removal():
    macro_bytes = _create_synthetic_macro_excel()
    result = sanitize_openxml_bytes(macro_bytes, "bonus_q3.xlsm")

    assert result.active_content_detected is True
    assert len(result.stripped_items) == 3
    assert "xl/vbaProject.bin" in result.stripped_items
    assert "xl/vbaData.xml" in result.stripped_items
    assert "xl/embeddings/oleObject1.bin" in result.stripped_items
    assert result.is_safe is True

    # Verify sanitized bytes no longer contain vbaProject.bin
    sanitized_zip = zipfile.ZipFile(io.BytesIO(result.sanitized_bytes), "r")
    entry_names = sanitized_zip.namelist()
    assert "xl/vbaProject.bin" not in entry_names
    assert "xl/vbaData.xml" not in entry_names
    assert "xl/embeddings/oleObject1.bin" not in entry_names
    assert "xl/workbook.xml" in entry_names

    # Verify content types cleaned
    content_types = sanitized_zip.read("[Content_Types].xml")
    assert b"macroEnabled" not in content_types
    assert b"spreadsheetml.sheet.main+xml" in content_types


def test_sanitize_clean_openxml_document():
    clean_bytes = _create_clean_openxml_doc()
    result = sanitize_openxml_bytes(clean_bytes, "report.docx")

    assert result.active_content_detected is False
    assert len(result.stripped_items) == 0
    assert result.is_safe is True
    assert result.sanitized_bytes == clean_bytes


def test_inspect_pdf_with_javascript_and_launch():
    # PDF containing embedded JS and Launch action
    pdf_with_js = (
        b"%PDF-1.7\n"
        b"1 0 obj\n<< /Type /Catalog /OpenAction 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /S /JavaScript /JS (app.alert('Exploit');) >>\nendobj\n"
        b"3 0 obj\n<< /S /Launch /F (calc.exe) >>\nendobj\n"
        b"%%EOF"
    )
    result = inspect_pdf_active_content(pdf_with_js, "invoice.pdf")

    assert result.active_content_detected is True
    assert result.is_safe is False
    assert any("JavaScript" in item for item in result.stripped_items)
    assert any("Launch" in item for item in result.stripped_items)
    assert any("Auto-execution" in item for item in result.stripped_items)


def test_inspect_clean_pdf():
    clean_pdf = (
        b"%PDF-1.7\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R >>\nendobj\n"
        b"%%EOF"
    )
    result = inspect_pdf_active_content(clean_pdf, "clean.pdf")

    assert result.active_content_detected is False
    assert result.is_safe is True
    assert len(result.stripped_items) == 0


def test_sanitize_document_bytes_routing():
    macro_bytes = _create_synthetic_macro_excel()
    res_excel = sanitize_document_bytes(macro_bytes, "finance.xlsm")
    assert res_excel.active_content_detected is True

    txt_bytes = b"Plain text content"
    res_txt = sanitize_document_bytes(txt_bytes, "readme.txt")
    assert res_txt.active_content_detected is False
    assert res_txt.is_safe is True
