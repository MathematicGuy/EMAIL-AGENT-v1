"""URL parsing, normalization, and IDN homograph attack inspection."""

import html
import ipaddress
import re
import unicodedata
from urllib.parse import urlsplit

from cowork_agent.domain.target_contracts import (
    LinkSafetyReport,
    ThreatCategory,
    ThreatLevel,
)

# Invisible and bi-directional formatting control characters often used to obfuscate URLs
_SUSPICIOUS_CONTROLS = re.compile(
    r"[\u0000-\u001f\u007f-\u009f\u00ad\u200b-\u200f\u2028-\u202e\u2060-\u206f\ufeff]"
)

# Well-known high-value brand names frequently targeted by homograph attacks
_HIGH_VALUE_TARGETS = frozenset(
    {
        "google",
        "apple",
        "microsoft",
        "amazon",
        "paypal",
        "github",
        "facebook",
        "instagram",
        "twitter",
        "netflix",
        "chase",
        "wellsfargo",
        "bankofamerica",
        "binance",
        "coinbase",
        "dropbox",
        "yahoo",
        "outlook",
        "gmail",
        "cloudflare",
    }
)

# Cyrillic to Latin visual lookalike mapping (confusables)
_CONFUSABLE_MAP = {
    "\u0430": "a",  # Cyrillic small letter a
    "\u0441": "c",  # Cyrillic small letter es
    "\u0435": "e",  # Cyrillic small letter ie
    "\u0456": "i",  # Cyrillic small letter byelorussian-ukrainian i
    "\u0458": "j",  # Cyrillic small letter je
    "\u043e": "o",  # Cyrillic small letter o
    "\u0440": "p",  # Cyrillic small letter er
    "\u0455": "s",  # Cyrillic small letter dze
    "\u0445": "x",  # Cyrillic small letter ha
    "\u0443": "y",  # Cyrillic small letter u
    "\u0410": "A",  # Cyrillic capital letter A
    "\u0412": "B",  # Cyrillic capital letter Ve
    "\u0421": "C",  # Cyrillic capital letter Es
    "\u0415": "E",  # Cyrillic capital letter Ie
    "\u041d": "H",  # Cyrillic capital letter En
    "\u0406": "I",  # Cyrillic capital letter Byelorussian-Ukrainian I
    "\u0408": "J",  # Cyrillic capital letter Je
    "\u041a": "K",  # Cyrillic capital letter Ka
    "\u041c": "M",  # Cyrillic capital letter Em
    "\u041e": "O",  # Cyrillic capital letter O
    "\u0420": "P",  # Cyrillic capital letter Er
    "\u0422": "T",  # Cyrillic capital letter Te
    "\u0425": "X",  # Cyrillic capital letter Ha
    "\u0423": "Y",  # Cyrillic capital letter U
    # Greek lookalikes
    "\u03b1": "a",  # Greek small letter alpha
    "\u03bf": "o",  # Greek small letter omicron
    "\u03c1": "p",  # Greek small letter rho
    "\u03bd": "v",  # Greek small letter nu
    "\u0391": "A",  # Greek capital letter Alpha
    "\u0392": "B",  # Greek capital letter Beta
    "\u0395": "E",  # Greek capital letter Epsilon
    "\u0397": "H",  # Greek capital letter Eta
    "\u0399": "I",  # Greek capital letter Iota
    "\u039a": "K",  # Greek capital letter Kappa
    "\u039c": "M",  # Greek capital letter Mu
    "\u039d": "N",  # Greek capital letter Nu
    "\u039f": "O",  # Greek capital letter Omicron
    "\u03a1": "P",  # Greek capital letter Rho
    "\u03a4": "T",  # Greek capital letter Tau
    "\u03a7": "X",  # Greek capital letter Chi
}


def normalize_url(url: str) -> str:
    """Clean and normalize URL by stripping whitespace, unescaping, and removing control chars."""
    cleaned = html.unescape(url).strip()
    cleaned = _SUSPICIOUS_CONTROLS.sub("", cleaned)
    return cleaned


def _get_script(char: str) -> str:
    """Identify the primary script family of a character."""
    if char.isascii():
        return "LATIN" if char.isalpha() else "COMMON"
    name = unicodedata.name(char, "")
    if "CYRILLIC" in name:
        return "CYRILLIC"
    if "GREEK" in name:
        return "GREEK"
    if "ARABIC" in name:
        return "ARABIC"
    if "HEBREW" in name:
        return "HEBREW"
    if "DEVANAGARI" in name:
        return "DEVANAGARI"
    if "HANGUL" in name:
        return "HANGUL"
    if "HIRAGANA" in name or "KATAKANA" in name or "CJK" in name:
        return "CJK"
    return "OTHER"


def _confusable_transliterate(text: str) -> str:
    """Replace visually confusable Cyrillic/Greek characters with their Latin lookalikes."""
    return "".join(_CONFUSABLE_MAP.get(char, char) for char in text)


def is_homograph_spoof(domain: str) -> tuple[bool, str | None]:
    """Check whether a domain name contains mixed-script or homograph attack patterns."""
    if not domain:
        return False, None

    # Handle Punycode (IDN) encoding e.g. xn--gogle-pra.com -> gооgle.com
    labels = domain.lower().split(".")
    decoded_labels: list[str] = []
    for label in labels:
        if label.startswith("xn--"):
            try:
                decoded = label.encode("ascii").decode("idna")
                decoded_labels.append(decoded)
            except Exception:
                decoded_labels.append(label)
        else:
            decoded_labels.append(label)

    for label in decoded_labels:
        if not label:
            continue

        scripts: set[str] = set()
        has_confusables = False
        for char in label:
            script = _get_script(char)
            if script not in {"COMMON", "OTHER"}:
                scripts.add(script)
            if char in _CONFUSABLE_MAP:
                has_confusables = True

        # Rule 1: Mixed-script in the same label (e.g. Latin mixed with Cyrillic or Greek)
        if len(scripts) > 1 and ("CYRILLIC" in scripts or "GREEK" in scripts):
            script_names = ", ".join(sorted(scripts))
            return True, f"Mixed-script label '{label}' combines scripts: {script_names}"

        # Rule 2: Whole-script spoofing of high-value brands (e.g. all-Cyrillic brand names)
        if has_confusables:
            transliterated = _confusable_transliterate(label)
            if transliterated in _HIGH_VALUE_TARGETS and transliterated != label:
                return (
                    True,
                    f"Homograph attack spoofing '{transliterated}' using confusables in '{label}'",
                )

    return False, None


def inspect_url(url: str) -> LinkSafetyReport:
    """Inspect and classify a URL for security threats (homographs, dangerous schemes)."""
    normalized = normalize_url(url)
    if not normalized:
        return LinkSafetyReport(
            original_url=url,
            resolved_url="",
            threat_level=ThreatLevel.SUSPICIOUS,
            threat_category=ThreatCategory.NONE,
            details="Empty or invalid URL",
        )

    # Check for dangerous embedded schemes (XSS / execution vectors)
    lower_url = normalized.lower()
    dangerous_schemes = ("javascript:", "data:", "vbscript:", "blob:", "file:")
    for scheme in dangerous_schemes:
        if lower_url.startswith(scheme):
            return LinkSafetyReport(
                original_url=url,
                resolved_url=normalized,
                threat_level=ThreatLevel.BLOCKED,
                threat_category=ThreatCategory.PARSER_EXPLOIT,
                details=f"Blocked dangerous scheme '{scheme.rstrip(':')}' in URL",
            )

    try:
        parts = urlsplit(normalized)
    except Exception as exc:
        return LinkSafetyReport(
            original_url=url,
            resolved_url=normalized,
            threat_level=ThreatLevel.BLOCKED,
            threat_category=ThreatCategory.PARSER_EXPLOIT,
            details=f"Malformed URL structure: {exc}",
        )

    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return LinkSafetyReport(
            original_url=url,
            resolved_url=normalized,
            threat_level=ThreatLevel.SUSPICIOUS,
            threat_category=ThreatCategory.NONE,
            details=f"Non-standard HTTP(S) scheme: '{scheme}'",
        )

    hostname = parts.hostname or ""
    if not hostname:
        return LinkSafetyReport(
            original_url=url,
            resolved_url=normalized,
            threat_level=ThreatLevel.SUSPICIOUS,
            threat_category=ThreatCategory.NONE,
            details="Missing hostname in HTTP(S) URL",
        )

    # Check for homograph and mixed-script spoofing
    is_spoof, spoof_reason = is_homograph_spoof(hostname)
    if is_spoof:
        return LinkSafetyReport(
            original_url=url,
            resolved_url=normalized,
            threat_level=ThreatLevel.MALICIOUS,
            threat_category=ThreatCategory.HOMOGRAPH_SPOOF,
            details=spoof_reason,
        )

    # Check if hostname is a raw IP literal (often suspicious in emails)
    try:
        ip_obj = ipaddress.ip_address(hostname)
        if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local:
            return LinkSafetyReport(
                original_url=url,
                resolved_url=normalized,
                threat_level=ThreatLevel.BLOCKED,
                threat_category=ThreatCategory.PARSER_EXPLOIT,
                details=f"URL targets internal private/loopback IP address: {hostname}",
            )
        # Public IP literal
        return LinkSafetyReport(
            original_url=url,
            resolved_url=normalized,
            threat_level=ThreatLevel.SUSPICIOUS,
            threat_category=ThreatCategory.NONE,
            details=f"URL uses direct public IP address ({hostname}) instead of domain name",
        )
    except ValueError:
        # Hostname is a regular domain name
        pass

    return LinkSafetyReport(
        original_url=url,
        resolved_url=normalized,
        threat_level=ThreatLevel.CLEAN,
        threat_category=ThreatCategory.NONE,
        details=None,
    )
