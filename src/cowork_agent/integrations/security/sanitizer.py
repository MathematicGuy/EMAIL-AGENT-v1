"""Active Content Stripper and Sanitizer for Office OpenXML and PDF Documents."""

from __future__ import annotations

import io
import logging
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

# Dangerous OpenXML parts to remove (Macros, VBA streams, embedded OLE objects)
_PROHIBITED_OPENXML_ENTRIES: Final[frozenset[str]] = frozenset({
    "word/vbaProject.bin",
    "word/vbaData.xml",
    "xl/vbaProject.bin",
    "xl/vbaData.xml",
    "ppt/vbaProject.bin",
    "ppt/vbaData.xml",
})

# Suspicious substrings in OpenXML path names
_SUSPICIOUS_PATH_PATTERNS: Final[tuple[str, ...]] = (
    "vbaProject",
    "vbaData",
    "embeddings/oleObject",
    "embeddings/package",
)

# PDF Active Content Regex Patterns (Case sensitive & insensitive)
_PDF_JAVASCRIPT_PATTERN: Final[re.Pattern[bytes]] = re.compile(
    rb"/JavaScript|/JS\b", re.IGNORECASE
)
_PDF_LAUNCH_PATTERN: Final[re.Pattern[bytes]] = re.compile(rb"/Launch\b", re.IGNORECASE)
_PDF_OPENACTION_PATTERN: Final[re.Pattern[bytes]] = re.compile(
    rb"/OpenAction\b|/AA\b", re.IGNORECASE
)
_PDF_EMBEDDED_FILES_PATTERN: Final[re.Pattern[bytes]] = re.compile(
    rb"/EmbeddedFiles\b|/EmbeddedFile\b", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class SanitizationResult:
    """Outcome of document active content inspection and sanitization."""

    original_filename: str
    active_content_detected: bool
    stripped_items: tuple[str, ...]
    sanitized_bytes: bytes
    details: str
    is_safe: bool


def sanitize_openxml_bytes(
    content: bytes,
    filename: str = "document.docx",
) -> SanitizationResult:
    """Inspect and strip VBA macros, embedded OLE binaries, and active content."""
    if not (content.startswith(b"PK\x03\x04") or content.startswith(b"PK\x05\x06")):
        return SanitizationResult(
            original_filename=filename,
            active_content_detected=False,
            stripped_items=(),
            sanitized_bytes=content,
            details="Not a ZIP / OpenXML archive",
            is_safe=True,
        )

    stripped_items: list[str] = []
    active_detected = False

    try:
        input_zip = zipfile.ZipFile(io.BytesIO(content), "r")
        output_buffer = io.BytesIO()
        output_zip = zipfile.ZipFile(output_buffer, "w", compression=zipfile.ZIP_DEFLATED)

        # Pass 1: Identify all prohibited entries
        for item in input_zip.infolist():
            item_name = item.filename
            is_prohibited = (
                item_name in _PROHIBITED_OPENXML_ENTRIES
                or any(pat in item_name for pat in _SUSPICIOUS_PATH_PATTERNS)
            )
            if is_prohibited:
                active_detected = True
                stripped_items.append(item_name)

        # Pass 2: Write clean entries and normalize Content_Types
        for item in input_zip.infolist():
            if item.filename in stripped_items:
                logger.info("Stripped active content entry %s from %s", item.filename, filename)
                continue

            entry_data = input_zip.read(item.filename)
            if item.filename == "[Content_Types].xml" and active_detected:
                entry_data = entry_data.replace(
                    b"application/vnd.ms-excel.sheet.macroEnabled.main+xml",
                    b"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
                ).replace(
                    b"application/vnd.ms-word.document.macroEnabled.main+xml",
                    b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
                )

            output_zip.writestr(item, entry_data)

        output_zip.close()
        sanitized = output_buffer.getvalue()

        details = (
            f"Stripped {len(stripped_items)} active content items ({', '.join(stripped_items)})"
            if active_detected
            else "Clean OpenXML document (no active content detected)"
        )

        return SanitizationResult(
            original_filename=filename,
            active_content_detected=active_detected,
            stripped_items=tuple(stripped_items),
            sanitized_bytes=sanitized,
            details=details,
            is_safe=True,
        )

    except zipfile.BadZipFile as exc:
        logger.warning("Corrupt OpenXML zip archive %s: %s", filename, exc)
        return SanitizationResult(
            original_filename=filename,
            active_content_detected=False,
            stripped_items=(),
            sanitized_bytes=content,
            details=f"Corrupt ZIP archive: {exc}",
            is_safe=False,
        )


def inspect_pdf_active_content(
    content: bytes,
    filename: str = "document.pdf",
) -> SanitizationResult:
    """Scan PDF byte stream for embedded JavaScript, Launch actions, and Auto-exec triggers."""
    active_items: list[str] = []

    if _PDF_JAVASCRIPT_PATTERN.search(content):
        active_items.append("Embedded JavaScript (/JavaScript or /JS)")

    if _PDF_LAUNCH_PATTERN.search(content):
        active_items.append("Executable Launch Action (/Launch)")

    if _PDF_OPENACTION_PATTERN.search(content):
        active_items.append("Auto-execution Action (/OpenAction or /AA)")

    if _PDF_EMBEDDED_FILES_PATTERN.search(content):
        active_items.append("Embedded Files Container (/EmbeddedFiles)")

    has_active = len(active_items) > 0
    details = (
        f"Detected active content triggers in PDF: {', '.join(active_items)}"
        if has_active
        else "Clean PDF document (no active executable triggers found)"
    )

    return SanitizationResult(
        original_filename=filename,
        active_content_detected=has_active,
        stripped_items=tuple(active_items),
        sanitized_bytes=content,
        details=details,
        is_safe=not has_active,
    )


def sanitize_document_bytes(
    content: bytes,
    filename: str,
) -> SanitizationResult:
    """Unified sanitizer routing documents to specialized active content engines."""
    ext = Path(filename).suffix.lower()

    if ext in {".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".pptm"}:
        return sanitize_openxml_bytes(content, filename=filename)

    if ext == ".pdf":
        return inspect_pdf_active_content(content, filename=filename)

    # Plain text / other formats without executable binary streams
    return SanitizationResult(
        original_filename=filename,
        active_content_detected=False,
        stripped_items=(),
        sanitized_bytes=content,
        details="Document format does not contain executable binary containers",
        is_safe=True,
    )
