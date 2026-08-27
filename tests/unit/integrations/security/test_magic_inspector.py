"""Unit tests for Magic Bytes Inspector, MIME validation, and attachment allowlist triage."""

from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path

from cowork_agent.domain.target_contracts import ThreatCategory, ThreatLevel
from cowork_agent.integrations.security.magic_inspector import (
    check_gzip_bomb,
    check_zip_bomb,
    detect_mime_from_bytes,
    inspect_attachment_bytes,
    inspect_attachment_file,
)


def test_detect_mime_from_bytes_standard_signatures():
    # PDF
    assert detect_mime_from_bytes(b"%PDF-1.7 standard pdf header") == "application/pdf"
    # PNG
    assert detect_mime_from_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00") == "image/png"
    # JPEG
    assert detect_mime_from_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF") == "image/jpeg"
    # GIF
    assert detect_mime_from_bytes(b"GIF89a\x01\x00\x01\x00") == "image/gif"
    assert detect_mime_from_bytes(b"GIF87a\x01\x00\x01\x00") == "image/gif"
    # WEBP
    assert detect_mime_from_bytes(b"RIFF\x10\x00\x00\x00WEBPVP8 ") == "image/webp"
    # BMP
    assert detect_mime_from_bytes(b"BM\x36\x00\x00\x00") == "image/bmp"
    # ZIP
    assert detect_mime_from_bytes(b"PK\x03\x04\x14\x00\x00\x00") == "application/zip"
    # GZIP
    assert detect_mime_from_bytes(b"\x1f\x8b\x08\x00\x00\x00") == "application/gzip"
    # 7z
    assert detect_mime_from_bytes(b"7z\xbc\xaf\x27\x1c\x00\x04") == "application/x-7z-compressed"
    # RAR
    assert detect_mime_from_bytes(b"Rar!\x1a\x07\x00") == "application/vnd.rar"
    # Windows PE (EXE/DLL)
    assert detect_mime_from_bytes(b"MZ\x90\x00\x03\x00\x00\x00") == "application/x-dosexec"
    # Linux ELF
    assert detect_mime_from_bytes(b"\x7fELF\x02\x01\x01\x00") == "application/x-executable"
    # Mach-O
    assert detect_mime_from_bytes(b"\xfe\xed\xfa\xce\x00\x00\x00") == "application/x-mach-binary"
    # Shell script
    assert detect_mime_from_bytes(b"#!/bin/bash\necho test") == "text/x-shellscript"
    # Plain text
    assert detect_mime_from_bytes(b"Hello world, this is a plain text file.\n") == "text/plain"
    # Empty
    assert detect_mime_from_bytes(b"") == "application/octet-stream"


def test_prohibited_executable_extensions_are_malicious():
    prohibited_samples = [
        ("malware.exe", b"MZ\x90\x00some binary content"),
        ("installer.msi", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
        ("library.dll", b"MZ\x90\x00dll content"),
        ("script.bat", b"@echo off\nformat C:"),
        ("script.cmd", b"@echo off\ndel *.*"),
        ("script.ps1", b"Invoke-Expression -Command evil"),
        ("script.vbs", b"WScript.Echo \x22evil\x22"),
        ("script.js", b"eval(malicious_code);"),
        ("app.hta", b"<script>evil()</script>"),
        ("disk.iso", b"CD001"),
        ("payload.scr", b"MZ\x90\x00screensaver payload"),
    ]

    for filename, content in prohibited_samples:
        report = inspect_attachment_bytes(filename, content)
        assert report.threat_level == ThreatLevel.MALICIOUS
        assert report.is_safe_to_extract is False
        assert report.threat_category in (ThreatCategory.MALWARE, ThreatCategory.MACRO_SCRIPT)
        assert report.reason is not None


def test_deceptive_double_extensions():
    report_exe = inspect_attachment_bytes("invoice.pdf.exe", b"MZ\x90\x00fake pdf")
    assert report_exe.threat_level == ThreatLevel.MALICIOUS
    assert "Deceptive double extension" in (report_exe.reason or "")

    report_vbs = inspect_attachment_bytes("statement.docx.vbs", b"WScript.Echo 1")
    assert report_vbs.threat_level == ThreatLevel.MALICIOUS
    assert report_vbs.threat_category == ThreatCategory.MACRO_SCRIPT

    report_js = inspect_attachment_bytes("image.jpg.js", b"console.log(1)")
    assert report_js.threat_level == ThreatLevel.MALICIOUS


def test_masquerading_executable_under_benign_extension():
    # File named .pdf but magic bytes are Windows PE MZ
    content = b"MZ\x90\x00\x03\x00\x00\x00PE binary pretending to be PDF"
    report = inspect_attachment_bytes("important_document.pdf", content)

    assert report.threat_level == ThreatLevel.MALICIOUS
    assert report.threat_category == ThreatCategory.MALWARE
    assert report.detected_mime_type == "application/x-dosexec"
    assert report.is_safe_to_extract is False
    assert "Executable binary disguised as .pdf" in (report.reason or "")

    # File named .docx but magic bytes are Linux ELF
    elf_content = b"\x7fELF\x02\x01\x01\x00Linux binary pretending to be DOCX"
    report_elf = inspect_attachment_bytes("contract.docx", elf_content)
    assert report_elf.threat_level == ThreatLevel.MALICIOUS
    assert report_elf.threat_category == ThreatCategory.MALWARE


def test_macro_enabled_office_documents():
    # 1. xlsm with VBA macro markers
    content = b"PK\x03\x04... Auto_Open() Sub vbaProject.bin ..."
    report = inspect_attachment_bytes("bonus_2026.xlsm", content)
    assert report.threat_level == ThreatLevel.MALICIOUS
    assert report.threat_category == ThreatCategory.MACRO_SCRIPT
    assert report.is_safe_to_extract is False

    # 2. zip file containing embedded vbaProject.bin
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("word/vbaProject.bin", b"VBA macro bytecode")
        zf.writestr("word/document.xml", b"<xml>safe text</xml>")
    zip_bytes = zip_buffer.getvalue()

    report_macro_zip = inspect_attachment_bytes("report.docm", zip_bytes)
    assert report_macro_zip.threat_level == ThreatLevel.MALICIOUS
    assert report_macro_zip.threat_category == ThreatCategory.MACRO_SCRIPT


def test_zip_bomb_detection():
    # 1. High compression ratio (100MB uncompressed compressed to a few bytes)
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # 15MB of repetitive zeroes compresses down to ~15KB (ratio > 1000:1)
        zf.writestr("huge_zeroes.bin", b"0" * (15 * 1024 * 1024))
    bomb_bytes = zip_buffer.getvalue()

    is_bomb, reason = check_zip_bomb(bomb_bytes, max_ratio=100.0)
    assert is_bomb is True
    assert "Zip bomb detected" in (reason or "")

    report = inspect_attachment_bytes("bomb.zip", bomb_bytes)
    assert report.threat_level == ThreatLevel.BLOCKED
    assert report.threat_category == ThreatCategory.ZIP_BOMB
    assert report.is_safe_to_extract is False


def test_zip_containing_prohibited_executable():
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("payload.exe", b"MZ\x90\x00evil binary")
        zf.writestr("readme.txt", b"Open payload.exe")
    zip_bytes = zip_buffer.getvalue()

    is_bomb, reason = check_zip_bomb(zip_bytes)
    assert is_bomb is True
    assert "prohibited executable file" in (reason or "")


def test_gzip_bomb_detection():
    # Synthetic GZIP header with gigantic ISIZE in the last 4 bytes (e.g. 500MB)
    fake_gz = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03" + b"\x00" * 20
    isize_500mb = struct.pack("<I", 500 * 1024 * 1024)
    huge_gz = fake_gz + isize_500mb

    is_bomb, reason = check_gzip_bomb(huge_gz, max_uncompressed_bytes=100 * 1024 * 1024)
    assert is_bomb is True
    assert "GZIP uncompressed size" in (reason or "")


def test_safe_allowed_documents_pass_clean():
    # Valid PDF
    pdf_content = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF"
    report_pdf = inspect_attachment_bytes("clean_report.pdf", pdf_content)
    assert report_pdf.threat_level == ThreatLevel.CLEAN
    assert report_pdf.threat_category == ThreatCategory.NONE
    assert report_pdf.is_safe_to_extract is True
    assert report_pdf.detected_mime_type == "application/pdf"

    # Valid PNG
    png_content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    report_png = inspect_attachment_bytes("chart.png", png_content)
    assert report_png.threat_level == ThreatLevel.CLEAN
    assert report_png.is_safe_to_extract is True
    assert report_png.detected_mime_type == "image/png"

    # Valid Plain Text
    txt_content = b"Meeting notes for Sprint 5 review.\nAll tests passing."
    report_txt = inspect_attachment_bytes("notes.txt", txt_content)
    assert report_txt.threat_level == ThreatLevel.CLEAN
    assert report_txt.is_safe_to_extract is True
    assert report_txt.detected_mime_type == "text/plain"


def test_inspect_attachment_file(tmp_path: Path):
    safe_file = tmp_path / "valid.pdf"
    safe_file.write_bytes(b"%PDF-1.5 test file content")
    report = inspect_attachment_file(safe_file)
    assert report.threat_level == ThreatLevel.CLEAN
    assert report.filename == "valid.pdf"

    non_existent = tmp_path / "missing.pdf"
    report_missing = inspect_attachment_file(non_existent)
    assert report_missing.threat_level == ThreatLevel.BLOCKED
    assert "File not found" in (report_missing.reason or "")
