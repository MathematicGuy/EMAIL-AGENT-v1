"""Privacy-First SHA-256 Hash Threat Lookup Adapter (VirusTotal / MalwareBazaar / Local DB)."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import Final

import httpx

from cowork_agent.domain.target_contracts import (
    AttachmentSafetyReport,
    ThreatCategory,
    ThreatLevel,
)
from cowork_agent.integrations.security.threat_intel import ThreatCache

logger = logging.getLogger(__name__)

# SHA-256 hexadecimal validation regex
_SHA256_REGEX: Final[re.Pattern[str]] = re.compile(r"^[a-fA-F0-9]{64}$")

# Standard EICAR test string SHA-256
EICAR_SHA256: Final[str] = "275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f"

# Known malicious malware hashes database (local offline cache)
_KNOWN_MALICIOUS_HASHES: Final[dict[str, tuple[str, str]]] = {
    # Standard EICAR Antivirus Test Signature
    EICAR_SHA256.lower(): (
        "EICAR-Standard-AV-Test-File",
        "Standard antivirus verification test signature",
    ),
    # WannaCry Ransomware (mssecsvc.exe)
    "24d004a104d4d54034dbcffc2a4b19a11f39008a575aa614ea04703480b1022c": (
        "Ransom:Win32/WannaCrypt",
        "WannaCry ransomware binary",
    ),
    # Emotet Trojan Dropper
    "41ed837130282697843825ee581b7e4dd38b00799787e915fa0bcda2652b04ea": (
        "Trojan:Win32/Emotet",
        "Emotet banking trojan payload",
    ),
}

VIRUSTOTAL_FILES_URL: Final[str] = "https://www.virustotal.com/api/v3/files/{sha256}"
MALWAREBAZAAR_API_URL: Final[str] = "https://mb-api.abuse.ch/api/v1/"


def compute_sha256(content: bytes) -> str:
    """Compute SHA-256 hexadecimal digest for raw content bytes."""
    return hashlib.sha256(content).hexdigest()


class KnownMalwareHashDatabase:
    """Local offline database of known malicious file hashes."""

    def __init__(self, additional_hashes: dict[str, tuple[str, str]] | None = None) -> None:
        self._database = dict(_KNOWN_MALICIOUS_HASHES)
        if additional_hashes:
            self._database.update({k.lower(): v for k, v in additional_hashes.items()})

    def lookup(self, sha256: str, filename: str = "") -> AttachmentSafetyReport | None:
        normalized_hash = sha256.lower().strip()
        match = self._database.get(normalized_hash)
        if match is not None:
            signature, description = match
            return AttachmentSafetyReport(
                filename=filename or f"file_{sha256[:8]}",
                sha256=sha256,
                detected_mime_type="application/octet-stream",
                threat_level=ThreatLevel.MALICIOUS,
                threat_category=ThreatCategory.MALWARE,
                is_safe_to_extract=False,
                reason=f"Local Signature Match: {signature} ({description})",
            )
        return None


class VirusTotalHashLookup:
    """Privacy-First VirusTotal v3 Hash Reputation Lookup."""

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 3.0,
    ) -> None:
        self._api_key = api_key
        self._client = client
        self._timeout = timeout_seconds

    async def lookup(self, sha256: str, filename: str = "") -> AttachmentSafetyReport:
        if not self._api_key:
            return AttachmentSafetyReport(
                filename=filename,
                sha256=sha256,
                detected_mime_type="application/octet-stream",
                threat_level=ThreatLevel.CLEAN,
                threat_category=ThreatCategory.NONE,
                is_safe_to_extract=True,
                reason="VirusTotal API key not configured",
            )

        url = VIRUSTOTAL_FILES_URL.format(sha256=sha256.lower().strip())
        headers = {"x-apikey": self._api_key, "Accept": "application/json"}

        try:
            if self._client:
                response = await self._client.get(url, headers=headers, timeout=self._timeout)
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.get(url, headers=headers, timeout=self._timeout)

            if response.status_code == 404:
                return AttachmentSafetyReport(
                    filename=filename,
                    sha256=sha256,
                    detected_mime_type="application/octet-stream",
                    threat_level=ThreatLevel.CLEAN,
                    threat_category=ThreatCategory.NONE,
                    is_safe_to_extract=True,
                    reason="VirusTotal: Hash not found in threat database",
                )

            if response.status_code != 200:
                logger.warning(
                    "VirusTotal API returned status %d for hash %s",
                    response.status_code,
                    sha256,
                )
                return AttachmentSafetyReport(
                    filename=filename,
                    sha256=sha256,
                    detected_mime_type="application/octet-stream",
                    threat_level=ThreatLevel.CLEAN,
                    threat_category=ThreatCategory.NONE,
                    is_safe_to_extract=True,
                    reason=f"VirusTotal API HTTP {response.status_code}",
                )

            payload = response.json()
            data = payload.get("data", {})
            attributes = data.get("attributes", {})
            stats = attributes.get("last_analysis_stats", {})

            malicious = stats.get("malicious", 0)
            suspicious = stats.get("suspicious", 0)
            meaningful_name = attributes.get("meaningful_name", filename)

            if malicious >= 3 or (malicious + suspicious) >= 5:
                return AttachmentSafetyReport(
                    filename=meaningful_name or filename,
                    sha256=sha256,
                    detected_mime_type="application/octet-stream",
                    threat_level=ThreatLevel.MALICIOUS,
                    threat_category=ThreatCategory.MALWARE,
                    is_safe_to_extract=False,
                    reason=f"VirusTotal detected malware: {malicious} engines flagged malicious",
                )
            if malicious >= 1 or suspicious >= 2:
                return AttachmentSafetyReport(
                    filename=meaningful_name or filename,
                    sha256=sha256,
                    detected_mime_type="application/octet-stream",
                    threat_level=ThreatLevel.SUSPICIOUS,
                    threat_category=ThreatCategory.MALWARE,
                    is_safe_to_extract=False,
                    reason=(
                        f"VirusTotal detected suspicious score "
                        f"({malicious} mal, {suspicious} susp)"
                    ),
                )

            return AttachmentSafetyReport(
                filename=meaningful_name or filename,
                sha256=sha256,
                detected_mime_type="application/octet-stream",
                threat_level=ThreatLevel.CLEAN,
                threat_category=ThreatCategory.NONE,
                is_safe_to_extract=True,
                reason="VirusTotal: 0 detection engines flagged malicious",
            )

        except (httpx.TimeoutException, httpx.RequestError) as exc:
            logger.warning("VirusTotal lookup failed for hash %s: %s", sha256, exc)
            return AttachmentSafetyReport(
                filename=filename,
                sha256=sha256,
                detected_mime_type="application/octet-stream",
                threat_level=ThreatLevel.CLEAN,
                threat_category=ThreatCategory.NONE,
                is_safe_to_extract=True,
                reason=f"VirusTotal lookup timeout/error: {exc}",
            )


class MalwareBazaarHashLookup:
    """MalwareBazaar (Abuse.ch) Hash Reputation Lookup."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 3.0,
    ) -> None:
        self._client = client
        self._timeout = timeout_seconds

    async def lookup(self, sha256: str, filename: str = "") -> AttachmentSafetyReport:
        data = {"query": "get_info", "hash": sha256.lower().strip()}

        try:
            if self._client:
                response = await self._client.post(
                    MALWAREBAZAAR_API_URL, data=data, timeout=self._timeout
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        MALWAREBAZAAR_API_URL, data=data, timeout=self._timeout
                    )

            if response.status_code != 200:
                return AttachmentSafetyReport(
                    filename=filename,
                    sha256=sha256,
                    detected_mime_type="application/octet-stream",
                    threat_level=ThreatLevel.CLEAN,
                    threat_category=ThreatCategory.NONE,
                    is_safe_to_extract=True,
                    reason=f"MalwareBazaar HTTP {response.status_code}",
                )

            res_json = response.json()
            query_status = res_json.get("query_status")

            if query_status == "ok":
                items = res_json.get("data", [])
                sig = items[0].get("signature") if items else "MalwareBazaar-Sample"
                file_type = items[0].get("file_type") if items else "binary"
                return AttachmentSafetyReport(
                    filename=filename,
                    sha256=sha256,
                    detected_mime_type=f"application/x-{file_type}",
                    threat_level=ThreatLevel.MALICIOUS,
                    threat_category=ThreatCategory.MALWARE,
                    is_safe_to_extract=False,
                    reason=f"MalwareBazaar matched signature: {sig}",
                )

            return AttachmentSafetyReport(
                filename=filename,
                sha256=sha256,
                detected_mime_type="application/octet-stream",
                threat_level=ThreatLevel.CLEAN,
                threat_category=ThreatCategory.NONE,
                is_safe_to_extract=True,
                reason="MalwareBazaar: Hash not found",
            )

        except (httpx.TimeoutException, httpx.RequestError) as exc:
            logger.warning("MalwareBazaar lookup failed for hash %s: %s", sha256, exc)
            return AttachmentSafetyReport(
                filename=filename,
                sha256=sha256,
                detected_mime_type="application/octet-stream",
                threat_level=ThreatLevel.CLEAN,
                threat_category=ThreatCategory.NONE,
                is_safe_to_extract=True,
                reason=f"MalwareBazaar lookup error: {exc}",
            )


class CompositeHashLookup:
    """Multi-tier Privacy-First SHA-256 Threat Lookup (Local DB -> Cache -> VT / MB)."""

    def __init__(
        self,
        *,
        local_db: KnownMalwareHashDatabase | None = None,
        cache: ThreatCache | None = None,
        virustotal: VirusTotalHashLookup | None = None,
        malwarebazaar: MalwareBazaarHashLookup | None = None,
    ) -> None:
        self._local_db = local_db or KnownMalwareHashDatabase()
        self._cache = cache or ThreatCache()
        self._virustotal = virustotal
        self._malwarebazaar = malwarebazaar

    async def check_hash(self, sha256: str, filename: str = "") -> AttachmentSafetyReport:
        """Query all reputation tiers safely without uploading file content."""
        clean_hash = sha256.lower().strip()
        if not _SHA256_REGEX.match(clean_hash):
            return AttachmentSafetyReport(
                filename=filename,
                sha256=sha256,
                detected_mime_type="application/octet-stream",
                threat_level=ThreatLevel.CLEAN,
                threat_category=ThreatCategory.NONE,
                is_safe_to_extract=True,
                reason="Invalid SHA-256 format",
            )

        # 1. Tier 1: Local Offline Malware Signature DB (< 1ms)
        local_match = self._local_db.lookup(clean_hash, filename)
        if local_match is not None:
            self._cache.set_hash(clean_hash, local_match)
            return local_match

        # 2. Tier 2: In-Memory / Redis Threat Cache (< 5ms)
        cached = self._cache.get_hash(clean_hash)
        if cached is not None:
            return cached

        # 3. Tier 3: Cloud Threat Feeds (VirusTotal / MalwareBazaar)
        tasks = []
        if self._virustotal is not None:
            tasks.append(self._virustotal.lookup(clean_hash, filename))
        if self._malwarebazaar is not None:
            tasks.append(self._malwarebazaar.lookup(clean_hash, filename))

        if tasks:
            reports = await asyncio.gather(*tasks, return_exceptions=True)
            for rep in reports:
                if isinstance(rep, AttachmentSafetyReport):
                    if rep.threat_level in (ThreatLevel.MALICIOUS, ThreatLevel.BLOCKED):
                        self._cache.set_hash(clean_hash, rep)
                        return rep
                    if rep.threat_level == ThreatLevel.SUSPICIOUS:
                        self._cache.set_hash(clean_hash, rep)
                        return rep

        fallback_report = AttachmentSafetyReport(
            filename=filename,
            sha256=clean_hash,
            detected_mime_type="application/octet-stream",
            threat_level=ThreatLevel.CLEAN,
            threat_category=ThreatCategory.NONE,
            is_safe_to_extract=True,
            reason="Hash threat lookup clean (no threats reported)",
        )
        self._cache.set_hash(clean_hash, fallback_report)
        return fallback_report
