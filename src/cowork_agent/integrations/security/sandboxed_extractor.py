"""Ephemeral Sandboxed Attachment Extractor (ADR-002 / ADR-003 Resource Guard)."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Final

from cowork_agent.domain.models import ExtractedAttachment, ExtractedUnit
from cowork_agent.domain.target_contracts import ThreatCategory, ThreatLevel
from cowork_agent.features.email_action_plan.ports import AttachmentExtractorPort
from cowork_agent.features.email_action_plan.schemas import ExtractionLimits
from cowork_agent.integrations.security.magic_inspector import inspect_attachment_bytes

logger = logging.getLogger(__name__)

DEFAULT_SANDBOX_TIMEOUT_SECONDS: Final[float] = 5.0
DEFAULT_HARD_MAX_BYTES: Final[int] = 25 * 1024 * 1024  # 25 MB hard ceiling
CHUNK_READ_SIZE: Final[int] = 64 * 1024  # 64 KB streaming chunks


def _extract_text_worker(
    file_path: Path,
    ext: str,
    max_characters: int,
    max_units: int,
) -> tuple[str, list[ExtractedUnit]]:
    """Worker function executed inside an isolated thread/process."""
    units: list[ExtractedUnit] = []
    combined_parts: list[str] = []
    total_chars = 0

    if ext in {".txt", ".csv", ".md", ".json", ".log", ".tsv", ".yaml", ".yml"}:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        truncated_content = content[:max_characters]
        units.append(
            ExtractedUnit(kind="text_document", label="Nội dung văn bản", text=truncated_content)
        )
        return truncated_content, units

    if ext == ".pdf":
        try:
            import pdfplumber
            with pdfplumber.open(str(file_path)) as pdf:
                num_pages = min(len(pdf.pages), max_units)
                for page_num in range(num_pages):
                    page = pdf.pages[page_num]
                    page_text = (page.extract_text() or "").strip()
                    if not page_text:
                        continue
                    if total_chars + len(page_text) > max_characters:
                        allowed = max(0, max_characters - total_chars)
                        page_text = page_text[:allowed]

                    unit = ExtractedUnit(
                        kind="page",
                        label=f"Trang {page_num + 1}",
                        text=page_text,
                    )
                    units.append(unit)
                    combined_parts.append(page_text)
                    total_chars += len(page_text)
                    if total_chars >= max_characters:
                        break

            return "\n\n".join(combined_parts), units
        except Exception as exc:
            logger.warning("PDF extraction error in sandbox worker: %s", exc)
            raise

    if ext in {".docx"}:
        try:
            from docx import Document
            doc = Document(str(file_path))
            p_idx = 1
            for p in doc.paragraphs:
                text = p.text.strip()
                if not text:
                    continue
                if total_chars + len(text) > max_characters:
                    text = text[: max(0, max_characters - total_chars)]

                unit = ExtractedUnit(kind="paragraph", label=f"Đoạn {p_idx}", text=text)
                units.append(unit)
                combined_parts.append(text)
                total_chars += len(text)
                p_idx += 1
                if len(units) >= max_units or total_chars >= max_characters:
                    break

            return "\n\n".join(combined_parts), units
        except Exception as exc:
            logger.warning("DOCX extraction error in sandbox worker: %s", exc)
            raise

    # Unsupported formats
    raise ValueError(f"Unsupported file format for sandboxed extraction: {ext}")


class EphemeralSandboxedExtractor(AttachmentExtractorPort):
    """Resource-isolated, ephemeral attachment extractor with strict limits and auto-cleanup."""

    def __init__(
        self,
        *,
        default_timeout_seconds: float = DEFAULT_SANDBOX_TIMEOUT_SECONDS,
        hard_max_bytes: int = DEFAULT_HARD_MAX_BYTES,
    ) -> None:
        self._default_timeout = default_timeout_seconds
        self._hard_max_bytes = hard_max_bytes

    async def extract(
        self,
        attachment_id: str,
        filename: str,
        declared_mime_type: str,
        content: AsyncIterator[bytes],
        limits: ExtractionLimits,
    ) -> ExtractedAttachment:
        effective_max_bytes = min(limits.max_bytes, self._hard_max_bytes)
        effective_timeout = (
            float(limits.timeout_seconds)
            if limits.timeout_seconds > 0
            else self._default_timeout
        )

        hasher = hashlib.sha256()
        buffer = bytearray()
        total_streamed = 0
        size_exceeded = False

        # 1. Stream consumption with hard memory ceiling guard
        try:
            async for chunk in content:
                total_streamed += len(chunk)
                if total_streamed > effective_max_bytes:
                    size_exceeded = True
                    break
                buffer.extend(chunk)
                hasher.update(chunk)
        except Exception as exc:
            logger.warning("Error consuming attachment stream %s: %s", filename, exc)
            return ExtractedAttachment(
                attachment_id=attachment_id,
                filename=filename,
                detected_mime_type=declared_mime_type,
                sha256=hasher.hexdigest(),
                status="failed",
                text=None,
                units=(),
                warning_code="STREAM_ERROR",
            )

        sha256_digest = hasher.hexdigest()

        if size_exceeded:
            logger.warning(
                "Attachment %s exceeded hard size limit (%d > %d bytes)",
                filename,
                total_streamed,
                effective_max_bytes,
            )
            return ExtractedAttachment(
                attachment_id=attachment_id,
                filename=filename,
                detected_mime_type=declared_mime_type,
                sha256=sha256_digest,
                status="size_exceeded",
                text=None,
                units=(),
                warning_code="SIZE_EXCEEDED",
            )

        raw_bytes = bytes(buffer)

        # 2. Magic Bytes & Security Triage Pre-Check
        safety_report = inspect_attachment_bytes(
            filename=filename,
            content=raw_bytes,
        )

        if safety_report.threat_level in (ThreatLevel.BLOCKED, ThreatLevel.MALICIOUS):
            logger.warning(
                "Sandboxed extractor quarantined dangerous attachment %s (%s): %s",
                filename,
                safety_report.threat_category.value,
                safety_report.reason,
            )
            warning_code = (
                "QUARANTINED_ZIP_BOMB"
                if safety_report.threat_category == ThreatCategory.ZIP_BOMB
                else "QUARANTINED_MALWARE"
            )
            return ExtractedAttachment(
                attachment_id=attachment_id,
                filename=filename,
                detected_mime_type=safety_report.detected_mime_type or declared_mime_type,
                sha256=sha256_digest,
                status="quarantined",
                text=None,
                units=(),
                warning_code=warning_code,
            )

        # 3. Ephemeral Sandbox Execution (Auto-cleanup guaranteed via TemporaryDirectory)
        ext = Path(filename).suffix.lower()
        try:
            with tempfile.TemporaryDirectory(prefix="cowork_sandbox_") as tmp_dir:
                tmp_file_path = Path(tmp_dir) / f"input_{attachment_id}{ext}"
                tmp_file_path.write_bytes(raw_bytes)

                try:
                    async with asyncio.timeout(effective_timeout):
                        text, units = await asyncio.to_thread(
                            _extract_text_worker,
                            tmp_file_path,
                            ext,
                            limits.max_characters,
                            limits.max_units,
                        )
                except TimeoutError:
                    logger.warning(
                        "Extraction timed out after %.1fs for %s",
                        effective_timeout,
                        filename,
                    )
                    return ExtractedAttachment(
                        attachment_id=attachment_id,
                        filename=filename,
                        detected_mime_type=safety_report.detected_mime_type or declared_mime_type,
                        sha256=sha256_digest,
                        status="timeout",
                        text=None,
                        units=(),
                        warning_code="TIMEOUT",
                    )
                except ValueError as ve:
                    logger.debug("Unsupported format for %s: %s", filename, ve)
                    return ExtractedAttachment(
                        attachment_id=attachment_id,
                        filename=filename,
                        detected_mime_type=safety_report.detected_mime_type or declared_mime_type,
                        sha256=sha256_digest,
                        status="unsupported",
                        text=None,
                        units=(),
                        warning_code="UNSUPPORTED_FORMAT",
                    )
                except Exception as exc:
                    logger.warning("Sandbox extraction failed for %s: %s", filename, exc)
                    return ExtractedAttachment(
                        attachment_id=attachment_id,
                        filename=filename,
                        detected_mime_type=safety_report.detected_mime_type or declared_mime_type,
                        sha256=sha256_digest,
                        status="failed",
                        text=None,
                        units=(),
                        warning_code="EXTRACTION_FAILED",
                    )

            # Ephemeral guarantee: TemporaryDirectory is completely deleted upon exit
            return ExtractedAttachment(
                attachment_id=attachment_id,
                filename=filename,
                detected_mime_type=safety_report.detected_mime_type or declared_mime_type,
                sha256=sha256_digest,
                status="ok",
                text=text,
                units=tuple(units),
                warning_code=None,
            )

        finally:
            # Explicitly release in-memory raw buffer
            del buffer
            del raw_bytes
