from cowork_agent.integrations.rag.markdown_chunking import (
    MarkdownPage,
    chunk_markdown,
    chunk_markdown_pages,
    split_markdown_pages,
)


def test_chunks_by_section_and_paragraph_without_overlap() -> None:
    chunks = chunk_markdown(
        "Preamble.\n\n# Policy\n\nAlpha.\n\nBeta.\n\n## Detail\n\nGamma."
    )

    assert [(chunk.section, chunk.text) for chunk in chunks] == [
        (None, "Preamble."),
        ("Policy", "Alpha.\n\nBeta."),
        ("Detail", "Gamma."),
    ]


def test_page_coordinates_span_pages_within_one_section() -> None:
    chunks = chunk_markdown_pages(
        (
            MarkdownPage("# Policy\n\nFirst paragraph.", 1),
            MarkdownPage("Second paragraph.", 2),
        )
    )

    assert len(chunks) == 1
    assert chunks[0].section == "Policy"
    assert chunks[0].page_start == 1
    assert chunks[0].page_end == 2
    assert chunks[0].text == "First paragraph.\n\nSecond paragraph."


def test_short_existing_markdown_keeps_meaningful_whitespace_exactly() -> None:
    body = "Line with hard break.  \n   - Nested item\n\n> Quote"

    (chunk,) = chunk_markdown(f"# Policy\n{body}\n")

    assert chunk.section == "Policy"
    assert chunk.text == body


def test_oversize_paragraph_splits_deterministically_within_limit() -> None:
    text = "Sentence one. " * 200
    first = chunk_markdown(text, max_chars=200)
    second = chunk_markdown(text, max_chars=200)

    assert first == second
    assert len(first) > 1
    assert all(0 < len(chunk.text) <= 200 for chunk in first)


def test_split_markdown_pages_without_markers_returns_one_unnumbered_page() -> None:
    markdown = "# Policy\n\nAlpha.\n"

    pages = split_markdown_pages(markdown)

    assert pages == (MarkdownPage(markdown=markdown, page_number=None),)


def test_split_markdown_pages_without_markers_normalizes_crlf() -> None:
    pages = split_markdown_pages("# Policy\r\n\r\nAlpha.\r")

    assert pages == (MarkdownPage(markdown="# Policy\n\nAlpha.\n", page_number=None),)


def test_split_markdown_pages_splits_two_markers_and_drops_comment_lines() -> None:
    pages = split_markdown_pages("<!-- Page 1 -->\nAlpha\n\n<!-- Page 2 -->\nBeta")

    assert [page.page_number for page in pages] == [1, 2]
    assert pages[0].markdown == "Alpha\n"
    assert pages[1].markdown == "Beta"
    assert all("<!--" not in page.markdown for page in pages)


def test_split_markdown_pages_parses_double_digit_page_number() -> None:
    pages = split_markdown_pages("<!-- Page 12 -->\nBody on twelve.")

    assert pages == (MarkdownPage(markdown="Body on twelve.", page_number=12),)


def test_split_markdown_pages_keeps_leading_text_unnumbered() -> None:
    pages = split_markdown_pages("Preface.\n<!-- Page 1 -->\nAlpha")

    assert pages == (
        MarkdownPage(markdown="Preface.", page_number=None),
        MarkdownPage(markdown="Alpha", page_number=1),
    )


def test_split_markdown_pages_omits_marker_from_every_page_body() -> None:
    pages = split_markdown_pages(
        "<!-- Page 1 -->\nKeep me.\n<!-- Page 12 -->\nAlso keep.\n"
    )

    assert pages
    assert all("<!-- Page" not in page.markdown for page in pages)
    assert all("-->" not in page.markdown for page in pages)


def test_chunk_markdown_still_splits_sections() -> None:
    chunks = chunk_markdown("Preamble.\n\n# Policy\n\nAlpha.")

    assert [(chunk.section, chunk.text) for chunk in chunks] == [
        (None, "Preamble."),
        ("Policy", "Alpha."),
    ]
