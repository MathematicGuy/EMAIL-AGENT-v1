"""Owner of the report-filename rule.

Every writer of a report artifact — the artifacts view and the AI Chat turn that
generates one — names its file through ``ReportFilename``. These cases are the
rule; route and store tests assert that they go through it, not what it decides.
"""

import pytest

from cowork_agent.domain.report_artifacts import (
    MAX_REPORT_FILENAME_LENGTH,
    InvalidReportFilename,
    ReportFilename,
)


def test_parse_strips_every_directory_part() -> None:
    """A traversing name reduces to its last component or is refused outright."""
    cases = [
        "../../../etc/passwd",
        "..\\..\\Windows\\System32\\evil.md",
        "subdir/report.md",
        "subdir\\report.md",
        "/absolute/report.md",
        "C:\\Users\\PC\\report.md",
    ]
    for raw in cases:
        parsed = ReportFilename.parse(raw)
        assert "/" not in parsed.value
        assert "\\" not in parsed.value
        assert parsed.value not in {"", ".", ".."}


def test_parse_refuses_names_that_address_no_file() -> None:
    for raw in ("", "   ", ".", "..", "../..", "report\x00.md"):
        with pytest.raises(InvalidReportFilename):
            ReportFilename.parse(raw)


def test_parse_refuses_a_dotfile() -> None:
    # The listing skips dotfiles, so writing one would create a report that can
    # never be read back.
    with pytest.raises(InvalidReportFilename):
        ReportFilename.parse(".hidden.md")


def test_parse_refuses_an_over_long_name() -> None:
    with pytest.raises(InvalidReportFilename):
        ReportFilename.parse("a" * (MAX_REPORT_FILENAME_LENGTH + 1) + ".md")


def test_parse_keeps_a_usable_name_untouched() -> None:
    assert ReportFilename.parse("bao-cao-quy-trinh-cccd.md").value == ("bao-cao-quy-trinh-cccd.md")


def test_constructing_directly_still_validates() -> None:
    # ``ReportFilename(untrusted)`` must not be a way around ``parse``.
    with pytest.raises(InvalidReportFilename):
        ReportFilename("../escape.md")


def test_sanitize_never_raises_and_always_yields_a_safe_name() -> None:
    """The provider names this file, so a bad name degrades — it does not raise."""
    cases = [
        "../../../etc/passwd",
        "..\\..\\evil.md",
        "",
        "   ",
        ".",
        "..",
        ".hidden",
        "///",
        "a" * 400,
        "?*<>|.md",
    ]
    for raw in cases:
        name = ReportFilename.sanitize(raw)
        assert "/" not in name.value
        assert "\\" not in name.value
        assert not name.value.startswith(".")
        assert len(name.value) <= MAX_REPORT_FILENAME_LENGTH
        assert name.value not in {"", ".", ".."}


def test_sanitize_folds_vietnamese_diacritics_into_an_ascii_slug() -> None:
    assert ReportFilename.sanitize("Báo cáo tổng hợp.md").value == "bao-cao-tong-hop.md"


def test_sanitize_defaults_the_suffix_when_the_provider_omits_one() -> None:
    assert ReportFilename.sanitize("bao cao thang 8").value == "bao-cao-thang-8.md"


def test_sanitize_keeps_a_deliberate_non_markdown_suffix() -> None:
    assert ReportFilename.sanitize("Ke hoach.docx").value == "ke-hoach.docx"


def test_sanitize_falls_back_to_the_default_stem_when_nothing_survives() -> None:
    assert ReportFilename.sanitize("!!!").value == "bao-cao-tong-hop.md"


def test_suffix_is_lowercased_for_media_type_lookup() -> None:
    assert ReportFilename.parse("Report.MD").suffix == ".md"
