"""Unit tests for URL inspection, IDN homograph detection, and SSRF resolver (Task 1.2)."""

import pytest

from cowork_agent.domain.target_contracts import (
    BodyFormat,
    ThreatCategory,
    ThreatLevel,
)
from cowork_agent.integrations.mailbox.normalization import normalize_body
from cowork_agent.integrations.security.redirect_resolver import (
    FakeRedirectResolver,
    SSRFSecurityError,
    is_private_or_restricted_ip,
    verify_host_not_ssrf,
)
from cowork_agent.integrations.security.url_inspector import (
    inspect_url,
    is_homograph_spoof,
    normalize_url,
)


def test_normalize_url_removes_invisible_controls_and_unescapes():
    raw = "  https://example.com/path\u200b?name=John&amp;id=123\ufeff  "
    normalized = normalize_url(raw)
    assert normalized == "https://example.com/path?name=John&id=123"


def test_inspect_clean_standard_urls():
    clean_urls = [
        "https://google.com",
        "https://github.com/project/repo",
        "https://sub.domain.example.com:443/test?query=val#frag",
        "http://news.ycombinator.com/item?id=12345",
    ]
    for url in clean_urls:
        report = inspect_url(url)
        assert report.threat_level == ThreatLevel.CLEAN
        assert report.threat_category == ThreatCategory.NONE
        assert report.details is None


def test_inspect_dangerous_schemes():
    dangerous = [
        ("javascript:alert(document.cookie)", "javascript"),
        ("data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==", "data"),
        ("file:///etc/passwd", "file"),
        ("vbscript:MsgBox(1)", "vbscript"),
        ("blob:https://example.com/uuid-1234", "blob"),
    ]
    for url, scheme in dangerous:
        report = inspect_url(url)
        assert report.threat_level == ThreatLevel.BLOCKED
        assert report.threat_category == ThreatCategory.PARSER_EXPLOIT
        assert scheme in (report.details or "")


def test_inspect_non_http_schemes():
    report = inspect_url("ftp://ftp.example.com/file.txt")
    assert report.threat_level == ThreatLevel.SUSPICIOUS
    assert "ftp" in (report.details or "")


def test_homograph_spoof_detection_cyrillic_lookalikes():
    # 'gооgle.com' with Cyrillic 'о' (U+043E)
    cyrillic_google = "https://g\u043e\u043egle.com/login"
    report = inspect_url(cyrillic_google)
    assert report.threat_level == ThreatLevel.MALICIOUS
    assert report.threat_category == ThreatCategory.HOMOGRAPH_SPOOF
    assert "Homograph attack" in (report.details or "") or "Mixed-script" in (report.details or "")

    # 'pаypal.com' with Cyrillic 'а' (U+0430)
    cyrillic_paypal = "https://p\u0430ypal.com/signin"
    report_paypal = inspect_url(cyrillic_paypal)
    assert report_paypal.threat_level == ThreatLevel.MALICIOUS
    assert report_paypal.threat_category == ThreatCategory.HOMOGRAPH_SPOOF

    # Punycode encoded 'xn--ggle-55da.com' (gооgle.com with cyrillic 'о')
    punycode_url = "https://xn--ggle-55da.com/auth"
    report_punycode = inspect_url(punycode_url)
    assert report_punycode.threat_level == ThreatLevel.MALICIOUS
    assert report_punycode.threat_category == ThreatCategory.HOMOGRAPH_SPOOF


def test_homograph_spoof_safe_domains():
    is_spoof, reason = is_homograph_spoof("google.com")
    assert is_spoof is False
    assert reason is None

    is_spoof, reason = is_homograph_spoof("github.com")
    assert is_spoof is False


def test_inspect_ip_literal_urls():
    # Private IP
    private_report = inspect_url("http://192.168.1.1/admin")
    assert private_report.threat_level == ThreatLevel.BLOCKED
    assert private_report.threat_category == ThreatCategory.PARSER_EXPLOIT
    assert "internal private/loopback" in (private_report.details or "")

    loopback_report = inspect_url("http://127.0.0.1:8080/api")
    assert loopback_report.threat_level == ThreatLevel.BLOCKED

    # Public IP literal is suspicious in emails
    public_report = inspect_url("http://93.184.216.34/download")
    assert public_report.threat_level == ThreatLevel.SUSPICIOUS
    assert "direct public IP" in (public_report.details or "")


def test_is_private_or_restricted_ip():
    assert is_private_or_restricted_ip("127.0.0.1") is True
    assert is_private_or_restricted_ip("10.0.1.5") is True
    assert is_private_or_restricted_ip("172.16.5.10") is True
    assert is_private_or_restricted_ip("192.168.0.1") is True
    assert is_private_or_restricted_ip("169.254.169.254") is True
    assert is_private_or_restricted_ip("0.0.0.0") is True
    assert is_private_or_restricted_ip("::1") is True
    assert is_private_or_restricted_ip("fe80::1") is True

    # Public IPs
    assert is_private_or_restricted_ip("8.8.8.8") is False
    assert is_private_or_restricted_ip("93.184.216.34") is False
    assert is_private_or_restricted_ip("1.1.1.1") is False


@pytest.mark.asyncio
async def test_verify_host_not_ssrf_blocks_private_and_metadata():
    with pytest.raises(SSRFSecurityError, match="reserved internal or metadata host"):
        await verify_host_not_ssrf("169.254.169.254")

    with pytest.raises(SSRFSecurityError, match="reserved internal or metadata host"):
        await verify_host_not_ssrf("metadata.google.internal")

    with pytest.raises(SSRFSecurityError, match="private/restricted address space"):
        await verify_host_not_ssrf("127.0.0.1")

    with pytest.raises(SSRFSecurityError, match="private/restricted address space"):
        await verify_host_not_ssrf("10.0.50.1")


@pytest.mark.asyncio
async def test_fake_redirect_resolver_follows_chain():
    fake = FakeRedirectResolver(
        redirect_map={
            "https://bit.ly/clean-report": "https://tinyurl.com/xyz",
            "https://tinyurl.com/xyz": "https://portal.example.com/reports/q3",
        }
    )
    result = await fake.resolve("https://bit.ly/clean-report")
    assert result.threat_level == ThreatLevel.CLEAN
    assert result.resolved_url == "https://portal.example.com/reports/q3"


@pytest.mark.asyncio
async def test_fake_redirect_resolver_blocks_ssrf_destination():
    fake = FakeRedirectResolver(
        redirect_map={
            "https://bit.ly/internal-admin": "http://127.0.0.1:8000/secret",
        }
    )
    result = await fake.resolve("https://bit.ly/internal-admin")
    assert result.threat_level == ThreatLevel.BLOCKED
    assert result.threat_category == ThreatCategory.PARSER_EXPLOIT
    assert "SSRF target blocked" in (result.details or "")


@pytest.mark.asyncio
async def test_fake_redirect_resolver_detects_loop():
    fake = FakeRedirectResolver(
        redirect_map={
            "https://bit.ly/loop1": "https://bit.ly/loop2",
            "https://bit.ly/loop2": "https://bit.ly/loop1",
        }
    )
    result = await fake.resolve("https://bit.ly/loop1")
    assert result.threat_level == ThreatLevel.SUSPICIOUS
    assert "loop" in (result.details or "").lower()


def test_normalization_stamps_threat_level_on_source_links():
    clean_body = (
        "Please check the documentation at https://example.com/docs and submit your feedback."
    )
    text, body_format, links = normalize_body(plain_parts=[clean_body])
    assert body_format == BodyFormat.TEXT
    assert len(links) == 1
    assert links[0].url == "https://example.com/docs"
    assert links[0].threat_level == ThreatLevel.CLEAN

    # Body containing homograph spoof link
    malicious_body = (
        "Security alert: reset your password at https://g\u043e\u043egle.com/reset immediately."
    )
    m_text, m_format, m_links = normalize_body(plain_parts=[malicious_body])
    assert len(m_links) == 1
    assert m_links[0].threat_level == ThreatLevel.MALICIOUS
