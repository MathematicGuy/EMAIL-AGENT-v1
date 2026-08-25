"""Security adapters and inspectors for email links, attachments, and threat intelligence."""

from .redirect_resolver import RedirectResolver, SSRFSecurityError, resolve_redirect_safe
from .url_inspector import (
    inspect_url,
    is_homograph_spoof,
    normalize_url,
)

__all__ = [
    "RedirectResolver",
    "SSRFSecurityError",
    "inspect_url",
    "is_homograph_spoof",
    "normalize_url",
    "resolve_redirect_safe",
]
