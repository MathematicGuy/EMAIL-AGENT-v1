from cowork_agent.integrations.rag.markdown_chunking import (
    MarkdownPage,
    chunk_markdown,
    chunk_markdown_pages,
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
