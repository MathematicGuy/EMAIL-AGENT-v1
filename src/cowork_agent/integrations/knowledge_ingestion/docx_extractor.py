"""Local, structure-preserving DOCX-to-Markdown extraction."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from docx import Document
from docx.document import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph

from cowork_agent.integrations.rag.structure_profile import DEFAULT_PROFILE, StructureProfile

from .models import ExtractionResult

#: A Word Heading style states the author's own depth, so it wins over the
#: profile's rule table rather than being re-derived from the text.
_HEADING_STYLE_LEVELS = {"Heading 1": 1, "Heading 2": 2, "Heading 3": 3}


class DocxExtractor:
    """Extract DOCX body blocks in their original order."""

    def __init__(self, profile: StructureProfile = DEFAULT_PROFILE) -> None:
        self._profile = profile

    def extract(self, path: Path) -> ExtractionResult:
        document = Document(str(path))
        blocks = [_block_to_markdown(block, self._profile) for block in _iter_blocks(document)]
        markdown = "\n\n".join(block for block in blocks if block)
        return ExtractionResult(markdown=markdown, page_count=1)


def _iter_blocks(document: DocxDocument) -> Iterator[Paragraph | Table]:
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, document)
        elif child.tag.endswith("}tbl"):
            yield Table(child, document)


def _block_to_markdown(block: Paragraph | Table, profile: StructureProfile) -> str:
    if isinstance(block, Table):
        return _table_to_markdown(block)
    text = block.text.strip()
    if not text:
        return ""
    style = block.style
    style_name = style.name if style is not None else ""
    level = _HEADING_STYLE_LEVELS.get(style_name)
    if level is None:
        if style_name.startswith("List Bullet"):
            return f"- {text}"
        if style_name.startswith("List Number"):
            return f"1. {text}"
        level = _emphasised_heading_level(block, text, profile)
    return f"{'#' * level} {text}" if level else text


def _emphasised_heading_level(block: Paragraph, text: str, profile: StructureProfile) -> int | None:
    """Read a fully bold, short paragraph as a heading.

    Vietnamese legal DOCX files style every paragraph ``Normal`` and mark
    structure with bold alone. Extraction is the last stage that can see that
    formatting, so discarding it here destroys the document's structure for
    good. Requiring *every* run to be bold is what separates a heading from a
    sentence that merely contains a bold phrase.
    """
    if not _is_fully_bold(block):
        return None
    return profile.formatted_heading_level(text)


def _is_fully_bold(block: Paragraph) -> bool:
    """True when every run carrying text is bold.

    Bails on the first non-bold run: ``block.runs`` builds a fresh object per
    run, so prose — the overwhelmingly common case — should not pay for the
    whole list.
    """
    seen = False
    for run in block.runs:
        if not run.text.strip():
            continue
        if not run.bold:
            return False
        seen = True
    return seen


def _table_to_markdown(table: Table) -> str:
    rows = [[_escape_cell(cell.text.strip()) for cell in row.cells] for row in table.rows]
    if not rows:
        return ""
    header = rows[0]
    lines = [f"| {' | '.join(header)} |", f"| {' | '.join('---' for _ in header)} |"]
    lines.extend(f"| {' | '.join(row)} |" for row in rows[1:])
    return "\n".join(lines)


def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")
