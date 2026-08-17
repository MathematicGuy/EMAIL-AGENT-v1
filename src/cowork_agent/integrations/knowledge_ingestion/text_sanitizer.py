"""NFC body cleanup and closed-field Markdown frontmatter emit/parse."""

from __future__ import annotations

import re
import unicodedata

FRONTMATTER_KEYS = (
    "document_id",
    "title",
    "source_file",
    "extractor",
    "page_count",
    "processed_at",
)

_KEEP_CONTROLS = frozenset("\n\t")
_ATX_H1_PREFIX = "# "


def sanitize_text(text: str) -> str:
    """NFC-normalize body text without destroying Markdown structure."""
    text = unicodedata.normalize("NFC", text)
    text = "".join(
        ch for ch in text if not unicodedata.category(ch).startswith("C") or ch in _KEEP_CONTROLS
    )
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def build_frontmatter(
    *,
    document_id: str,
    title: str,
    source_file: str,
    extractor: str,
    page_count: int,
    processed_at: str,
) -> str:
    """Emit the closed key set with a trailing fence ready to prefix a body."""
    values = {
        "document_id": document_id,
        "title": title,
        "source_file": source_file,
        "extractor": extractor,
        "page_count": str(page_count),
        "processed_at": processed_at,
    }
    lines = ["---"]
    for key in FRONTMATTER_KEYS:
        lines.append(f"{key}: {_format_frontmatter_value(values[key])}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def split_frontmatter(markdown: str) -> tuple[dict[str, str], str]:
    """Parse a leading closed frontmatter block; ignore unknown keys."""
    if not markdown.startswith("---\n"):
        return {}, markdown
    header, separator, remainder = markdown[4:].partition("\n---\n")
    if not separator:
        if markdown[4:].endswith("\n---"):
            header = markdown[4:-4]
            remainder = ""
        else:
            return {}, markdown
    fields: dict[str, str] = {}
    for line in header.split("\n"):
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        key = key.strip()
        if key not in FRONTMATTER_KEYS:
            continue
        fields[key] = _parse_frontmatter_value(raw.strip())
    return fields, remainder.removeprefix("\n")


def resolve_title(body: str, fallback: str) -> str:
    """Return the first ATX H1, or fallback when the body has none."""
    for line in body.splitlines():
        if line.startswith(_ATX_H1_PREFIX) and not line.startswith("##"):
            title = line[len(_ATX_H1_PREFIX) :].strip()
            return title or fallback
    return fallback


def _format_frontmatter_value(value: str) -> str:
    if ":" in value or value != value.strip():
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _parse_frontmatter_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        inner = value[1:-1]
        if value[0] == '"':
            return inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner
    return value
