"""SSRF-protected safe URL redirect resolver."""

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlsplit

import httpx

from cowork_agent.domain.target_contracts import (
    LinkSafetyReport,
    ThreatCategory,
    ThreatLevel,
)
from cowork_agent.integrations.security.url_inspector import inspect_url

logger = logging.getLogger(__name__)

# Known cloud metadata hosts and IP literals that must never be reached
_BLOCKED_METADATA_HOSTS = frozenset(
    {
        "169.254.169.254",
        "metadata.google.internal",
        "metadata.internal",
        "instance-data",
    }
)

# Common URL shortener domains
URL_SHORTENER_DOMAINS = frozenset(
    {
        "bit.ly",
        "tinyurl.com",
        "t.co",
        "goo.gl",
        "ow.ly",
        "is.gd",
        "buff.ly",
        "rebrand.ly",
        "cutt.ly",
        "shorturl.at",
        "tiny.cc",
    }
)


class SSRFSecurityError(ValueError):
    """Raised when an outbound connection targets a forbidden or private address space."""


def is_private_or_restricted_ip(ip_str: str) -> bool:
    """Return True if the IP address is private, loopback, link-local, multicast, or reserved."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )
    except ValueError:
        return False


async def verify_host_not_ssrf(hostname: str) -> None:
    """Resolve hostname and verify none of the resolved IPs belong to private/restricted ranges."""
    if not hostname:
        raise SSRFSecurityError("Empty hostname cannot be verified for SSRF")

    lower_host = hostname.lower().strip()
    if lower_host in _BLOCKED_METADATA_HOSTS or lower_host.endswith(".internal"):
        raise SSRFSecurityError(f"Target host '{hostname}' is a reserved internal or metadata host")

    # If it is already an IP address string
    if is_private_or_restricted_ip(lower_host):
        raise SSRFSecurityError(f"Target IP '{hostname}' is in a private/restricted address space")

    # Resolve hostname to IPv4/IPv6 addresses
    loop = asyncio.get_running_loop()
    try:
        addr_info = await loop.getaddrinfo(
            hostname, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise ValueError(f"Failed to resolve DNS for host '{hostname}': {exc}") from exc

    for entry in addr_info:
        sockaddr = entry[4]
        ip_addr = str(sockaddr[0])
        if is_private_or_restricted_ip(ip_addr):
            raise SSRFSecurityError(
                f"Target host '{hostname}' resolves to private/restricted IP '{ip_addr}'"
            )


class RedirectResolver:
    """Safe HTTP redirect resolver with SSRF protection and hop limits."""

    def __init__(
        self,
        *,
        max_hops: int = 5,
        timeout_seconds: float = 2.0,
        only_shorteners: bool = False,
    ) -> None:
        self._max_hops = max_hops
        self._timeout_seconds = timeout_seconds
        self._only_shorteners = only_shorteners

    async def resolve(self, url: str) -> LinkSafetyReport:
        """Resolve redirect chain for a URL safely, checking SSRF at every intermediate hop."""
        initial_inspection = inspect_url(url)
        if initial_inspection.threat_level in (ThreatLevel.BLOCKED, ThreatLevel.MALICIOUS):
            return initial_inspection

        current_url = initial_inspection.resolved_url
        visited: set[str] = {current_url}

        parsed = urlsplit(current_url)
        hostname = (parsed.hostname or "").lower()

        if self._only_shorteners and hostname not in URL_SHORTENER_DOMAINS:
            return initial_inspection

        hops = 0
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=self._timeout_seconds,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Cowork-Agent/1.0"},
        ) as client:
            while hops < self._max_hops:
                parts = urlsplit(current_url)
                current_host = (parts.hostname or "").lower()

                # SSRF validation before making any network request
                try:
                    await verify_host_not_ssrf(current_host)
                except SSRFSecurityError as ssrf_err:
                    return LinkSafetyReport(
                        original_url=url,
                        resolved_url=current_url,
                        threat_level=ThreatLevel.BLOCKED,
                        threat_category=ThreatCategory.PARSER_EXPLOIT,
                        details=str(ssrf_err),
                    )
                except Exception as exc:
                    return LinkSafetyReport(
                        original_url=url,
                        resolved_url=current_url,
                        threat_level=ThreatLevel.SUSPICIOUS,
                        threat_category=ThreatCategory.NONE,
                        details=f"Host resolution failed: {exc}",
                    )

                # Attempt HEAD request to follow Location header
                try:
                    response = await client.head(current_url)
                    if response.status_code == 405:  # Method Not Allowed -> fallback to GET
                        response = await client.get(current_url)
                except (httpx.TimeoutException, httpx.RequestError) as net_err:
                    logger.debug("Redirect resolution stopped due to network error: %s", net_err)
                    break

                if response.is_redirect or response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        break
                    next_url = urljoin(current_url, location)

                    # Inspect the next hop
                    next_inspection = inspect_url(next_url)
                    if next_inspection.threat_level in (
                        ThreatLevel.BLOCKED,
                        ThreatLevel.MALICIOUS,
                    ):
                        return LinkSafetyReport(
                            original_url=url,
                            resolved_url=next_inspection.resolved_url,
                            threat_level=next_inspection.threat_level,
                            threat_category=next_inspection.threat_category,
                            details=f"Redirect hop led to threat: {next_inspection.details}",
                        )

                    if next_url in visited:
                        return LinkSafetyReport(
                            original_url=url,
                            resolved_url=next_url,
                            threat_level=ThreatLevel.SUSPICIOUS,
                            threat_category=ThreatCategory.NONE,
                            details=f"Redirect loop detected at '{next_url}'",
                        )

                    visited.add(next_url)
                    current_url = next_url
                    hops += 1
                else:
                    # Final non-redirect destination reached
                    break

        final_inspection = inspect_url(current_url)
        return LinkSafetyReport(
            original_url=url,
            resolved_url=current_url,
            threat_level=final_inspection.threat_level,
            threat_category=final_inspection.threat_category,
            details=final_inspection.details,
        )


class FakeRedirectResolver:
    """Deterministic fake redirect resolver for testing without outbound network calls."""

    def __init__(
        self,
        redirect_map: dict[str, str] | None = None,
        ssrf_hosts: set[str] | None = None,
    ) -> None:
        self.redirect_map = redirect_map or {}
        self.ssrf_hosts = ssrf_hosts or {"127.0.0.1", "localhost", "169.254.169.254", "10.0.0.1"}

    async def resolve(self, url: str) -> LinkSafetyReport:
        initial = inspect_url(url)
        if initial.threat_level in (ThreatLevel.BLOCKED, ThreatLevel.MALICIOUS):
            return initial

        current_url = initial.resolved_url
        visited: set[str] = {current_url}
        hops = 0

        while current_url in self.redirect_map and hops < 5:
            next_url = self.redirect_map[current_url]
            parts = urlsplit(next_url)
            host = (parts.hostname or "").lower()

            if host in self.ssrf_hosts or is_private_or_restricted_ip(host):
                return LinkSafetyReport(
                    original_url=url,
                    resolved_url=next_url,
                    threat_level=ThreatLevel.BLOCKED,
                    threat_category=ThreatCategory.PARSER_EXPLOIT,
                    details=f"Fake SSRF target blocked: {host}",
                )

            if next_url in visited:
                return LinkSafetyReport(
                    original_url=url,
                    resolved_url=next_url,
                    threat_level=ThreatLevel.SUSPICIOUS,
                    threat_category=ThreatCategory.NONE,
                    details="Redirect loop detected",
                )

            visited.add(next_url)
            current_url = next_url
            hops += 1

        final_inspection = inspect_url(current_url)
        return LinkSafetyReport(
            original_url=url,
            resolved_url=current_url,
            threat_level=final_inspection.threat_level,
            threat_category=final_inspection.threat_category,
            details=final_inspection.details,
        )


async def resolve_redirect_safe(url: str, *, max_hops: int = 5) -> LinkSafetyReport:
    """Convenience functional interface for safe redirect resolution."""
    resolver = RedirectResolver(max_hops=max_hops)
    return await resolver.resolve(url)
