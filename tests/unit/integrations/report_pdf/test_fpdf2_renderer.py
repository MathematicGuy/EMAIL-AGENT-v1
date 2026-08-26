from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO

from pypdf import PdfReader

from cowork_agent.domain.report_artifacts import ReportFilename, StoredReport
from cowork_agent.integrations.report_pdf import Fpdf2ReportPdfRenderer


def _report(content: str) -> StoredReport:
    return StoredReport(
        filename=ReportFilename("bao-cao.md"),
        content=content,
        size=len(content.encode("utf-8")),
        updated_at=datetime(2026, 8, 26, tzinfo=UTC),
    )


def _text(pdf: bytes) -> str:
    reader = PdfReader(BytesIO(pdf))
    return "\n".join(page.extract_text() for page in reader.pages)


def test_render_returns_a_pdf_with_extractable_vietnamese_text() -> None:
    pdf = Fpdf2ReportPdfRenderer().render(
        _report("# Báo cáo tiến độ\n\nNội dung tiếng Việt có dấu."),
    )

    assert pdf.startswith(b"%PDF-")
    assert "Báo cáo tiến độ" in _text(pdf)
    assert "Nội dung tiếng Việt có dấu." in _text(pdf)


def test_render_preserves_the_supported_markdown_as_readable_text() -> None:
    report = _report(
        """## Kết quả

Đây là **nội dung đậm** và *nội dung nghiêng*.

- Mục một
- Mục hai

1. Bước đầu
2. Bước tiếp

[Tài liệu](https://example.com/huong-dan)

```python
print("đường dẫn dữ liệu")
```

<widget>vẫn đọc được</widget>
"""
    )

    extracted = _text(Fpdf2ReportPdfRenderer().render(report, title="Bản xuất PDF"))

    for expected in (
        "Bản xuất PDF",
        "Kết quả",
        "nội dung đậm",
        "nội dung nghiêng",
        "Mục một",
        "Mục hai",
        "Bước đầu",
        "Bước tiếp",
        "Tài liệu (https://example.com/huong-dan)",
        'print("đường dẫn dữ liệu")',
        "<widget>vẫn đọc được</widget>",
    ):
        assert expected in extracted
