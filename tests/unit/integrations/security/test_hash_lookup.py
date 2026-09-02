"""Unit tests for Privacy-First SHA-256 Hash Threat Lookup Adapter."""

from __future__ import annotations

import httpx
import pytest

from cowork_agent.domain.target_contracts import ThreatCategory, ThreatLevel
from cowork_agent.integrations.security.hash_lookup import (
    EICAR_SHA256,
    CompositeHashLookup,
    KnownMalwareHashDatabase,
    MalwareBazaarHashLookup,
    VirusTotalHashLookup,
    compute_sha256,
)
from cowork_agent.integrations.security.threat_intel import CompositeThreatIntel, ThreatCache


def test_compute_sha256():
    assert compute_sha256(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    eicar_bytes = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    assert compute_sha256(eicar_bytes) == EICAR_SHA256


def test_known_malware_hash_database_eicar_and_custom():
    db = KnownMalwareHashDatabase()
    report = db.lookup(EICAR_SHA256, "eicar.com")
    assert report is not None
    assert report.threat_level == ThreatLevel.MALICIOUS
    assert report.threat_category == ThreatCategory.MALWARE
    assert report.is_safe_to_extract is False
    assert "EICAR" in (report.reason or "")

    # Unknown clean hash
    clean_hash = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert db.lookup(clean_hash, "clean.pdf") is None

    # Custom registered hash
    custom_db = KnownMalwareHashDatabase({
        clean_hash: ("Custom-Trojan", "Custom threat signature")
    })
    custom_report = custom_db.lookup(clean_hash, "custom.exe")
    assert custom_report is not None
    assert custom_report.threat_level == ThreatLevel.MALICIOUS


@pytest.mark.asyncio
async def test_virustotal_hash_lookup_malicious():
    sha256 = "1111111111111111111111111111111111111111111111111111111111111111"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("x-apikey") == "test-api-key"
        return httpx.Response(
            200,
            json={
                "data": {
                    "attributes": {
                        "meaningful_name": "trojan_dropper.exe",
                        "last_analysis_stats": {
                            "malicious": 45,
                            "suspicious": 3,
                            "undetected": 10,
                            "harmless": 0,
                        },
                    }
                }
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        vt = VirusTotalHashLookup(api_key="test-api-key", client=client)
        report = await vt.lookup(sha256, "sample.pdf")

    assert report.threat_level == ThreatLevel.MALICIOUS
    assert report.threat_category == ThreatCategory.MALWARE
    assert report.is_safe_to_extract is False
    assert "45 engines flagged malicious" in (report.reason or "")


@pytest.mark.asyncio
async def test_virustotal_hash_lookup_not_found_and_clean():
    sha256 = "2222222222222222222222222222222222222222222222222222222222222222"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"code": "NotFoundError"}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        vt = VirusTotalHashLookup(api_key="test-api-key", client=client)
        report = await vt.lookup(sha256, "clean_doc.pdf")

    assert report.threat_level == ThreatLevel.CLEAN
    assert report.threat_category == ThreatCategory.NONE
    assert report.is_safe_to_extract is True


@pytest.mark.asyncio
async def test_malwarebazaar_hash_lookup_hit():
    sha256 = "3333333333333333333333333333333333333333333333333333333333333333"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "query_status": "ok",
                "data": [
                    {
                        "signature": "AgentTesla",
                        "file_type": "exe",
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        mb = MalwareBazaarHashLookup(client=client)
        report = await mb.lookup(sha256, "invoice.exe")

    assert report.threat_level == ThreatLevel.MALICIOUS
    assert report.threat_category == ThreatCategory.MALWARE
    assert report.is_safe_to_extract is False
    assert "AgentTesla" in (report.reason or "")


@pytest.mark.asyncio
async def test_composite_hash_lookup_tier1_local_and_cache():
    cache = ThreatCache(default_ttl_seconds=3600)
    composite = CompositeHashLookup(cache=cache)

    # 1. Tier 1: Local DB match (EICAR)
    report_eicar = await composite.check_hash(EICAR_SHA256, "eicar.txt")
    assert report_eicar.threat_level == ThreatLevel.MALICIOUS
    assert report_eicar.threat_category == ThreatCategory.MALWARE

    # 2. Check that it was cached in ThreatCache
    cached_report = cache.get_hash(EICAR_SHA256)
    assert cached_report is not None
    assert cached_report.threat_level == ThreatLevel.MALICIOUS

    # 3. Invalid SHA-256 format
    invalid_report = await composite.check_hash("not-a-valid-sha256", "test.txt")
    assert invalid_report.threat_level == ThreatLevel.CLEAN
    assert "Invalid SHA-256" in (invalid_report.reason or "")


@pytest.mark.asyncio
async def test_composite_threat_intel_with_hash_lookup():
    hash_lookup = CompositeHashLookup()
    intel = CompositeThreatIntel(hash_lookup=hash_lookup)

    report = await intel.check_file_hash(EICAR_SHA256, "audit_signature_test.txt")
    assert report.threat_level == ThreatLevel.MALICIOUS
    assert report.threat_category == ThreatCategory.MALWARE
    assert report.is_safe_to_extract is False
