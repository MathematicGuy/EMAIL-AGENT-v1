"""Deterministic test fakes for security ports."""

import hashlib
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

from cowork_agent.domain.models import ExtractedAttachment, ExtractedUnit
from cowork_agent.domain.target_contracts import (
    AttachmentSafetyReport,
    EphemeralEmailEnvelope,
    LinkSafetyReport,
    SecurityScanResult,
    ThreatCategory,
    ThreatLevel,
)
from cowork_agent.features.email_action_plan.schemas import ExtractionLimits
from cowork_agent.integrations.security.url_inspector import inspect_url


class FakeThreatIntel:
    """Deterministic fake implementing ThreatIntelPort."""

    def __init__(
        self,
        *,
        url_threats: dict[str, LinkSafetyReport] | None = None,
        hash_threats: dict[str, AttachmentSafetyReport] | None = None,
    ) -> None:
        self.url_threats: dict[str, LinkSafetyReport] = url_threats or {}
        self.hash_threats: dict[str, AttachmentSafetyReport] = hash_threats or {}
        self.checked_urls: list[str] = []
        self.checked_hashes: list[str] = []

    def register_threat_url(
        self,
        url: str,
        threat_level: ThreatLevel = ThreatLevel.MALICIOUS,
        threat_category: ThreatCategory = ThreatCategory.PHISHING,
        details: str = "Registered fake threat URL",
    ) -> None:
        self.url_threats[url] = LinkSafetyReport(
            original_url=url,
            resolved_url=url,
            threat_level=threat_level,
            threat_category=threat_category,
            details=details,
        )

    def register_malware_hash(
        self,
        sha256: str,
        filename: str,
        threat_level: ThreatLevel = ThreatLevel.MALICIOUS,
        threat_category: ThreatCategory = ThreatCategory.MALWARE,
        reason: str = "Registered fake malware hash",
    ) -> None:
        self.hash_threats[sha256] = AttachmentSafetyReport(
            filename=filename,
            sha256=sha256,
            detected_mime_type="application/octet-stream",
            threat_level=threat_level,
            threat_category=threat_category,
            is_safe_to_extract=False,
            reason=reason,
        )

    async def check_url(self, url: str) -> LinkSafetyReport:
        self.checked_urls.append(url)
        if url in self.url_threats:
            return self.url_threats[url]
        return inspect_url(url)

    async def check_file_hash(self, sha256: str, filename: str) -> AttachmentSafetyReport:
        self.checked_hashes.append(sha256)
        if sha256 in self.hash_threats:
            return self.hash_threats[sha256]
        return AttachmentSafetyReport(
            filename=filename,
            sha256=sha256,
            detected_mime_type="application/octet-stream",
            threat_level=ThreatLevel.CLEAN,
            threat_category=ThreatCategory.NONE,
            is_safe_to_extract=True,
        )


class FakeEmailSecurityScanner:
    """Deterministic fake implementing EmailSecurityScannerPort."""

    def __init__(
        self,
        threat_intel: FakeThreatIntel | None = None,
        *,
        default_threat_level: ThreatLevel = ThreatLevel.CLEAN,
    ) -> None:
        self.threat_intel = threat_intel or FakeThreatIntel()
        self.default_threat_level = default_threat_level
        self.scanned_envelope_ids: list[str] = []

    async def scan_envelope(self, envelope: EphemeralEmailEnvelope) -> SecurityScanResult:
        self.scanned_envelope_ids.append(envelope.gmail_message_id)
        link_reports: list[LinkSafetyReport] = []
        highest_threat = self.default_threat_level

        for link in envelope.source_links:
            report = await self.threat_intel.check_url(link.url)
            link_reports.append(report)
            if report.threat_level == ThreatLevel.BLOCKED:
                highest_threat = ThreatLevel.BLOCKED
            elif (
                report.threat_level == ThreatLevel.MALICIOUS
                and highest_threat != ThreatLevel.BLOCKED
            ):
                highest_threat = ThreatLevel.MALICIOUS
            elif (
                report.threat_level == ThreatLevel.SUSPICIOUS
                and highest_threat == ThreatLevel.CLEAN
            ):
                highest_threat = ThreatLevel.SUSPICIOUS

        quarantined = highest_threat in (ThreatLevel.MALICIOUS, ThreatLevel.BLOCKED)
        if quarantined:
            recommended = "quarantine"
        elif highest_threat == ThreatLevel.SUSPICIOUS:
            recommended = "warn"
        else:
            recommended = "allow"

        return SecurityScanResult(
            email_id=envelope.gmail_message_id,
            overall_threat_level=highest_threat,
            scanned_at=datetime.now(UTC),
            links=tuple(link_reports),
            attachments=(),
            quarantined=quarantined,
            recommended_action=recommended,
        )

    async def scan_envelopes(
        self, envelopes: Sequence[EphemeralEmailEnvelope]
    ) -> Sequence[SecurityScanResult]:
        return [await self.scan_envelope(envelope) for envelope in envelopes]


class FakeClamAVScanner:
    """Deterministic test fake for ClamAVScanner."""

    def __init__(
        self,
        *,
        is_online: bool = True,
        version: str = "ClamAV 1.4.0/FakeTestSignatures",
        signatures: dict[bytes, str] | None = None,
    ) -> None:
        self.is_online = is_online
        self.version = version
        self.signatures = signatures or {
            b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*": (
                "Win.Test.EICAR_HDB-1"
            ),
        }
        self.scanned_contents: list[bytes] = []

    async def ping(self) -> bool:
        return self.is_online

    async def get_version(self) -> str | None:
        return self.version if self.is_online else None

    async def scan_bytes(
        self, content: bytes, filename: str = ""
    ) -> AttachmentSafetyReport:
        self.scanned_contents.append(content)
        sha256_hash = hashlib.sha256(content).hexdigest()

        if not self.is_online:
            return AttachmentSafetyReport(
                filename=filename,
                sha256=sha256_hash,
                detected_mime_type="application/octet-stream",
                threat_level=ThreatLevel.CLEAN,
                threat_category=ThreatCategory.NONE,
                is_safe_to_extract=True,
                reason="ClamAV daemon offline (fake)",
            )

        for pattern, virus_name in self.signatures.items():
            if pattern in content:
                return AttachmentSafetyReport(
                    filename=filename,
                    sha256=sha256_hash,
                    detected_mime_type="application/octet-stream",
                    threat_level=ThreatLevel.MALICIOUS,
                    threat_category=ThreatCategory.MALWARE,
                    is_safe_to_extract=False,
                    reason=f"ClamAV detected malware signature: {virus_name}",
                )

        return AttachmentSafetyReport(
            filename=filename,
            sha256=sha256_hash,
            detected_mime_type="application/octet-stream",
            threat_level=ThreatLevel.CLEAN,
            threat_category=ThreatCategory.NONE,
            is_safe_to_extract=True,
            reason="ClamAV: OK (no virus signatures found)",
        )

    async def scan_file(
        self, file_path: Path | str, original_filename: str | None = None
    ) -> AttachmentSafetyReport:
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
        return await self.scan_bytes(content, filename=original_filename or path.name)


class FakeAttachmentExtractor:
    """Deterministic test fake implementing AttachmentExtractorPort."""

    def __init__(
        self,
        *,
        fixed_text: str = "Fake extracted text content",
        forced_status: str = "ok",
        forced_warning: str | None = None,
    ) -> None:
        self.fixed_text = fixed_text
        self.forced_status = forced_status
        self.forced_warning = forced_warning
        self.extracted_attachments: list[str] = []

    async def extract(
        self,
        attachment_id: str,
        filename: str,
        declared_mime_type: str,
        content: AsyncIterator[bytes],
        limits: ExtractionLimits,
    ) -> ExtractedAttachment:
        self.extracted_attachments.append(filename)
        buffer = bytearray()
        async for chunk in content:
            buffer.extend(chunk)

        sha256_hash = hashlib.sha256(buffer).hexdigest()
        text = self.fixed_text if self.forced_status == "ok" else None
        units = (
            (ExtractedUnit(kind="text", label="Fake unit", text=self.fixed_text),)
            if self.forced_status == "ok"
            else ()
        )

        return ExtractedAttachment(
            attachment_id=attachment_id,
            filename=filename,
            detected_mime_type=declared_mime_type,
            sha256=sha256_hash,
            status=self.forced_status,
            text=text,
            units=units,
            warning_code=self.forced_warning,
        )


