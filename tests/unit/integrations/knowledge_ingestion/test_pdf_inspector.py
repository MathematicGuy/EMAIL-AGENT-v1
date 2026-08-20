from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cowork_agent.integrations.knowledge_ingestion.models import PdfKind
from cowork_agent.integrations.knowledge_ingestion.pdf_inspector import PdfInspector

pytestmark = pytest.mark.extended


def test_inspector_maps_mixed_pages_and_native_markdown(tmp_path: Path) -> None:
    path = tmp_path / "policy.pdf"
    inspected_paths: list[str] = []
    extracted_pages: list[list[int] | None] = []

    def detector(value: str) -> SimpleNamespace:
        inspected_paths.append(value)
        return SimpleNamespace(pdf_type="mixed", page_count=3, pages_needing_ocr=[2])

    def extractor(value: str, pages: list[int] | None) -> SimpleNamespace:
        assert value == str(path)
        extracted_pages.append(pages)
        return SimpleNamespace(
            pages=[
                SimpleNamespace(page=0, markdown="# One", needs_ocr=False),
                SimpleNamespace(page=2, markdown="# Three", needs_ocr=False),
            ]
        )

    inspection = PdfInspector(detector, extractor).inspect(path)

    assert inspection.kind is PdfKind.MIXED
    assert inspection.page_count == 3
    assert inspection.pages_needing_ocr == (2,)
    assert dict(inspection.native_markdown_by_page) == {1: "# One", 3: "# Three"}
    assert inspected_paths == [str(path)]
    assert extracted_pages == [[0, 2]]


@pytest.mark.parametrize(
    "detection",
    [
        SimpleNamespace(pdf_type="unknown", page_count=2, pages_needing_ocr=[]),
        SimpleNamespace(pdf_type="mixed", page_count=0, pages_needing_ocr=[]),
        SimpleNamespace(pdf_type="mixed", page_count=2, pages_needing_ocr=[3]),
    ],
)
def test_inspector_rejects_invalid_detection(tmp_path: Path, detection: object) -> None:
    with pytest.raises(ValueError, match="PDF inspection output is invalid"):
        PdfInspector(lambda _path: detection, lambda _path, _pages: None).inspect(
            tmp_path / "policy.pdf"
        )


def test_inspector_rejects_invalid_markdown_response(tmp_path: Path) -> None:
    def detector(_path: str) -> SimpleNamespace:
        return SimpleNamespace(pdf_type="text_based", page_count=1, pages_needing_ocr=[])

    def extractor(_path: str, _pages: list[int] | None) -> SimpleNamespace:
        return SimpleNamespace(pages=[SimpleNamespace(page=0, markdown="", needs_ocr=False)])

    with pytest.raises(ValueError, match="PDF Markdown output is invalid"):
        PdfInspector(detector, extractor).inspect(tmp_path / "policy.pdf")
