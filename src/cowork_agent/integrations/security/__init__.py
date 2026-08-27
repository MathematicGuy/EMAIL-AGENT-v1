"""Security adapters and inspectors for email links, attachments, and threat intelligence."""

from .fakes import FakeEmailSecurityScanner, FakeThreatIntel
from .redirect_resolver import (
    FakeRedirectResolver,
    RedirectResolver,
    SSRFSecurityError,
    resolve_redirect_safe,
)
from .scanner import EmailSecurityScanner
from .threat_intel import (
    CompositeThreatIntel,
    GoogleWebRiskThreatIntel,
    ThreatCache,
)
from .url_inspector import (
    inspect_url,
    is_homograph_spoof,
    normalize_url,
)

__all__ = [
    "CompositeThreatIntel",
    "EmailSecurityScanner",
    "FakeEmailSecurityScanner",
    "FakeRedirectResolver",
    "FakeThreatIntel",
    "GoogleWebRiskThreatIntel",
    "RedirectResolver",
    "SSRFSecurityError",
    "ThreatCache",
    "inspect_url",
    "is_homograph_spoof",
    "normalize_url",
    "resolve_redirect_safe",
]
