"""Unicode-safe PDF rendering for the report artifact port."""

from __future__ import annotations

import html
import re
from pathlib import Path

from fpdf import FPDF

from cowork_agent.domain.report_artifacts import StoredReport

_FONT_FAMILY = "Noto Sans"
_FONT_STYLES = {
    "": "NotoSans-Regular.ttf",
    "B": "NotoSans-Bold.ttf",
    "I": "NotoSans-Italic.ttf",
    "BI": "NotoSans-BoldItalic.ttf",
}

_FENCE_RE = re.compile(r"^\s*```(?:[^`]*)$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_UNORDERED_ITEM_RE = re.compile(r"^\s*[-+*]\s+(.+?)\s*$")
_ORDERED_ITEM_RE = re.compile(r"^\s*\d+[.)]\s+(.+?)\s*$")
_LINK_RE = re.compile(r"\[([^\]\n]+)]\(([^)\n]+)\)")
_STRONG_RE = re.compile(r"\*\*(.+?)\*\*")
_EMPHASIS_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")


class Fpdf2ReportPdfRenderer:
    """Render the report Markdown subset through fpdf2 and bundled Noto Sans."""

    def __init__(self, *, font_directory: Path | None = None) -> None:
        self._font_directory = font_directory or Path(__file__).with_name("fonts")

    def render(self, report: StoredReport, *, title: str | None = None) -> bytes:
        pdf = FPDF(format="A4")
        pdf.set_margins(18, 18, 18)
        pdf.set_auto_page_break(auto=True, margin=18)
        self._register_fonts(pdf)
        pdf.add_page()
        pdf.set_font(_FONT_FAMILY, size=11)

        document = _markdown_to_html(report.content)
        if title:
            document = f"<h1>{_inline_markup(title)}</h1>{document}"
        pdf.write_html(document)
        return bytes(pdf.output())

    def _register_fonts(self, pdf: FPDF) -> None:
        for style, filename in _FONT_STYLES.items():
            pdf.add_font(_FONT_FAMILY, style=style, fname=self._font_directory / filename)


def _inline_markup(text: str) -> str:
    """Escape source HTML, then admit only the supported inline Markdown."""
    escaped = html.escape(text, quote=True)
    escaped = _LINK_RE.sub(
        lambda match: (
            f"{match.group(1)} ({match.group(2)})"
        ),
        escaped,
    )
    escaped = _INLINE_CODE_RE.sub(r"<code>\1</code>", escaped)
    escaped = _STRONG_RE.sub(r"<strong>\1</strong>", escaped)
    return _EMPHASIS_RE.sub(r"<em>\1</em>", escaped)


def _markdown_to_html(markdown: str) -> str:
    """Translate the small report Markdown contract into safe fpdf2 HTML."""
    blocks: list[str] = []
    paragraph: list[str] = []
    list_kind: str | None = None
    list_items: list[str] = []
    code_lines: list[str] = []
    in_code = False

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(f"<p>{_inline_markup(' '.join(paragraph))}</p>")
            paragraph.clear()

    def flush_list() -> None:
        nonlocal list_kind
        if list_kind is not None:
            items = "".join(f"<li>{_inline_markup(item)}</li>" for item in list_items)
            blocks.append(f"<{list_kind}>{items}</{list_kind}>")
            list_items.clear()
            list_kind = None

    def append_list_item(kind: str, item: str) -> None:
        nonlocal list_kind
        flush_paragraph()
        if list_kind != kind:
            flush_list()
            list_kind = kind
        list_items.append(item)

    for line in markdown.splitlines():
        if _FENCE_RE.match(line):
            if in_code:
                blocks.append(
                    f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>"
                )
                code_lines.clear()
                in_code = False
            else:
                flush_paragraph()
                flush_list()
                in_code = True
            continue

        if in_code:
            code_lines.append(line)
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            flush_paragraph()
            flush_list()
            level = len(heading.group(1))
            blocks.append(f"<h{level}>{_inline_markup(heading.group(2))}</h{level}>")
            continue

        unordered = _UNORDERED_ITEM_RE.match(line)
        if unordered:
            append_list_item("ul", unordered.group(1))
            continue

        ordered = _ORDERED_ITEM_RE.match(line)
        if ordered:
            append_list_item("ol", ordered.group(1))
            continue

        if not line.strip():
            flush_paragraph()
            flush_list()
            continue

        flush_list()
        paragraph.append(line.strip())

    if in_code:
        blocks.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
    flush_paragraph()
    flush_list()
    return "".join(blocks) or "<p></p>"
