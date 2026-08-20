import pytest

from cowork_agent.domain._chat_contracts_memory import MAX_CHAT_RAG_SECTION_LENGTH
from cowork_agent.integrations.rag.markdown_chunking import (
    MarkdownPage,
    chunk_markdown,
    chunk_markdown_pages,
    split_markdown_pages,
)
from cowork_agent.integrations.rag.structure_normalizer import normalize_structure

pytestmark = pytest.mark.extended


def test_chunks_by_section_and_prefixes_each_with_its_breadcrumb() -> None:
    chunks = chunk_markdown(
        "Preamble.\n\n# Policy\n\nAlpha.\n\nBeta.\n\n## Detail\n\nGamma."
    )

    assert [(chunk.section, chunk.text) for chunk in chunks] == [
        (None, "Preamble."),
        ("Policy", "Policy\n\nAlpha.\n\nBeta."),
        ("Detail", "Policy\nDetail\n\nGamma."),
    ]


def test_chunk_records_the_full_heading_path() -> None:
    chunks = chunk_markdown("# Policy\n\n## Detail\n\nGamma.")

    assert [chunk.heading_path for chunk in chunks] == [("Policy", "Detail")]


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
    assert chunks[0].text == "Policy\n\nFirst paragraph.\n\nSecond paragraph."


def test_short_existing_markdown_keeps_meaningful_whitespace_exactly() -> None:
    body = "Line with hard break.  \n   - Nested item\n\n> Quote"

    (chunk,) = chunk_markdown(f"# Policy\n{body}\n")

    assert chunk.section == "Policy"
    assert chunk.text == f"Policy\n\n{body}"


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
        ("Policy", "Policy\n\nAlpha."),
    ]


def test_plain_text_article_heading_opens_its_own_section() -> None:
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


def test_numbered_clause_is_not_mistaken_for_a_heading() -> None:
    clause = (
        "1. Bản đồ địa chính là bản đồ thể hiện các thửa đất và các đối tượng "
        "địa lý có liên quan, lập theo đơn vị hành chính cấp xã."
    )

    (chunk,) = chunk_markdown(f"# Giải thích từ ngữ\n\n{clause}")

    assert chunk.section == "Giải thích từ ngữ"
    assert clause in chunk.text


def test_table_split_repeats_its_header_on_every_part() -> None:
    rows = "\n".join(f"| row {index} | {'x' * 60} |" for index in range(20))
    table = f"| Name | Value |\n| --- | --- |\n{rows}"

    chunks = chunk_markdown(f"# Fees\n\n{table}", max_chars=400)

    assert len(chunks) > 1
    assert all("| Name | Value |" in chunk.text for chunk in chunks)
    assert all("| --- | --- |" in chunk.text for chunk in chunks)


def test_fenced_code_is_never_cut_mid_block() -> None:
    body = "\n".join(f"value_{index} = {index}" for index in range(60))

    chunks = chunk_markdown(f"# Snippet\n\n```python\n{body}\n```", max_chars=400)

    assert len(chunks) > 1
    assert all(chunk.text.count("```") % 2 == 0 for chunk in chunks)


def test_page_marker_comment_never_becomes_a_chunk() -> None:
    chunks = chunk_markdown("# Policy\n\n<!-- Page 1 -->\n\nAlpha body.")

    assert [chunk.text for chunk in chunks] == ["Policy\n\nAlpha body."]


def test_split_section_overlaps_within_itself_but_not_across_articles() -> None:
    long_body = " ".join(f"Câu số {index} trong điều này." for index in range(80))
    chunks = chunk_markdown(
        normalize_structure(
            f"Điều 1. Điều dài\n\n{long_body}\n\nĐiều 2. Điều ngắn\n\nNội dung ngắn."
        ),
        max_chars=600,
    )

    first = [chunk for chunk in chunks if chunk.section == "Điều 1. Điều dài"]
    assert len(first) > 1
    tail = first[0].text.rsplit(" ", 4)[-1]
    assert tail in first[1].text
    assert all("Điều 2" not in chunk.text for chunk in first)


def test_breadcrumb_and_body_together_respect_the_ceiling() -> None:
    body = " ".join(f"Đoạn văn số {index}." for index in range(400))
    markdown = normalize_structure(
        f"Chương I\n\nQUY ĐỊNH CHUNG\n\nĐiều 7. Một điều rất dài\n\n{body}"
    )

    chunks = chunk_markdown(markdown, max_chars=500)

    assert len(chunks) > 1
    assert all(len(chunk.text) <= 500 for chunk in chunks)


def test_overlong_heading_is_fitted_to_the_citation_label_limit() -> None:
    """``section`` is a citation label, and every consumer rejects one past 300
    characters — so a statute title that long must be fitted, not passed on."""
    title = "Điều 100. " + "Bồi thường về đất khi Nhà nước thu hồi đất, " * 8
    heading = title.strip()

    (chunk,) = chunk_markdown(f"# {heading}\n\nNội dung.")

    assert chunk.section is not None
    assert len(heading) > MAX_CHAT_RAG_SECTION_LENGTH
    assert len(chunk.section) <= MAX_CHAT_RAG_SECTION_LENGTH
    assert chunk.section.startswith("Điều 100.")
    assert chunk.section.endswith("…")
    # The full heading is still recoverable, so nothing is lost by labelling.
    assert chunk.heading_path == (heading,)


def test_heading_within_the_label_limit_is_left_exactly_as_written() -> None:
    (chunk,) = chunk_markdown("# Điều 1. Phạm vi điều chỉnh\n\nNội dung.")

    assert chunk.section == "Điều 1. Phạm vi điều chỉnh"


def test_points_cut_from_their_clause_carry_the_clause_stem() -> None:
    points = "\n\n".join(
        f"{letter}) Trách nhiệm số {index} của Ủy ban nhân dân cấp tỉnh "
        "đối với quốc lộ được phân cấp quản lý."
        for index, letter in enumerate("abcdđefghi")
    )
    markdown = normalize_structure(
        "Điều 4. Phân cấp quản lý quốc lộ\n\n"
        "1. Phân cấp để Ủy ban nhân dân cấp tỉnh quản lý quốc lộ bao gồm điều này.\n\n"
        "5. Trách nhiệm của Ủy ban nhân dân cấp tỉnh đối với quốc lộ được phân cấp\n\n"
        f"{points}"
    )

    chunks = chunk_markdown(markdown, max_chars=600)

    resumed = [chunk for chunk in chunks[1:] if chunk.section == "Điều 4. Phân cấp quản lý quốc lộ"]
    assert resumed
    assert all(
        "5. Trách nhiệm của Ủy ban nhân dân cấp tỉnh" in chunk.text for chunk in resumed
    )
    assert all(len(chunk.text) <= 600 for chunk in chunks)


def test_clause_stem_is_not_repeated_before_a_clause_that_names_itself() -> None:
    clauses = "\n\n".join(
        f"{index}. Khoản số {index} nêu rõ nội dung của khoản này một cách đầy đủ."
        for index in range(1, 12)
    )
    chunks = chunk_markdown(
        normalize_structure(f"Điều 9. Các khoản độc lập\n\n{clauses}"), max_chars=400
    )

    assert len(chunks) > 1
    resumed = chunks[1].text.split("\n\n")[1]
    assert resumed.startswith("7. Khoản số 7")
    assert resumed not in chunks[0].text


def test_a_stem_too_long_to_repeat_is_left_out_of_the_resumed_chunk() -> None:
    stem = "2. " + " ".join(f"vế thứ {index} của khoản này" for index in range(40)) + ":"
    points = "\n\n".join(
        f"{letter}) Điểm {letter} quy định nội dung cụ thể tương ứng."
        for letter in "abcdđefgh"
    )
    chunks = chunk_markdown(
        normalize_structure(f"Điều 3. Điều có khoản dài\n\n{stem}\n\n{points}"),
        max_chars=400,
    )

    assert len(chunks) > 1
    assert all(len(chunk.text) <= 400 for chunk in chunks)
    assert not any(chunk.text.count(stem) > 1 for chunk in chunks)
