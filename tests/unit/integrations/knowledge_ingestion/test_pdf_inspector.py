from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from cowork_agent.integrations.knowledge_ingestion.models import PdfKind
from cowork_agent.integrations.knowledge_ingestion.pdf_inspector import PdfInspector


class FakeRunner:
    def __init__(self, responses: dict[tuple[str, ...], str]) -> None:
        self.responses = responses
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command: Sequence[str]) -> str:
        key = tuple(command)
        self.commands.append(key)
        return self.responses[key]


def test_inspector_maps_mixed_pages_and_native_markdown(tmp_path: Path) -> None:
    """Dropping the classifier's OCR page list would OCR the wrong PDF pages."""
    path = tmp_path / "policy.pdf"
    runner = FakeRunner(
        {
            ("detect-pdf", "--json", str(path)): (
                '{"pdf_type":"mixed","page_count":3,"pages_needing_ocr":[2]}'
            ),
            ("pdf2md", str(path), "--json", "--pages", "--select-pages", "1,3"): (
                '{"markdown":"<!-- Page 1 -->\\n# One\\n<!-- Page 3 -->\\n# Three"}'
            ),
        }
    )

    inspection = PdfInspector(runner).inspect(path)

    assert inspection.kind is PdfKind.MIXED
    assert inspection.page_count == 3
    assert inspection.pages_needing_ocr == (2,)
    assert dict(inspection.native_markdown_by_page) == {1: "# One", 3: "# Three"}
    assert runner.commands == [
        ("detect-pdf", "--json", str(path)),
        ("pdf2md", str(path), "--json", "--pages", "--select-pages", "1,3"),
    ]


@pytest.mark.parametrize(
    "detect_output",
    [
        "not-json",
        '{"pdf_type":"mixed","page_count":2,"pages_needing_ocr":[3]}',
        '{"pdf_type":"text_based","page_count":0,"pages_needing_ocr":[]}',
    ],
)
def test_inspector_rejects_invalid_detection_without_leaking_command_output(
    tmp_path: Path, detect_output: str
) -> None:
    """Including tool output in failures could expose document content in logs."""
    path = tmp_path / "policy.pdf"
    runner = FakeRunner({("detect-pdf", "--json", str(path)): f"secret: {detect_output}"})

    with pytest.raises(ValueError) as error:
        PdfInspector(runner).inspect(path)

    assert "secret:" not in str(error.value)
