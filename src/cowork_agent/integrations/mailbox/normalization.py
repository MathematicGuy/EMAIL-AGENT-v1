"""Shared conversion of plain/HTML mailbox bodies into the envelope format."""

import html
import re
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlparse

from cowork_agent.domain.target_contracts import BodyFormat, EmailSourceLink
from cowork_agent.integrations.security.url_inspector import inspect_url


def normalize_body(
    plain_parts: Iterable[str] = (), html_parts: Iterable[str] = ()
) -> tuple[str, BodyFormat, tuple[EmailSourceLink, ...]]:
    """Normalize body parts while retaining deterministic source-link references."""
    plain = [strip_suspicious_format_controls(item) for item in plain_parts]
    rich = [strip_suspicious_format_controls(item) for item in html_parts]
    links = LinkCollector()
    if plain:
        plain_text = "\n".join(plain).strip()
        plain_urls = set(iter_urls(plain_text))
        normalized = plain_to_text(plain_text, links)
        rich_urls = {url for part in rich for url, _ in extract_html_links(part, links)}
        missing = [
            link
            for link in links.as_tuple()
            if link.url in rich_urls
            and link.url not in plain_urls
            and include_in_llm_link_appendix(link)
        ]
        if missing:
            normalized += "\n\nLiên kết trong email:\n" + "\n".join(
                f"{link.label} [{link.ref}]" for link in missing
            )
        return remove_separator_lines(normalized), BodyFormat.TEXT, links.as_tuple()
    if not rich:
        return "", BodyFormat.TEXT, ()
    normalized = html_to_text("\n".join(rich), links).strip()
    return remove_separator_lines(normalized), BodyFormat.HTML_CONVERTED, links.as_tuple()


@dataclass(slots=True)
class LinkCollector:
    _links: list[EmailSourceLink]
    _positions: dict[str, int]

    def __init__(self) -> None:
        self._links = []
        self._positions = {}

    def add(self, url: str, label: str | None = None) -> str:
        report = inspect_url(url)
        clean_url = report.resolved_url or url
        threat_level = report.threat_level
        position = self._positions.get(clean_url)
        if position is not None:
            existing = self._links[position]
            if existing.label is None and label:
                self._links[position] = EmailSourceLink(
                    existing.ref, label, existing.url, threat_level=existing.threat_level
                )
            return existing.ref
        ref = f"link{len(self._links) + 1}"
        self._positions[clean_url] = len(self._links)
        self._links.append(EmailSourceLink(ref, label, clean_url, threat_level=threat_level))
        return ref

    def as_tuple(self) -> tuple[EmailSourceLink, ...]:
        return tuple(self._links)


_URL = re.compile(r"https?://[^\s<>\"'\[\]]+", re.IGNORECASE)
_MARKDOWN = re.compile(r"\[([^\]\r\n]+)\]\((https?://[^\s)]+)\)", re.IGNORECASE)
_HTML_LIKE = re.compile(
    r"(?is)<!--|</?(?:a|body|br|div|html|p|strong|table|tbody|td|th|thead|tr|v:[\w-]+|w:[\w-]+)\b"
)
_TRAILING = ".,;:!?"
_CONTROLS = frozenset("\u00ad\u034f\u200b\u200e\u200f\ufeff")
_SEPARATOR = re.compile(r"^[ \t]*[-=_*|—–]{2,}[ \t]*$")
_MULTI_REF = re.compile(r"\[([^\]\r\n]+)\]\(\s*((?:\[link\d+\]\s*){2,})\r?$", re.MULTILINE)
_CROSS_REF = re.compile(
    r"\[([^\]\r\n]+)\]\(\s*(\[link\d+\])\r?\n[^\[\]()\r\n]*\)", re.MULTILINE
)
_FOOTER_LABEL = re.compile(
    r"(?i)^(?:unsubscribe|switch to the weekly digest|careers?|help center|"
    r"privacy(?: policy)?|terms(?: of service)?|control your recommendations|"
    r"become a (?:medium )?member|get (?:medium )?on (?:the )?"
    r"(?:app store|google play))$"
)
_FOOTER_PATH = re.compile(
    r"(?i)(?:unsubscribe|privacy|terms-of-service|settings/notifications|missioncontrol|jobs-at-|/plans(?:/|$))"
)


def _split_url(value: str) -> tuple[str, str]:
    url = value.rstrip(_TRAILING)
    return url, value[len(url) :]


def iter_urls(value: str) -> list[str]:
    return [url for match in _URL.finditer(value) if (url := _split_url(match.group(0))[0])]


def _replace_urls(value: str, links: LinkCollector) -> str:
    def replace(match: re.Match[str]) -> str:
        url, punctuation = _split_url(match.group(0))
        return f"[{links.add(url)}]{punctuation}" if url else match.group(0)

    replaced = _URL.sub(replace, value)
    replaced = re.sub(r"\[\[(link\d+)\]\]", r"[\1]", replaced)
    return re.sub(r"<\[(link\d+)\]>", r"[\1]", replaced)


def _replace_markdown(value: str, links: LinkCollector) -> str:
    def replace(match: re.Match[str]) -> str:
        label = re.sub(r"\s+", " ", match.group(1)).strip()
        url = match.group(2)
        source_label = _source_label(label, url)
        ref = links.add(url, source_label)
        return f"{source_label} [{ref}]" if source_label else ""

    return _MARKDOWN.sub(replace, value)


def plain_to_text(value: str, links: LinkCollector) -> str:
    value = html.unescape(value)
    if _HTML_LIKE.search(value):
        return html_to_text(value, links)
    return _normalize_wrappers(_replace_urls(_replace_markdown(value, links), links))


def _normalize_wrappers(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        label = re.sub(r"\s+", " ", match.group(1)).strip()
        refs = " ".join(re.findall(r"\[link\d+\]", match.group(2)))
        return f"{label} {refs}".strip()

    return _CROSS_REF.sub(replace, _MULTI_REF.sub(replace, value))


def _source_label(label: str, url: str) -> str | None:
    if not label or label == url or re.match(r"(?i)^https?://", label):
        return None
    return label


def include_in_llm_link_appendix(link: EmailSourceLink) -> bool:
    if link.label is None:
        return False
    label = re.sub(r"\s+", " ", link.label).strip()
    if _FOOTER_LABEL.fullmatch(label):
        return False
    parsed = urlparse(link.url)
    host = parsed.hostname.lower() if parsed.hostname else ""
    path = parsed.path.rstrip("/")
    if host in {"itunes.apple.com", "play.google.com"}:
        return False
    if host.startswith(("help.", "policy.")) or _FOOTER_PATH.search(path):
        return False
    segments = [segment for segment in path.split("/") if segment]
    if any(segment.startswith("@") for segment in segments):
        return False
    if re.search(r"(?i)/(?:author|profile|user)/", f"/{path.lstrip('/')}/"):
        return False
    return not ((host == "medium.com" or host.endswith(".medium.com")) and len(segments) <= 1)


def _anchor_label(value: str) -> str:
    visible = re.sub(r"(?s)<[^>]+>", " ", value)
    visible = re.sub(r"\s+", " ", html.unescape(visible)).strip()
    if visible:
        return visible
    alt_values = re.findall(r"(?is)<img\b[^>]*?\balt\s*=\s*(['\"])(.*?)\1", value)
    return re.sub(r"\s+", " ", " ".join(html.unescape(alt) for _, alt in alt_values)).strip()


def _anchor_to_text(match: re.Match[str], links: LinkCollector) -> str:
    url = html.unescape(match.group(2)).strip()
    label = _anchor_label(match.group(3))
    if not url.startswith(("https://", "http://")):
        return label or url
    source_label = _source_label(label, url)
    ref = links.add(url, source_label)
    return f" {source_label} [{ref}] " if source_label else " "


def html_to_text(value: str, links: LinkCollector | None = None) -> str:
    collector = links or LinkCollector()
    result = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    result = re.sub(r"(?is)<!--.*?-->", "\n", result)
    result = re.sub(
        r"(?is)<a\b[^>]*?href\s*=\s*(['\"])(.*?)\1[^>]*>(.*?)</a>",
        lambda match: _anchor_to_text(match, collector),
        result,
    )
    result = re.sub(
        r"(?is)<\s*/?\s*(?:address|article|aside|blockquote|br|div|dl|dt|dd|fieldset|figcaption|figure|footer|form|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        result,
    )
    result = re.sub(r"(?s)<[^>]+>", " ", result)
    result = _normalize_wrappers(
        _replace_urls(_replace_markdown(html.unescape(result), collector), collector)
    )
    lines = (re.sub(r"[^\S\r\n]+", " ", line).strip() for line in result.splitlines())
    return "\n".join(line for line in lines if line)


def extract_html_links(value: str, links: LinkCollector) -> list[tuple[str, str]]:
    pattern = r"(?is)<a\b[^>]*?href\s*=\s*(['\"])(.*?)\1[^>]*>(.*?)</a>"
    result = []
    for match in re.finditer(pattern, value):
        url = html.unescape(match.group(2)).strip()
        if url.startswith(("https://", "http://")):
            result.append((url, _anchor_to_text(match, links).strip()))
    return result


def strip_suspicious_format_controls(value: str) -> str:
    characters = list(value)
    cleaned: list[str] = []
    for index, character in enumerate(characters):
        if character in _CONTROLS:
            continue
        if character not in {"\u200c", "\u200d"}:
            cleaned.append(character)
            continue
        left = _nearest(characters, index, -1)
        right = _nearest(characters, index, 1)
        if left is None or right is None:
            continue
        if character == "\u200c" and _is_joining(left) and _is_joining(right):
            cleaned.append(character)
        elif character == "\u200d" and (
            (_is_emoji(left) and _is_emoji(right))
            or (_is_joining(left) and _is_joining(right))
        ):
            cleaned.append(character)
    return "".join(cleaned)


def remove_separator_lines(value: str) -> str:
    return "\n".join(line for line in value.splitlines() if not _SEPARATOR.fullmatch(line))


def _nearest(characters: list[str], start: int, direction: int) -> str | None:
    index = start + direction
    while 0 <= index < len(characters):
        if characters[index] not in {"\ufe0e", "\ufe0f"}:
            return characters[index]
        index += direction
    return None


def _is_emoji(character: str) -> bool:
    codepoint = ord(character)
    return 0x1F000 <= codepoint <= 0x1FAFF or 0x2600 <= codepoint <= 0x27BF


def _is_joining(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x0600 <= codepoint <= 0x08FF
        or 0x0900 <= codepoint <= 0x0D7F
        or 0xFB50 <= codepoint <= 0xFEFF
    )
