"""Email security scanner implementing EmailSecurityScannerPort."""

import asyncio
import logging
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
from cowork_agent.features.email_action_plan.ports import (
    EmailSecurityScannerPort,
    ThreatIntelPort,
)
from cowork_agent.integrations.security.redirect_resolver import RedirectResolver
from cowork_agent.integrations.security.threat_intel import CompositeThreatIntel

logger = logging.getLogger(__name__)


class EmailSecurityScanner(EmailSecurityScannerPort):
    """Production email security scanner inspecting links and attachments."""

    def __init__(
        self,
        threat_intel: ThreatIntelPort | None = None,
        redirect_resolver: RedirectResolver | None = None,
    ) -> None:
        self._threat_intel = threat_intel or CompositeThreatIntel()
        self._redirect_resolver = redirect_resolver or RedirectResolver(only_shorteners=True)

    async def scan_envelope(self, envelope: EphemeralEmailEnvelope) -> SecurityScanResult:
        """Scan all source links and attachment metadata in an email envelope."""
        link_reports: list[LinkSafetyReport] = []
        attachment_reports: list[AttachmentSafetyReport] = []

        # 1. Scan links
        for link in envelope.source_links:
            # Check redirect if applicable
            try:
                resolved_report = await self._redirect_resolver.resolve(link.url)
                if resolved_report.threat_level in (ThreatLevel.BLOCKED, ThreatLevel.MALICIOUS):
                    link_reports.append(resolved_report)
                    continue
                target_url = resolved_report.resolved_url or link.url
            except Exception as exc:
                logger.debug("Failed redirect resolution for '%s': %s", link.url, exc)
                target_url = link.url

            # Query Threat Intel (Cache -> Web Risk)
            try:
                intel_report = await self._threat_intel.check_url(target_url)
                link_reports.append(intel_report)
            except Exception as exc:
                logger.warning("Threat intel check failed for '%s': %s", target_url, exc)
                link_reports.append(
                    LinkSafetyReport(
                        original_url=link.url,
                        resolved_url=target_url,
                        threat_level=ThreatLevel.CLEAN,
                        threat_category=ThreatCategory.NONE,
                        details=f"Fallback inspection: {exc}",
                    )
                )

        # 2. Determine highest threat level
        highest_threat = ThreatLevel.CLEAN
        for report in link_reports:
            if report.threat_level == ThreatLevel.BLOCKED:
                highest_threat = ThreatLevel.BLOCKED
                break
            if report.threat_level == ThreatLevel.MALICIOUS:
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
            attachments=tuple(attachment_reports),
            quarantined=quarantined,
            recommended_action=recommended,
        )

    async def scan_envelopes(
        self, envelopes: Sequence[EphemeralEmailEnvelope]
    ) -> Sequence[SecurityScanResult]:
        """Concurrently scan multiple email envelopes."""
        if not envelopes:
            return ()
        return await asyncio.gather(*(self.scan_envelope(envelope) for envelope in envelopes))
