from __future__ import annotations

import unicodedata

from cowork_agent.integrations.knowledge_ingestion.text_sanitizer import (
    FRONTMATTER_KEYS,
    build_frontmatter,
    resolve_title,
    sanitize_text,
    split_frontmatter,
)

_NFD_VIETNAMESE_E = "e\u0302\u0301"


def test_sanitize_text_normalizes_nfd_vietnamese_e_to_nfc() -> None:
    assert unicodedata.normalize("NFD", "ế") == _NFD_VIETNAMESE_E
    assert sanitize_text(_NFD_VIETNAMESE_E) == "\u1ebf\n"


def test_sanitize_text_strips_null_control_and_keeps_newlines() -> None:
    assert sanitize_text("keep\x00\nthis") == "keep\nthis\n"


def test_sanitize_text_preserves_indented_list_and_pipe_table() -> None:
    markdown = "intro\n    - nested\n| a | b |"
    assert sanitize_text(markdown) == "intro\n    - nested\n| a | b |\n"


def test_sanitize_text_preserves_html_page_comment() -> None:
    markdown = "<!-- Page 1 -->\nbody"
    assert sanitize_text(markdown) == "<!-- Page 1 -->\nbody\n"


def test_sanitize_text_collapses_runs_of_three_or_more_newlines() -> None:
    assert sanitize_text("a\n\n\n\nb") == "a\n\nb\n"


def test_frontmatter_round_trips_closed_key_set() -> None:
    header = build_frontmatter(
        document_id="policy-file",
        title="Policy: Update",
        source_file="Policy File.pdf",
        extractor="pdf_native",
        page_count=2,
        processed_at="2026-08-16T00:00:00+00:00",
    )
    body = "# Policy: Update\n"
    fields, remainder = split_frontmatter(header + body)

    assert FRONTMATTER_KEYS == (
        "document_id",
        "title",
        "source_file",
        "extractor",
        "page_count",
        "processed_at",
    )
    assert [line.split(":", 1)[0] for line in header.splitlines()[1:7]] == list(FRONTMATTER_KEYS)
    assert fields == {
        "document_id": "policy-file",
        "title": "Policy: Update",
        "source_file": "Policy File.pdf",
        "extractor": "pdf_native",
        "page_count": "2",
        "processed_at": "2026-08-16T00:00:00+00:00",
    }
    assert remainder == body


def test_split_frontmatter_without_leading_fence_returns_empty_fields() -> None:
    markdown = "# Hello\n\nbody\n"
    assert split_frontmatter(markdown) == ({}, markdown)


def test_split_frontmatter_ignores_unknown_keys_and_returns_body() -> None:
    markdown = (
        "---\n"
        "document_id: policy-file\n"
        "extra: ignored\n"
        "title: Policy\n"
        "---\n"
        "\n"
        "# Policy\n"
    )
    fields, body = split_frontmatter(markdown)

    assert fields == {"document_id": "policy-file", "title": "Policy"}
    assert "extra" not in fields
    assert body == "# Policy\n"


def test_resolve_title_uses_first_atx_h1_else_fallback() -> None:
    assert resolve_title("# Official Title\n\n## Section\n", "stem") == "Official Title"
    assert resolve_title("## Not a title\nplain text\n", "stem") == "stem"
