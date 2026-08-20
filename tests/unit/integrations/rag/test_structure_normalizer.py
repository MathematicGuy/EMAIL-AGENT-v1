import pytest

from cowork_agent.integrations.rag.structure_normalizer import normalize_structure
from cowork_agent.integrations.rag.structure_profile import DEFAULT_PROFILE

pytestmark = pytest.mark.extended


def test_promotes_plain_text_article_to_its_own_depth() -> None:
    assert normalize_structure("Điều 1. Phạm vi điều chỉnh") == (
        "#### Điều 1. Phạm vi điều chỉnh"
    )


def test_promotes_each_division_to_its_own_depth() -> None:
    markdown = "Phần I\n\nChương II\n\nMục 3\n\nĐiều 4. Tiêu đề"

    assert normalize_structure(markdown).split("\n\n") == [
        "# Phần I",
        "## Chương II",
        "### Mục 3",
        "#### Điều 4. Tiêu đề",
    ]


def test_bare_division_adopts_the_uppercase_title_beneath_it() -> None:
    assert normalize_structure("Chương I\n\nQUY ĐỊNH CHUNG") == (
        "## Chương I — QUY ĐỊNH CHUNG"
    )


def test_long_numbered_clause_is_left_as_prose() -> None:
    clause = (
        "1. Bản đồ địa chính là bản đồ thể hiện các thửa đất và các đối tượng "
        "địa lý có liên quan, lập theo đơn vị hành chính cấp xã."
    )

    assert normalize_structure(clause) == clause


def test_bare_numbering_is_never_a_heading() -> None:
    """Every measured hit on data/extracted was an enumerated clause, so the
    default profile carries no rule for unqualified numbering."""
    assert normalize_structure("2. Người sử dụng đất.") == "2. Người sử dụng đất."
    assert normalize_structure("1. Hủy giá trị sử dụng hộ chiếu còn thời hạn bị mất") == (
        "1. Hủy giá trị sử dụng hộ chiếu còn thời hạn bị mất"
    )


def test_statute_title_beyond_the_generic_limit_is_still_a_heading() -> None:
    title = "Điều 100. " + "Bồi thường về đất khi Nhà nước thu hồi đất, " * 5

    promoted = normalize_structure(title.strip())

    assert promoted.startswith("#### Điều 100.")
    assert len(title) > DEFAULT_PROFILE.max_title_chars


def test_run_on_prose_naming_several_steps_is_not_a_heading() -> None:
    prose = (
        "Bước 1: Cá nhân chuẩn bị hồ sơ theo quy định của pháp luật. "
        "Bước 2: Cá nhân nộp hồ sơ đến Công an cấp xã. " * 6
    ).strip()

    assert normalize_structure(prose) == prose


def test_line_inside_a_paragraph_is_never_promoted() -> None:
    markdown = "Theo quy định:\nĐiều 5. Nội dung\ncòn hiệu lực."

    assert normalize_structure(markdown) == markdown


def test_content_inside_a_fenced_block_is_untouched() -> None:
    markdown = "```\n\nĐiều 1. Trong code\n\n```"

    assert normalize_structure(markdown) == markdown


def test_existing_atx_headings_pass_through() -> None:
    markdown = "# Policy\n\n## Điều 1. Đã là heading\n\nBody."

    assert normalize_structure(markdown) == markdown


def test_normalization_is_idempotent() -> None:
    markdown = "Chương I\n\nQUY ĐỊNH CHUNG\n\nĐiều 1. Phạm vi\n\nNội dung điều một."

    once = normalize_structure(markdown)

    assert normalize_structure(once) == once


def test_crlf_input_is_normalized_without_promoting_prose() -> None:
    assert normalize_structure("Alpha.\r\n\r\nBeta.\r") == "Alpha.\n\nBeta.\n"
