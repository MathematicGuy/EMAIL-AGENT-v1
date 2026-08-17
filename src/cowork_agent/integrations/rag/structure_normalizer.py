"""Recover document structure that extraction could not preserve.

PDF and OCR pipelines return Markdown with the typography already flattened,
and uploaded project documents are never persisted as Markdown at all, so the
text itself is the only remaining evidence of how a document is organised.
Promoting plain-text headings to ATX here means the chunker only ever has to
understand one representation.

Promotion is idempotent: Markdown that already carries ATX headings, and
Markdown that has been normalized once, both pass through unchanged.
"""

from __future__ import annotations

import re
from typing import Final

from .structure_profile import DEFAULT_PROFILE, StructureProfile

_FENCE: Final = re.compile(r"^\s*(?:```|~~~)")
_ATX: Final = re.compile(r"^\s*#{1,6}\s")
_TABLE_ROW: Final = re.compile(r"^\s*\|")
_HAS_LETTER: Final = re.compile(r"[^\W\d_]", re.UNICODE)
_TITLE_JOIN: Final = " — "


def normalize_structure(
    markdown: str, *, profile: StructureProfile = DEFAULT_PROFILE
) -> str:
    """Rewrite plain-text structural headings in ``markdown`` as ATX headings.

    Only standalone lines are considered — a line surrounded by blank lines or
    document edges. Everything else is emitted byte for byte, so prose, tables,
    fenced code and deliberate indentation survive untouched.
    """
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    output: list[str] = []
    index = 0
    in_fence = False
    while index < len(lines):
        line = lines[index]
        if _FENCE.match(line):
            in_fence = not in_fence
            output.append(line)
            index += 1
            continue
        if in_fence or not _is_promotable(lines, index):
            output.append(line)
            index += 1
            continue
        stripped = line.strip()
        level = profile.heading_level(stripped)
        if level is None:
            output.append(line)
            index += 1
            continue
        title, consumed = _with_adopted_title(lines, index, stripped, profile)
        output.append(f"{'#' * level} {title}")
        index += consumed
    return "\n".join(output)


def _is_promotable(lines: list[str], index: int) -> bool:
    """True when the line stands alone as its own block of plain text."""
    line = lines[index]
    if not line.strip() or _ATX.match(line) or _TABLE_ROW.match(line):
        return False
    before_blank = index == 0 or not lines[index - 1].strip()
    after_blank = index == len(lines) - 1 or not lines[index + 1].strip()
    return before_blank and after_blank


def _with_adopted_title(
    lines: list[str], index: int, heading: str, profile: StructureProfile
) -> tuple[str, int]:
    """Fold a division's title into its heading, returning the lines consumed.

    ``Chương I`` followed by ``QUY ĐỊNH CHUNG`` is one heading split across two
    lines. Left apart, the breadcrumb would read ``Chương I`` and say nothing.
    """
    if not profile.is_titleless(heading):
        return heading, 1
    candidate = index + 1
    while candidate < len(lines) and not lines[candidate].strip():
        candidate += 1
    if candidate >= len(lines) or not _is_promotable(lines, candidate):
        return heading, 1
    title = lines[candidate].strip()
    if (
        len(title) > profile.max_title_chars
        or profile.heading_level(title) is not None
        or not _HAS_LETTER.search(title)
        or title != title.upper()
    ):
        return heading, 1
    return f"{heading}{_TITLE_JOIN}{title}", candidate - index + 1
