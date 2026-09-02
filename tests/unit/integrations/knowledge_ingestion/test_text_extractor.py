from __future__ import annotations

from pathlib import Path

import pytest

from cowork_agent.integrations.knowledge_ingestion.text_extractor import TextExtractor


def test_utf8_txt_returns_body_and_page_count_one(tmp_path: Path) -> None:
    path = tmp_path / "note.txt"
    path.write_text("Hello world\nSecond line", encoding="utf-8")

    result = TextExtractor().extract(path)

    assert result.markdown == "Hello world\nSecond line"
    assert result.page_count == 1


def test_md_with_leading_closed_frontmatter_returns_only_body(tmp_path: Path) -> None:
    path = tmp_path / "policy.md"
    path.write_text(
        "---\ndocument_id: policy-file\ntitle: Policy\n---\n\n# Policy\n",
        encoding="utf-8",
    )

    result = TextExtractor().extract(path)

    assert "document_id:" not in result.markdown
    assert result.markdown == "# Policy\n"


def test_page_markers_set_page_count_to_max_n(tmp_path: Path) -> None:
    path = tmp_path / "paged.txt"
    path.write_text("<!-- Page 1 -->\nAlpha\n\n<!-- Page 2 -->\nBeta\n", encoding="utf-8")

    result = TextExtractor().extract(path)

    assert result.page_count == 2
    assert "<!-- Page 1 -->" in result.markdown
    assert "<!-- Page 2 -->" in result.markdown


def test_invalid_bytes_raise_decode_failed(tmp_path: Path) -> None:
    path = tmp_path / "bad.txt"
    path.write_bytes(b"\xff\xfe not utf-8")

    with pytest.raises(ValueError, match="decode_failed"):
        TextExtractor().extract(path)


def test_empty_file_raises_empty_extraction(tmp_path: Path) -> None:
    path = tmp_path / "empty.txt"
    path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="empty_extraction"):
        TextExtractor().extract(path)
