"""Shared deterministic Markdown chunking for knowledge and project RAG."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

DEFAULT_MAX_CHARS = 1_200

_HEADING = re.compile(r"^#{1,2}\s+(.+?)\s*$")
_PAGE_MARKER = re.compile(r"^<!--\s*Page\s+(\d+)\s*-->\s*$")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True, slots=True)
class MarkdownPage:
    """One Markdown fragment with an optional one-based source page."""

    markdown: str
    page_number: int | None = None


@dataclass(frozen=True, slots=True)
class MarkdownChunk:
    """One section-aware chunk and its inclusive page coordinates."""

    text: str
    section: str | None
    page_start: int | None
    page_end: int | None


@dataclass(frozen=True, slots=True)
class _Paragraph:
    text: str
    page_number: int | None


def split_markdown_pages(markdown: str) -> tuple[MarkdownPage, ...]:
    """Split Markdown on `<!-- Page N -->` markers into page fragments."""

    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if not any(_PAGE_MARKER.match(line) for line in lines):
        return (MarkdownPage(markdown=normalized, page_number=None),)

    pages: list[MarkdownPage] = []
    current_number: int | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_lines
        body = "\n".join(current_lines)
        if current_number is None and not body:
            current_lines = []
            return
        pages.append(MarkdownPage(markdown=body, page_number=current_number))
        current_lines = []

    for line in lines:
        match = _PAGE_MARKER.match(line)
        if match is not None:
            flush()
            current_number = int(match.group(1))
            continue
        current_lines.append(line)
    flush()
    return tuple(pages)


def chunk_markdown(
    markdown: str, *, max_chars: int = DEFAULT_MAX_CHARS
) -> tuple[MarkdownChunk, ...]:
    """Chunk one Markdown document by H1/H2 section and paragraph."""

    return chunk_markdown_pages((MarkdownPage(markdown),), max_chars=max_chars)


def chunk_markdown_pages(
    pages: Iterable[MarkdownPage], *, max_chars: int = DEFAULT_MAX_CHARS
) -> tuple[MarkdownChunk, ...]:
    """Chunk ordered page fragments without mixing distinct sections."""

    if max_chars < 200:
        raise ValueError("max_chars must be at least 200")
    chunks: list[MarkdownChunk] = []
    section: str | None = None
    paragraphs: list[_Paragraph] = []
    raw_section_lines: list[str] = []

    def flush_section() -> None:
        nonlocal paragraphs, raw_section_lines
        if paragraphs:
            chunks.extend(
                _pack_paragraphs(
                    paragraphs,
                    section,
                    max_chars,
                    preserved_text="\n".join(raw_section_lines).strip(),
                )
            )
            paragraphs = []
        raw_section_lines = []

    for page in pages:
        if page.page_number is not None and page.page_number < 1:
            raise ValueError("page numbers must be positive")
        normalized = page.markdown.replace("\r\n", "\n").replace("\r", "\n")
        page_number = page.page_number
        pending: list[str] = []

        def flush_paragraph(source_page: int | None = page_number) -> None:
            nonlocal pending
            text = "\n".join(pending).strip()
            if text:
                paragraphs.append(_Paragraph(text=text, page_number=source_page))
            pending = []

        for raw_line in normalized.split("\n"):
            line = raw_line
            heading = _HEADING.match(line)
            if heading is not None:
                flush_paragraph()
                flush_section()
                section = heading.group(1).strip()
            elif line.strip():
                # Preserve meaningful Markdown indentation (nested lists,
                # code, tables). Paragraph-level trim still removes only
                # incidental leading/trailing whitespace, matching the
                # established Email/Knowledge corpus output.
                pending.append(line)
                raw_section_lines.append(line)
            else:
                flush_paragraph()
                raw_section_lines.append(line)
        flush_paragraph()
        raw_section_lines.append("")
    flush_section()
    return tuple(chunks)


def _pack_paragraphs(
    paragraphs: list[_Paragraph],
    section: str | None,
    max_chars: int,
    *,
    preserved_text: str,
) -> list[MarkdownChunk]:
    if preserved_text and len(preserved_text) <= max_chars:
        coordinates = _build_chunk(paragraphs, section)
        return [
            MarkdownChunk(
                text=preserved_text,
                section=section,
                page_start=coordinates.page_start,
                page_end=coordinates.page_end,
            )
        ]
    expanded = [
        _Paragraph(part, paragraph.page_number)
        for paragraph in paragraphs
        for part in _split_oversize(paragraph.text, max_chars)
    ]
    chunks: list[MarkdownChunk] = []
    current: list[_Paragraph] = []
    current_length = 0
    for paragraph in expanded:
        separator = 2 if current else 0
        if current and current_length + separator + len(paragraph.text) > max_chars:
            chunks.append(_build_chunk(current, section))
            current = []
            current_length = 0
            separator = 0
        current.append(paragraph)
        current_length += separator + len(paragraph.text)
    if current:
        chunks.append(_build_chunk(current, section))
    return chunks


def _split_oversize(text: str, max_chars: int) -> tuple[str, ...]:
    if len(text) <= max_chars:
        return (text,)
    sentences = [value.strip() for value in _SENTENCE_BOUNDARY.split(text) if value.strip()]
    if len(sentences) > 1:
        parts: list[str] = []
        current = ""
        for sentence in sentences:
            if len(sentence) > max_chars:
                if current:
                    parts.append(current)
                    current = ""
                parts.extend(_hard_split(sentence, max_chars))
            elif current and len(current) + len(sentence) + 1 > max_chars:
                parts.append(current)
                current = sentence
            else:
                current = f"{current} {sentence}" if current else sentence
        if current:
            parts.append(current)
        return tuple(parts)
    return tuple(_hard_split(text, max_chars))


def _hard_split(text: str, max_chars: int) -> list[str]:
    parts: list[str] = []
    remaining = text.strip()
    while len(remaining) > max_chars:
        boundary = max(
            remaining.rfind("\n", 0, max_chars + 1),
            remaining.rfind(" ", max_chars // 2, max_chars + 1),
        )
        if boundary < max_chars // 2:
            boundary = max_chars
        part = remaining[:boundary].strip()
        if part:
            parts.append(part)
        remaining = remaining[boundary:].strip()
    if remaining:
        parts.append(remaining)
    return parts


def _build_chunk(paragraphs: list[_Paragraph], section: str | None) -> MarkdownChunk:
    page_numbers = [item.page_number for item in paragraphs if item.page_number is not None]
    return MarkdownChunk(
        text="\n\n".join(item.text for item in paragraphs),
        section=section,
        page_start=min(page_numbers) if page_numbers else None,
        page_end=max(page_numbers) if page_numbers else None,
    )
