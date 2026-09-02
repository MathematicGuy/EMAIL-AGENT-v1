"""UTF-8 .txt/.md extraction; persist path owns sanitize and frontmatter wrap."""

from __future__ import annotations

import re
from pathlib import Path

from .models import ExtractionResult
from .text_sanitizer import split_frontmatter

_PAGE_MARKER = re.compile(r"^<!--\s*Page\s+(\d+)\s*-->\s*$")


def page_count_from_markers(text: str) -> int:
    """Return max `<!-- Page N -->` marker, or 1 when the body has none."""
    numbers = [
        int(match.group(1))
        for line in text.splitlines()
        if (match := _PAGE_MARKER.match(line)) is not None
    ]
    return max(numbers) if numbers else 1


class TextExtractor:
    """Read a local UTF-8 text or Markdown file into an unsanitized body."""

    def extract(self, path: Path) -> ExtractionResult:
        try:
            raw = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("decode_failed") from exc

        if path.suffix.lower() == ".md":
            _, body = split_frontmatter(raw)
        else:
            body = raw

        if not body.strip():
            raise ValueError("empty_extraction")

        return ExtractionResult(markdown=body, page_count=page_count_from_markers(body))
