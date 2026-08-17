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

from .markdown_syntax import ATX_PREFIX, FENCE, TABLE_ROW, normalize_newlines
from .structure_profile import DEFAULT_PROFILE, StructureProfile

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
    return "\n".join(normalize_lines(normalize_newlines(markdown).split("\n"), profile))


def normalize_lines(lines: list[str], profile: StructureProfile) -> list[str]:
    """Promote structural headings in already-split lines.

    Split out so a caller holding lines does not pay to join them and split
    them again.
    """
    # One strip per line up front: a line is otherwise stripped again as its
    # predecessor's successor and as its successor's predecessor.
    stripped = [line.strip() for line in lines]
    output: list[str] = []
    index = 0
    in_fence = False
    while index < len(lines):
        consumed = 1
        line = lines[index]
        if FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence and _is_promotable(lines, stripped, index):
            level = profile.heading_level(stripped[index])
            if level is not None:
                title, consumed = _with_adopted_title(
                    lines, stripped, index, stripped[index], profile
                )
                line = f"{'#' * level} {title}"
        output.append(line)
        index += consumed
    return output


def _is_promotable(lines: list[str], stripped: list[str], index: int) -> bool:
    """True when the line stands alone as its own block of plain text."""
    if not stripped[index] or ATX_PREFIX.match(lines[index]) or TABLE_ROW.match(lines[index]):
        return False
    before_blank = index == 0 or not stripped[index - 1]
    after_blank = index == len(lines) - 1 or not stripped[index + 1]
    return before_blank and after_blank


def _with_adopted_title(
    lines: list[str],
    stripped: list[str],
    index: int,
    heading: str,
    profile: StructureProfile,
) -> tuple[str, int]:
    """Fold a division's title into its heading, returning the lines consumed.

    ``Chương I`` followed by ``QUY ĐỊNH CHUNG`` is one heading split across two
    lines. Left apart, the breadcrumb would read ``Chương I`` and say nothing.
    """
    if not profile.is_titleless(heading):
        return heading, 1
    candidate = index + 1
    while candidate < len(lines) and not stripped[candidate]:
        candidate += 1
    if candidate >= len(lines) or not _is_promotable(lines, stripped, candidate):
        return heading, 1
    title = stripped[candidate]
    if (
        len(title) > profile.max_title_chars
        or profile.heading_level(title) is not None
        or not _HAS_LETTER.search(title)
        or title != title.upper()
    ):
        return heading, 1
    return f"{heading}{_TITLE_JOIN}{title}", candidate - index + 1
