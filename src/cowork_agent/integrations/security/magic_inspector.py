"""Magic bytes inspection, MIME validation, and file allowlist triage for attachments."""

from __future__ import annotations

import hashlib
import io
import logging
import struct
import zipfile
from pathlib import Path
from typing import Final

from cowork_agent.domain.target_contracts import (
    AttachmentSafetyReport,
    ThreatCategory,
    ThreatLevel,
)

logger = logging.getLogger(__name__)

# Maximum chunk of header bytes to inspect for MIME signatures
HEADER_SAMPLE_SIZE: Final[int] = 512

# Decompression Limits (Anti-Zip/Gzip Bomb)
DEFAULT_MAX_DECOMPRESSION_RATIO: Final[float] = 100.0
DEFAULT_MAX_UNCOMPRESSED_BYTES: Final[int] = 100 * 1024 * 1024  # 100 MB
DEFAULT_MAX_ARCHIVE_FILES: Final[int] = 10_000

# Prohibited dangerous executable and script extensions (strictly blocked)
PROHIBITED_EXTENSIONS: Final[frozenset[str]] = frozenset({
    ".exe",
    ".msi",
    ".dll",
    ".sys",
    ".com",
    ".scr",
    ".pif",
    ".cpl",
    ".iso",
    ".img",
    ".dmg",
    ".pkg",
    ".deb",
    ".rpm",
    ".apk",
    ".bat",
    ".cmd",
    ".ps1",
    ".psm1",
    ".vbs",
    ".vbe",
    ".js",
    ".jse",
    ".wsf",
    ".wsh",
    ".hta",
    ".jar",
    ".reg",
    ".bin",
    ".gadget",
})

# Macro-enabled Office extensions
MACRO_EXTENSIONS: Final[frozenset[str]] = frozenset({
    ".xlsm",
    ".docm",
    ".pptm",
    ".dotm",
    ".xltm",
    ".potm",
    ".xla",
    ".xlam",
})

# Allowed safe document and media extensions for content extraction
ALLOWED_DOCUMENT_EXTENSIONS: Final[frozenset[str]] = frozenset({
    ".pdf",
    ".docx",
    ".xlsx",
    ".pptx",
    ".txt",
    ".csv",
    ".md",
    ".rtf",
    ".odt",
    ".ods",
    ".odp",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".svg",
    ".zip",
    ".gz",
    ".tar",
})


def detect_mime_from_bytes(header: bytes) -> str:
    """Determine real MIME type from magic bytes in header sample."""
    if not header:
        return "application/octet-stream"

    # PDF signature: %PDF-
    if header.startswith(b"%PDF-"):
        return "application/pdf"

    # PNG signature: \x89PNG\r\n\x1a\n
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"

    # JPEG signature: \xff\xd8\xff
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"

    # GIF signature: GIF87a or GIF89a
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"

    # WEBP signature: RIFF....WEBP
    if len(header) >= 12 and header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp"

    # BMP signature: BM
    if header.startswith(b"BM"):
        return "image/bmp"

    # Windows PE Executable (EXE/DLL/SYS): MZ
    if header.startswith(b"MZ"):
        return "application/x-dosexec"

    # Linux ELF Executable: \x7fELF
    if header.startswith(b"\x7fELF"):
        return "application/x-executable"

    # Mach-O Executable / Universal Binary
    if header.startswith((
        b"\xfe\xed\xfa\xce",
        b"\xfe\xed\xfa\xcf",
        b"\xce\xfa\xed\xfe",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
    )):
        return "application/x-mach-binary"

    # ZIP archive / Office OpenXML: PK\x03\x04
    if header.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return "application/zip"

    # GZIP archive: \x1f\x8b
    if header.startswith(b"\x1f\x8b"):
        return "application/gzip"

    # 7-Zip archive: 7z\xbc\xaf\x27\x1c
    if header.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "application/x-7z-compressed"

    # RAR archive: Rar!\x1a\x07
    if header.startswith((b"Rar!\x1a\x07", b"Rar!\x1a\x07\x01\x00")):
        return "application/vnd.rar"

    # RTF document: {\rtf
    if header.startswith(b"{\\rtf"):
        return "application/rtf"

    # Shell script: #!
    if header.startswith(b"#!"):
        return "text/x-shellscript"

    # HTML / XML signatures
    stripped = header.lstrip()
    if stripped.lower().startswith((b"<!doctype html", b"<html")):
        return "text/html"
    if stripped.lower().startswith(b"<?xml"):
        return "application/xml"

    # Check for text/plain (valid ASCII or UTF-8 without control characters)
    try:
        sample_text = header[:min(256, len(header))].decode("utf-8")
        if all(c.isprintable() or c in "\r\n\t" for c in sample_text):
            return "text/plain"
    except UnicodeDecodeError:
        pass

    return "application/octet-stream"


def _check_double_extension(filename: str) -> tuple[bool, str | None]:
    """Detect double extensions used for deception (e.g. invoice.pdf.exe)."""
    parts = Path(filename).name.lower().split(".")
    if len(parts) > 2:
        final_ext = f".{parts[-1]}"
        inner_ext = f".{parts[-2]}"
        if final_ext in PROHIBITED_EXTENSIONS and inner_ext in ALLOWED_DOCUMENT_EXTENSIONS:
            return True, f"Deceptive double extension detected: {inner_ext}{final_ext}"
        if final_ext in PROHIBITED_EXTENSIONS:
            return True, f"Prohibited executable extension: {final_ext}"
    return False, None


def check_zip_bomb(
    zip_bytes: bytes,
    max_ratio: float = DEFAULT_MAX_DECOMPRESSION_RATIO,
    max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
    max_files: int = DEFAULT_MAX_ARCHIVE_FILES,
) -> tuple[bool, str | None]:
    """Inspect ZIP metadata without full decompression to detect decompression bombs."""
    compressed_size = len(zip_bytes)
    if compressed_size == 0:
        return False, None

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            infolist = zf.infolist()
            total_files = len(infolist)
            if total_files > max_files:
                return True, f"Archive contains {total_files} files, exceeding limit of {max_files}"

            total_uncompressed = sum(info.file_size for info in infolist)

            # Check decompression ratio
            ratio = total_uncompressed / max(compressed_size, 1)
            if ratio > max_ratio and total_uncompressed > 10 * 1024 * 1024:
                return (
                    True,
                    f"Zip bomb detected: decompression ratio {ratio:.1f}:1 exceeds {max_ratio}:1",
                )

            if total_uncompressed > max_uncompressed_bytes:
                return (
                    True,
                    f"Uncompressed size {total_uncompressed} bytes exceeds limit of "
                    f"{max_uncompressed_bytes} bytes",
                )

            # Check for prohibited extensions inside zip
            for info in infolist:
                ext = Path(info.filename).suffix.lower()
                if ext in PROHIBITED_EXTENSIONS:
                    return True, f"Archive contains prohibited executable file: {info.filename}"

    except (zipfile.BadZipFile, struct.error, OSError) as exc:
        return False, f"Malformed ZIP archive: {exc}"

    return False, None


def check_gzip_bomb(
    gz_bytes: bytes,
    max_ratio: float = DEFAULT_MAX_DECOMPRESSION_RATIO,
    max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
) -> tuple[bool, str | None]:
    """Inspect GZIP header and ISIZE footer to detect GZIP bombs safely."""
    compressed_size = len(gz_bytes)
    if compressed_size < 10:
        return False, None

    # Read uncompressed size (ISIZE) from last 4 bytes (modulo 2^32)
    isize = struct.unpack("<I", gz_bytes[-4:])[0]
    if isize > max_uncompressed_bytes:
        return True, f"GZIP uncompressed size {isize} bytes exceeds limit {max_uncompressed_bytes}"

    ratio = isize / max(compressed_size, 1)
    if ratio > max_ratio and isize > 10 * 1024 * 1024:
        return True, f"Gzip bomb detected: decompression ratio {ratio:.1f}:1 exceeds {max_ratio}:1"

    return False, None


def _check_vba_macros_in_zip(zip_bytes: bytes) -> bool:
    """Check if an OpenXML ZIP archive contains embedded VBA macro binary files."""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            for name in zf.namelist():
                name_lower = name.lower()
                if (
                    "vbaproject.bin" in name_lower
                    or "macros/" in name_lower
                    or "vba/" in name_lower
                ):
                    return True
    except Exception:
        pass
    return False


def inspect_attachment_bytes(
    filename: str,
    content: bytes,
    *,
    max_uncompressed_size: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
    max_ratio: float = DEFAULT_MAX_DECOMPRESSION_RATIO,
) -> AttachmentSafetyReport:
    """Perform comprehensive magic bytes, extension, and threat triage on attachment bytes."""
    sha256_hash = hashlib.sha256(content).hexdigest()
    path = Path(filename)
    extension = path.suffix.lower()
    header = content[:HEADER_SAMPLE_SIZE]
    detected_mime = detect_mime_from_bytes(header)

    # 1. Check for double extension deception
    is_double_ext, double_ext_reason = _check_double_extension(filename)
    if is_double_ext:
        return AttachmentSafetyReport(
            filename=filename,
            sha256=sha256_hash,
            detected_mime_type=detected_mime,
            threat_level=ThreatLevel.MALICIOUS,
            threat_category=ThreatCategory.MACRO_SCRIPT
            if extension in {".vbs", ".js", ".ps1"}
            else ThreatCategory.MALWARE,
            is_safe_to_extract=False,
            reason=double_ext_reason,
        )

    # 2. Check for prohibited extension directly
    if extension in PROHIBITED_EXTENSIONS:
        return AttachmentSafetyReport(
            filename=filename,
            sha256=sha256_hash,
            detected_mime_type=detected_mime,
            threat_level=ThreatLevel.MALICIOUS,
            threat_category=ThreatCategory.MACRO_SCRIPT
            if extension in {".vbs", ".js", ".ps1", ".bat", ".cmd", ".hta"}
            else ThreatCategory.MALWARE,
            is_safe_to_extract=False,
            reason=f"Prohibited executable or script file extension: {extension}",
        )

    # 3. Check for binary executable signature disguised under benign extension
    executable_mimes = (
        "application/x-dosexec",
        "application/x-executable",
        "application/x-mach-binary",
    )
    if detected_mime in executable_mimes:
        doc_type = extension or "document"
        return AttachmentSafetyReport(
            filename=filename,
            sha256=sha256_hash,
            detected_mime_type=detected_mime,
            threat_level=ThreatLevel.MALICIOUS,
            threat_category=ThreatCategory.MALWARE,
            is_safe_to_extract=False,
            reason=f"Executable binary disguised as {doc_type} (detected {detected_mime})",
        )

    # 4. Check for Macro-enabled Office documents (.xlsm, .docm, etc.) or embedded VBA
    if extension in MACRO_EXTENSIONS:
        has_vba = (
            _check_vba_macros_in_zip(content)
            or b"Auto_Open" in content
            or b"vbaProject" in content
        )
        return AttachmentSafetyReport(
            filename=filename,
            sha256=sha256_hash,
            detected_mime_type=detected_mime,
            threat_level=ThreatLevel.MALICIOUS if has_vba else ThreatLevel.SUSPICIOUS,
            threat_category=ThreatCategory.MACRO_SCRIPT,
            is_safe_to_extract=False,
            reason=(
                "Macro-enabled spreadsheet/document containing active VBA macro scripts"
                if has_vba
                else f"Macro-enabled Office extension {extension}"
            ),
        )

    # 5. Check ZIP / GZIP bomb attacks
    if detected_mime == "application/zip":
        if _check_vba_macros_in_zip(content):
            return AttachmentSafetyReport(
                filename=filename,
                sha256=sha256_hash,
                detected_mime_type=detected_mime,
                threat_level=ThreatLevel.MALICIOUS,
                threat_category=ThreatCategory.MACRO_SCRIPT,
                is_safe_to_extract=False,
                reason="Embedded VBA macros found in archive/document",
            )

        is_bomb, bomb_reason = check_zip_bomb(
            content,
            max_ratio=max_ratio,
            max_uncompressed_bytes=max_uncompressed_size,
        )
        if is_bomb:
            return AttachmentSafetyReport(
                filename=filename,
                sha256=sha256_hash,
                detected_mime_type=detected_mime,
                threat_level=ThreatLevel.BLOCKED,
                threat_category=ThreatCategory.ZIP_BOMB,
                is_safe_to_extract=False,
                reason=bomb_reason,
            )

    elif detected_mime == "application/gzip":
        is_bomb, bomb_reason = check_gzip_bomb(
            content,
            max_ratio=max_ratio,
            max_uncompressed_bytes=max_uncompressed_size,
        )
        if is_bomb:
            return AttachmentSafetyReport(
                filename=filename,
                sha256=sha256_hash,
                detected_mime_type=detected_mime,
                threat_level=ThreatLevel.BLOCKED,
                threat_category=ThreatCategory.ZIP_BOMB,
                is_safe_to_extract=False,
                reason=bomb_reason,
            )

    # 6. Check for extension and MIME mismatch (e.g. .pdf but is text/html)
    if (
        extension == ".pdf"
        and detected_mime not in ("application/pdf", "application/octet-stream")
    ):
        return AttachmentSafetyReport(
            filename=filename,
            sha256=sha256_hash,
            detected_mime_type=detected_mime,
            threat_level=ThreatLevel.SUSPICIOUS,
            threat_category=ThreatCategory.PARSER_EXPLOIT,
            is_safe_to_extract=False,
            reason=f"MIME type mismatch: extension .pdf but file signature is {detected_mime}",
        )

    # 7. Check if extension is in allowlist for extraction
    is_safe = extension in ALLOWED_DOCUMENT_EXTENSIONS or extension == ""

    return AttachmentSafetyReport(
        filename=filename,
        sha256=sha256_hash,
        detected_mime_type=detected_mime,
        threat_level=ThreatLevel.CLEAN,
        threat_category=ThreatCategory.NONE,
        is_safe_to_extract=is_safe,
        reason=None if is_safe else f"Extension {extension} not in allowed extraction list",
    )


def inspect_attachment_file(
    file_path: Path | str,
    original_filename: str | None = None,
) -> AttachmentSafetyReport:
    """Read file from path and perform magic bytes inspection."""
    path = Path(file_path)
    if not path.exists():
        return AttachmentSafetyReport(
            filename=original_filename or path.name,
            sha256="",
            detected_mime_type="application/octet-stream",
            threat_level=ThreatLevel.BLOCKED,
            threat_category=ThreatCategory.NONE,
            is_safe_to_extract=False,
            reason=f"File not found on disk: {path}",
        )

    content = path.read_bytes()
    name = original_filename or path.name
    return inspect_attachment_bytes(name, content)
