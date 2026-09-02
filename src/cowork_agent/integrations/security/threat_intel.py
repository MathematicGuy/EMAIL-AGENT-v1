"""Threat intelligence integration with Google Web Risk API and in-memory TTL caching."""

import logging
import time

import httpx

from cowork_agent.domain.target_contracts import (
    AttachmentSafetyReport,
    LinkSafetyReport,
    ThreatCategory,
    ThreatLevel,
)
from cowork_agent.integrations.security.resilience import CircuitBreaker
from cowork_agent.integrations.security.url_inspector import inspect_url

logger = logging.getLogger(__name__)

# Google Web Risk API URI search endpoint
_WEBRISK_API_URL = "https://webrisk.googleapis.com/v1/uris:search"
_DEFAULT_THREAT_TYPES = ("MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE")


class ThreatCache:
    """In-memory cache with TTL expiration for URL and file hash threat reports."""

    def __init__(self, default_ttl_seconds: int = 86_400, max_entries: int = 10_000) -> None:
        self._default_ttl = default_ttl_seconds
        self._max_entries = max_entries
        self._url_cache: dict[str, tuple[float, LinkSafetyReport]] = {}
        self._hash_cache: dict[str, tuple[float, AttachmentSafetyReport]] = {}

    def get_url(self, url: str) -> LinkSafetyReport | None:
        now = time.monotonic()
        entry = self._url_cache.get(url)
        if entry is None:
            return None
        expires_at, report = entry
        if now >= expires_at:
            self._url_cache.pop(url, None)
            return None
        return report

    def set_url(
        self, url: str, report: LinkSafetyReport, ttl_seconds: int | None = None
    ) -> None:
        if len(self._url_cache) >= self._max_entries:
            self._evict_expired_urls()
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        self._url_cache[url] = (time.monotonic() + ttl, report)

    def get_hash(self, sha256: str) -> AttachmentSafetyReport | None:
        now = time.monotonic()
        entry = self._hash_cache.get(sha256)
        if entry is None:
            return None
        expires_at, report = entry
        if now >= expires_at:
            self._hash_cache.pop(sha256, None)
            return None
        return report

    def set_hash(
        self,
        sha256: str,
        report: AttachmentSafetyReport,
        ttl_seconds: int | None = None,
    ) -> None:
        if len(self._hash_cache) >= self._max_entries:
            self._evict_expired_hashes()
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        self._hash_cache[sha256] = (time.monotonic() + ttl, report)

    def clear(self) -> None:
        self._url_cache.clear()
        self._hash_cache.clear()

    def _evict_expired_urls(self) -> None:
        now = time.monotonic()
        expired = [k for k, (exp, _) in self._url_cache.items() if now > exp]
        for k in expired:
            self._url_cache.pop(k, None)
        if len(self._url_cache) >= self._max_entries:
            # Pop oldest 10%
            oldest_keys = list(self._url_cache.keys())[: max(1, self._max_entries // 10)]
            for k in oldest_keys:
                self._url_cache.pop(k, None)

    def _evict_expired_hashes(self) -> None:
        now = time.monotonic()
        expired = [k for k, (exp, _) in self._hash_cache.items() if now > exp]
        for k in expired:
            self._hash_cache.pop(k, None)
        if len(self._hash_cache) >= self._max_entries:
            oldest_keys = list(self._hash_cache.keys())[: max(1, self._max_entries // 10)]
            for k in oldest_keys:
                self._hash_cache.pop(k, None)


class GoogleWebRiskThreatIntel:
    """Threat intelligence adapter calling Google Web Risk API with Circuit Breaker protection."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 3.0,
        threat_types: tuple[str, ...] = _DEFAULT_THREAT_TYPES,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._threat_types = threat_types
        self._circuit_breaker = circuit_breaker or CircuitBreaker(
            name="GoogleWebRisk",
            failure_threshold=3,
            recovery_timeout_seconds=30.0,
        )

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        return self._circuit_breaker

    async def check_url(self, url: str) -> LinkSafetyReport:
        """Query Google Web Risk API with circuit breaking and graceful local fallback."""
        static_report = inspect_url(url)
        if static_report.threat_level in (ThreatLevel.BLOCKED, ThreatLevel.MALICIOUS):
            return static_report

        if not self._api_key.strip():
            return static_report

        params: list[tuple[str, str | int | float | bool | None]] = [
            ("key", self._api_key),
            ("uri", url),
        ]
        for threat_type in self._threat_types:
            params.append(("threatTypes", threat_type))

        async def _fetch_webrisk() -> LinkSafetyReport:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.get(_WEBRISK_API_URL, params=params)
                if response.status_code == 200:
                    data = response.json()
                    threat_data = data.get("threat")
                    if threat_data:
                        threat_types_found = threat_data.get("threatTypes", [])
                        return self._map_webrisk_response(url, threat_types_found)
                    # Empty threat object means clean
                    return LinkSafetyReport(
                        original_url=url,
                        resolved_url=url,
                        threat_level=ThreatLevel.CLEAN,
                        threat_category=ThreatCategory.NONE,
                    )
                raise httpx.HTTPStatusError(
                    f"Google Web Risk API HTTP {response.status_code}",
                    request=response.request,
                    response=response,
                )

        report, is_degraded = await self._circuit_breaker.execute(
            _fetch_webrisk,
            fallback=static_report,
        )

        if is_degraded:
            prefix = "[SECURITY_SCAN_DEGRADED: WebRisk circuit open/timeout]"
            degraded_details = f"{prefix} {static_report.details or ''}".strip()
            return LinkSafetyReport(
                original_url=static_report.original_url,
                resolved_url=static_report.resolved_url,
                threat_level=static_report.threat_level,
                threat_category=static_report.threat_category,
                details=degraded_details,
            )

        return report

    async def check_file_hash(self, sha256: str, filename: str) -> AttachmentSafetyReport:
        """Fallback check for file hash."""
        return AttachmentSafetyReport(
            filename=filename,
            sha256=sha256,
            detected_mime_type="application/octet-stream",
            threat_level=ThreatLevel.CLEAN,
            threat_category=ThreatCategory.NONE,
            is_safe_to_extract=True,
        )

    def _map_webrisk_response(self, url: str, threat_types: list[str]) -> LinkSafetyReport:
        types_upper = {t.upper() for t in threat_types}
        if "SOCIAL_ENGINEERING" in types_upper:
            return LinkSafetyReport(
                original_url=url,
                resolved_url=url,
                threat_level=ThreatLevel.MALICIOUS,
                threat_category=ThreatCategory.PHISHING,
                details="Google Web Risk: SOCIAL_ENGINEERING (Phishing) detected",
            )
        if "MALWARE" in types_upper:
            return LinkSafetyReport(
                original_url=url,
                resolved_url=url,
                threat_level=ThreatLevel.MALICIOUS,
                threat_category=ThreatCategory.MALWARE,
                details="Google Web Risk: MALWARE detected",
            )
        if "UNWANTED_SOFTWARE" in types_upper:
            return LinkSafetyReport(
                original_url=url,
                resolved_url=url,
                threat_level=ThreatLevel.SUSPICIOUS,
                threat_category=ThreatCategory.MALWARE,
                details="Google Web Risk: UNWANTED_SOFTWARE detected",
            )
        return LinkSafetyReport(
            original_url=url,
            resolved_url=url,
            threat_level=ThreatLevel.SUSPICIOUS,
            threat_category=ThreatCategory.NONE,
            details=f"Google Web Risk: Unknown threat types {threat_types}",
        )


class CompositeThreatIntel:
    """Composite Threat Intelligence combining static inspection, cache, and cloud feeds."""

    def __init__(
        self,
        *,
        cloud_intel: GoogleWebRiskThreatIntel | None = None,
        hash_lookup: object | None = None,
        cache: ThreatCache | None = None,
    ) -> None:
        self._cloud_intel = cloud_intel
        self._hash_lookup = hash_lookup
        self._cache = cache or ThreatCache()

    async def check_url(self, url: str) -> LinkSafetyReport:
        # Step 1: Static checks (fast, offline)
        static_report = inspect_url(url)
        if static_report.threat_level in (ThreatLevel.BLOCKED, ThreatLevel.MALICIOUS):
            return static_report

        # Step 2: Cache check (< 5ms)
        cached = self._cache.get_url(url)
        if cached is not None:
            return cached

        # Step 3: Cloud threat intelligence lookup (< 150ms)
        if self._cloud_intel is not None:
            cloud_report = await self._cloud_intel.check_url(url)
            self._cache.set_url(url, cloud_report)
            return cloud_report

        self._cache.set_url(url, static_report)
        return static_report

    async def check_file_hash(self, sha256: str, filename: str) -> AttachmentSafetyReport:
        cached = self._cache.get_hash(sha256)
        if cached is not None:
            return cached

        if self._hash_lookup is not None and hasattr(self._hash_lookup, "check_hash"):
            hash_report = await self._hash_lookup.check_hash(sha256, filename)
            if isinstance(hash_report, AttachmentSafetyReport):
                self._cache.set_hash(sha256, hash_report)
                return hash_report

        clean_report = AttachmentSafetyReport(
            filename=filename,
            sha256=sha256,
            detected_mime_type="application/octet-stream",
            threat_level=ThreatLevel.CLEAN,
            threat_category=ThreatCategory.NONE,
            is_safe_to_extract=True,
        )
        self._cache.set_hash(sha256, clean_report)
        return clean_report
