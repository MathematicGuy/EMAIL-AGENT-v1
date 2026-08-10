from __future__ import annotations

from pathlib import Path

from docx import Document

from cowork_agent.integrations.knowledge_ingestion.docx_extractor import DocxExtractor


def test_docx_extractor_preserves_headings_lists_and_tables_in_document_order(
    tmp_path: Path,
) -> None:
    """Removing structural conversions would lose navigable document meaning."""
    document = Document()
    document.add_heading("Quy định", level=1)
    document.add_paragraph("Nội dung mở đầu")
    document.add_heading("Hồ sơ", level=2)
    document.add_paragraph("Giấy tờ", style="List Bullet")
    document.add_heading("Chi tiết", level=3)
    table = document.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "Giấy tờ"
    table.rows[0].cells[1].text = "Bắt buộc"
    table.rows[1].cells[0].text = "A|B"
    table.rows[1].cells[1].text = "Có"
    path = tmp_path / "policy.docx"
    document.save(path)

    result = DocxExtractor().extract(path)

    assert result.markdown == (
        "# Quy định\n\n"
        "Nội dung mở đầu\n\n"
        "## Hồ sơ\n\n"
        "- Giấy tờ\n\n"
        "### Chi tiết\n\n"
        "| Giấy tờ | Bắt buộc |\n"
        "| --- | --- |\n"
        "| A\\|B | Có |"
    )
    assert result.page_count == 1
