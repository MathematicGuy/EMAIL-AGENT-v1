"""Local, structure-preserving DOCX-to-Markdown extraction."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from docx import Document
from docx.document import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph

from .models import ExtractionResult


class DocxExtractor:
    """Extract DOCX body blocks in their original order."""

    def extract(self, path: Path) -> ExtractionResult:
        document = Document(str(path))
        blocks = [_block_to_markdown(block) for block in _iter_blocks(document)]
        markdown = "\n\n".join(block for block in blocks if block)
        return ExtractionResult(markdown=markdown, page_count=1)


def _iter_blocks(document: DocxDocument) -> Iterator[Paragraph | Table]:
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, document)
        elif child.tag.endswith("}tbl"):
            yield Table(child, document)


def _block_to_markdown(block: Paragraph | Table) -> str:
    if isinstance(block, Table):
        return _table_to_markdown(block)
    text = block.text.strip()
    if not text:
        return ""
    style = block.style
    style_name = style.name if style is not None else ""
    heading_prefixes = {"Heading 1": "#", "Heading 2": "##", "Heading 3": "###"}
    if prefix := heading_prefixes.get(style_name):
        return f"{prefix} {text}"
    if style_name.startswith("List Bullet"):
        return f"- {text}"
    if style_name.startswith("List Number"):
        return f"1. {text}"
    return text


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
