"""Deterministic test fakes for security ports."""

from collections.abc import Sequence
from datetime import UTC, datetime

from cowork_agent.domain.target_contracts import (
    AttachmentSafetyReport,
    EphemeralEmailEnvelope,
    LinkSafetyReport,
    SecurityScanResult,
    ThreatCategory,
    ThreatLevel,
)
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
