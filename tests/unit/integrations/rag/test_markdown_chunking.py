from cowork_agent.domain._chat_contracts_memory import MAX_CHAT_RAG_SECTION_LENGTH
from cowork_agent.integrations.rag.markdown_chunking import (
    MarkdownPage,
    chunk_markdown,
    chunk_markdown_pages,
    split_markdown_pages,
)
from cowork_agent.integrations.rag.structure_normalizer import normalize_structure


def test_chunk_markdown_heading_breadcrumbs_and_page_spans() -> None:
    chunks = chunk_markdown("Preamble.\n\n# Policy\n\nAlpha.\n\nBeta.\n\n## Detail\n\nGamma.")
    assert [(chunk.section, chunk.text) for chunk in chunks] == [
        (None, "Preamble."),
        ("Policy", "Policy\n\nAlpha.\n\nBeta."),
        ("Detail", "Policy\nDetail\n\nGamma."),
    ]
    assert chunks[2].heading_path == ("Policy", "Detail")

    # Multi-page chunking coordinates
    page_chunks = chunk_markdown_pages(
        (
            MarkdownPage("# Policy\n\nFirst paragraph.", 1),
            MarkdownPage("Second paragraph.", 2),
        )
    )
    assert len(page_chunks) == 1
    assert page_chunks[0].section == "Policy"
    assert page_chunks[0].page_start == 1 and page_chunks[0].page_end == 2


def test_split_markdown_pages_parsing_and_normalization() -> None:
    # Unnumbered & CRLF normalization
    assert split_markdown_pages("# Policy\r\n\r\nAlpha.\r") == (
        MarkdownPage(markdown="# Policy\n\nAlpha.\n", page_number=None),
    )

    # Marker splitting
    pages = split_markdown_pages("Preface.\n<!-- Page 1 -->\nAlpha\n\n<!-- Page 12 -->\nBeta")
    assert [page.page_number for page in pages] == [None, 1, 12]
    assert pages[0].markdown == "Preface."
    assert pages[1].markdown == "Alpha\n"
    assert all("<!--" not in page.markdown for page in pages)


def test_oversize_and_block_preservation_deterministic_splitting() -> None:
    # Deterministic paragraph split
    text = "Sentence one. " * 200
    first = chunk_markdown(text, max_chars=200)
    second = chunk_markdown(text, max_chars=200)
    assert first == second and len(first) > 1
    assert all(0 < len(chunk.text) <= 200 for chunk in first)

    # Tables repeat header on split
    rows = "\n".join(f"| row {index} | {'x' * 60} |" for index in range(20))
    table = f"| Name | Value |\n| --- | --- |\n{rows}"
    table_chunks = chunk_markdown(f"# Fees\n\n{table}", max_chars=400)
    assert len(table_chunks) > 1
    assert all("| Name | Value |" in chunk.text for chunk in table_chunks)

    # Fenced code blocks never cut mid block
    code = "\n".join(f"value_{index} = {index}" for index in range(60))
    code_chunks = chunk_markdown(f"# Snippet\n\n```python\n{code}\n```", max_chars=400)
    assert all(chunk.text.count("```") % 2 == 0 for chunk in code_chunks)


def test_legal_structure_and_article_headings_normalization() -> None:
    chunks = chunk_markdown(
        normalize_structure(
            "Chương I\n\nQUY ĐỊNH CHUNG\n\n"
            "Điều 1. Phạm vi điều chỉnh\n\nLuật này quy định về đất đai.\n\n"
            "Điều 2. Đối tượng áp dụng\n\nNgười sử dụng đất chịu điều chỉnh."
        )
    )
    assert [chunk.section for chunk in chunks] == [
        "Điều 1. Phạm vi điều chỉnh",
        "Điều 2. Đối tượng áp dụng",
    ]
    assert chunks[0].text.startswith("Chương I — QUY ĐỊNH CHUNG\nĐiều 1.")

    # Numbered clause is not mistaken for heading
    clause = "1. Bản đồ địa chính là bản đồ thể hiện các thửa đất."
    (c_clause,) = chunk_markdown(f"# Giải thích từ ngữ\n\n{clause}")
    assert c_clause.section == "Giải thích từ ngữ" and clause in c_clause.text


def test_citation_label_limits_and_stem_continuity() -> None:
    # Overlong heading fitted to citation label limit
    title = "Điều 100. " + "Bồi thường về đất khi Nhà nước thu hồi đất, " * 8
    (chunk,) = chunk_markdown(f"# {title.strip()}\n\nNội dung.")
    assert chunk.section is not None
    assert len(chunk.section) <= MAX_CHAT_RAG_SECTION_LENGTH
    assert chunk.section.endswith("…")
    assert chunk.heading_path == (title.strip(),)

    # Points cut from clause carry clause stem
    points = "\n\n".join(
        f"{letter}) Trách nhiệm số {index} của Ủy ban nhân dân."
        for index, letter in enumerate("abcdđefghi")
    )
    markdown = normalize_structure(
        f"Điều 4. Phân cấp quản lý\n\n5. Trách nhiệm của Ủy ban nhân dân cấp tỉnh\n\n{points}"
    )
    stem_chunks = chunk_markdown(markdown, max_chars=400)
    resumed = [c for c in stem_chunks[1:] if c.section == "Điều 4. Phân cấp quản lý"]
    assert all("5. Trách nhiệm của Ủy ban nhân dân cấp tỉnh" in c.text for c in resumed)
