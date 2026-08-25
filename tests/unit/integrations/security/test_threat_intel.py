"""Unit tests for ThreatCache, GoogleWebRiskThreatIntel, and FakeThreatIntel (Task 1.3)."""

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from cowork_agent.config import SecuritySettings
from cowork_agent.domain.target_contracts import (
    AttachmentSafetyReport,
    LinkSafetyReport,
    ThreatCategory,
    ThreatLevel,
)
from cowork_agent.integrations.security.fakes import FakeThreatIntel
from cowork_agent.integrations.security.threat_intel import (
    CompositeThreatIntel,
    GoogleWebRiskThreatIntel,
    ThreatCache,
)


def test_security_settings_from_env():
    # Test default values
    settings = SecuritySettings.from_env({})
    assert settings.enabled is True
    assert settings.webrisk_api_key == ""
    assert settings.cache_ttl_seconds == 86_400
    assert settings.quarantine_malicious_emails is True

    # Test custom values
    custom_env = {
        "SECURITY_SCAN_ENABLED": "false",
        "SECURITY_WEBRISK_API_KEY": "AIzaSyTestKey123",
        "SECURITY_CACHE_TTL_SECONDS": "3600",
        "SECURITY_QUARANTINE_ENABLED": "false",
    }
    custom_settings = SecuritySettings.from_env(custom_env)
    assert custom_settings.enabled is False
    assert custom_settings.webrisk_api_key == "AIzaSyTestKey123"
    assert custom_settings.cache_ttl_seconds == 3600
    assert custom_settings.quarantine_malicious_emails is False


def test_threat_cache_url_put_get_expire():
    cache = ThreatCache(default_ttl_seconds=3600, max_entries=5)
    report = LinkSafetyReport(
        original_url="https://example.com",
        resolved_url="https://example.com",
        threat_level=ThreatLevel.CLEAN,
        threat_category=ThreatCategory.NONE,
    )

    # Miss before put
    assert cache.get_url("https://example.com") is None

    # Hit after put
    cache.set_url("https://example.com", report)
    assert cache.get_url("https://example.com") == report

    # Expiration
    cache.set_url("https://expire.com", report, ttl_seconds=0)
    time.sleep(0.01)
    assert cache.get_url("https://expire.com") is None


def test_threat_cache_hash_put_get_clear():
    cache = ThreatCache()
    report = AttachmentSafetyReport(
        filename="invoice.pdf",
        sha256="abc123sha",
        detected_mime_type="application/pdf",
        threat_level=ThreatLevel.CLEAN,
        threat_category=ThreatCategory.NONE,
        is_safe_to_extract=True,
    )
    cache.set_hash("abc123sha", report)
    assert cache.get_hash("abc123sha") == report

    cache.clear()
    assert cache.get_hash("abc123sha") is None


@pytest.mark.asyncio
async def test_google_webrisk_phishing_detected():
    intel = GoogleWebRiskThreatIntel(api_key="test-key")

    mock_response = httpx.Response(
        status_code=200,
        json={"threat": {"threatTypes": ["SOCIAL_ENGINEERING"]}},
        request=httpx.Request("GET", "https://webrisk.googleapis.com"),
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        report = await intel.check_url("https://phishing-site.example.com")
        assert report.threat_level == ThreatLevel.MALICIOUS
        assert report.threat_category == ThreatCategory.PHISHING
        assert "SOCIAL_ENGINEERING" in (report.details or "")


@pytest.mark.asyncio
async def test_google_webrisk_malware_detected():
    intel = GoogleWebRiskThreatIntel(api_key="test-key")

    mock_response = httpx.Response(
        status_code=200,
        json={"threat": {"threatTypes": ["MALWARE"]}},
        request=httpx.Request("GET", "https://webrisk.googleapis.com"),
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        report = await intel.check_url("https://malware-download.example.com")
        assert report.threat_level == ThreatLevel.MALICIOUS
        assert report.threat_category == ThreatCategory.MALWARE


@pytest.mark.asyncio
async def test_google_webrisk_clean_url():
    intel = GoogleWebRiskThreatIntel(api_key="test-key")

    mock_response = httpx.Response(
        status_code=200,
        json={},  # Empty object means no threats found
        request=httpx.Request("GET", "https://webrisk.googleapis.com"),
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        report = await intel.check_url("https://clean-site.example.com")
        assert report.threat_level == ThreatLevel.CLEAN
        assert report.threat_category == ThreatCategory.NONE


@pytest.mark.asyncio
async def test_google_webrisk_empty_key_fallback():
    intel = GoogleWebRiskThreatIntel(api_key="")
    # With empty API key, should return static inspection result without making HTTP calls
    report = await intel.check_url("https://clean-site.example.com")
    assert report.threat_level == ThreatLevel.CLEAN


@pytest.mark.asyncio
async def test_composite_threat_intel_caching_and_fast_path():
    cache = ThreatCache()
    mock_cloud = AsyncMock(spec=GoogleWebRiskThreatIntel)
    mock_cloud.check_url.return_value = LinkSafetyReport(
        original_url="https://tested.example.com",
        resolved_url="https://tested.example.com",
        threat_level=ThreatLevel.CLEAN,
        threat_category=ThreatCategory.NONE,
    )

    composite = CompositeThreatIntel(cloud_intel=mock_cloud, cache=cache)

    # 1. Dangerous scheme is blocked directly in static fast-path without cloud call
    blocked_report = await composite.check_url("javascript:alert(1)")
    assert blocked_report.threat_level == ThreatLevel.BLOCKED
    assert mock_cloud.check_url.call_count == 0

    # 2. First call for a normal URL hits cloud intel and gets cached
    first_result = await composite.check_url("https://tested.example.com")
    assert first_result.threat_level == ThreatLevel.CLEAN
    assert mock_cloud.check_url.call_count == 1

    # 3. Second call for the same URL hits cache (< 5ms), cloud intel not called again
    second_result = await composite.check_url("https://tested.example.com")
    assert second_result.threat_level == ThreatLevel.CLEAN
    assert mock_cloud.check_url.call_count == 1


@pytest.mark.asyncio
async def test_fake_threat_intel_recording():
    fake = FakeThreatIntel()
    fake.register_threat_url(
        "https://known-evil.com",
        threat_level=ThreatLevel.MALICIOUS,
        threat_category=ThreatCategory.PHISHING,
        details="Phishing portal",
    )
    fake.register_malware_hash(
        "sha256-evil-sample",
        "trojan.exe",
        threat_level=ThreatLevel.BLOCKED,
        reason="Known ransomware signature",
    )

    url_rep = await fake.check_url("https://known-evil.com")
    assert url_rep.threat_level == ThreatLevel.MALICIOUS
    assert "https://known-evil.com" in fake.checked_urls

    hash_rep = await fake.check_file_hash("sha256-evil-sample", "trojan.exe")
    assert hash_rep.threat_level == ThreatLevel.BLOCKED
    assert "sha256-evil-sample" in fake.checked_hashes
