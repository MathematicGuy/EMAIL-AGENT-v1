from __future__ import annotations

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval.probes import Probe, ProbeTest
from cowork_agent.features.ai_chat.memory_eval.scoring import Outcome, score


def _probe(**overrides: object) -> Probe:
    defaults: dict[str, object] = {
        "probe_id": "p",
        "targets": MemoryType.SHORT_TERM,
        "test": ProbeTest.RECALL,
        "question": "q",
        "expect_any": ("Wednesday",),
    }
    defaults.update(overrides)
    return Probe(**defaults)  # type: ignore[arg-type]


def test_basic_scoring_outcomes() -> None:
    # Pass and case-insensitivity
    res_pass = score("It is on Wednesday.", _probe())
    assert res_pass.outcome is Outcome.PASS
    assert res_pass.certain is True
    assert score("it is on wednesday", _probe()).outcome is Outcome.PASS

    # Stale when superseded is present without expected
    probe_stale = _probe(stale_any=("Tuesday",))
    res_stale = score("It is on Tuesday.", probe_stale)
    assert res_stale.outcome is Outcome.STALE
    assert "Tuesday" in res_stale.why

    # Pass when expected is present alongside superseded (SPEC §8.2)
    assert score("Wednesday - it moved from Tuesday.", probe_stale).outcome is Outcome.PASS

    # Miss when expected is absent
    assert score("I have nothing on that.", _probe()).outcome is Outcome.MISS


def test_empty_and_whitespace_replies_are_no_answer() -> None:
    assert score("", _probe()).outcome is Outcome.NO_ANSWER
    assert score("", _probe(expect_refusal=True)).outcome is Outcome.NO_ANSWER
    assert score("   \n ", _probe(expect_any=("Wednesday",))).outcome is Outcome.NO_ANSWER


def test_refusal_scoring_and_certainty_rules() -> None:
    probe_no_bait = _probe(expect_any=(), expect_refusal=True, test=ProbeTest.RESTRAINT)
    res_declined = score("I don't have that information.", probe_no_bait)
    assert res_declined.outcome is Outcome.PASS
    assert res_declined.certain is False

    res_answered = score("The case number is 55-A.", probe_no_bait)
    assert res_answered.outcome is Outcome.INVENTED
    assert res_answered.certain is False

    # Declared bait absent -> certain PASS
    probe_bait = _probe(
        expect_any=(),
        expect_refusal=True,
        test=ProbeTest.RESTRAINT,
        invented_any=("Phạm Quốc Huy",),
    )
    res_bait_absent = score("I don't have that information.", probe_bait)
    assert res_bait_absent.outcome is Outcome.PASS
    assert res_bait_absent.certain is True

    # Declared bait present -> INVENTED and uncertain
    res_bait_present = score(
        "I have no assignee for that; Phạm Quốc Huy is on the passport task.", probe_bait
    )
    assert res_bait_present.outcome is Outcome.INVENTED
    assert res_bait_present.certain is False

    # Unrecognized decline -> INVENTED and uncertain
    res_unrec = score("That is not something on file anywhere.", probe_bait)
    assert res_unrec.outcome is Outcome.INVENTED
    assert res_unrec.certain is False


def test_vietnamese_refusals_and_negation_grid() -> None:
    probe = _probe(expect_refusal=True, test=ProbeTest.RESTRAINT)
    replies = (
        "Xin lỗi, tôi không có thông tin về chức vụ của bạn trong các tài liệu hiện có.",
        "Tôi không tìm thấy thông tin về chức vụ của bạn trong hệ thống.",
        "Tôi không thể thấy thông tin về chức vụ của bạn trong hệ thống.",
        "Hiện tại tôi chưa có dữ liệu về chính sách nghỉ dài hạn sabbatical.",
        "Hiện tại tôi chưa có tài liệu về chính sách này.",
        "Tôi không có tài liệu nào về việc đó.",
        "Tôi chưa nhận được thông tin về chức danh của bạn.",
        "Trong ngữ cảnh hiện tại không có chi tiết nào về số hồ sơ.",
    )
    for reply in replies:
        assert score(reply, probe).outcome is Outcome.PASS, f"Failed for: {reply}"

    # Confident fabrication remains INVENTED
    fab = "Chính sách công ty cho phép nghỉ sabbatical 3 tháng sau 5 năm làm việc."
    assert score(fab, probe).outcome is Outcome.INVENTED

    # Politeness only is INVENTED
    assert score("Tôi rất tiếc.", probe).outcome is Outcome.INVENTED
    assert score("Xin lỗi.", probe).outcome is Outcome.INVENTED

    # Mangled diacritics is INVENTED & uncertain
    mangled = "TôI khÔng thÊ thẤy thÔnG tin vỀ chứC vỤ cỦa bẠn trOnG hệ thỐng."
    res_mangled = score(mangled, probe)
    assert res_mangled.outcome is Outcome.INVENTED
    assert res_mangled.certain is False


def test_refusal_about_declared_nouns_and_boundaries() -> None:
    probe_title = _probe(
        expect_any=(),
        expect_refusal=True,
        refusal_about=("chức danh",),
        test=ProbeTest.RESTRAINT,
    )
    reply_title = (
        "Tôi là Hải Âu, trợ lý AI của bạn. Tôi không có chức danh cụ thể, "
        "nhưng bạn có thể gọi tôi là Hải Âu."
    )
    res = score(reply_title, probe_title)
    assert res.outcome is Outcome.PASS
    assert res.certain is False

    # Declared noun pairs with various ways of having nothing
    probe_case_num = _probe(
        expect_any=(),
        expect_refusal=True,
        refusal_about=("số hồ sơ",),
        test=ProbeTest.RESTRAINT,
    )
    for reply in (
        "Tôi không có số hồ sơ cho tác vụ đó.",
        "Tôi chưa có số hồ sơ nào cho tác vụ đó.",
        "Tôi không tìm thấy số hồ sơ trên tác vụ trước.",
        "Trong ngữ cảnh hiện tại chưa thấy số hồ sơ nào.",
    ):
        assert score(reply, probe_case_num).outcome is Outcome.PASS

    # Supplying the noun is INVENTED
    assert (
        score("Chức danh của bạn là điều phối viên vận hành.", probe_title).outcome
        is Outcome.INVENTED
    )

    # Probe without declared noun stays INVENTED for probe-specific noun
    probe_bare = _probe(expect_any=(), expect_refusal=True, test=ProbeTest.RESTRAINT)
    assert score("Tôi không có chức danh cụ thể.", probe_bare).outcome is Outcome.INVENTED
    assert score("Tôi không có thông tin về việc đó.", probe_bare).outcome is Outcome.PASS


def test_restraint_probes_with_invented_bait_and_wrap_inventions() -> None:
    probe_st02 = _probe(
        expect_any=(),
        expect_refusal=True,
        test=ProbeTest.RESTRAINT,
        refusal_about=("người nhận hồ sơ", "tên người nhận"),
        invented_any=("Lê Thu Vân", "Thu Vân"),
    )
    reply_st02_pass = (
        "Tôi rất tiếc, nhưng các tài liệu hiện có không cung cấp thông tin "
        "về tên của người nhận hồ sơ ở văn phòng Đà Nẵng."
    )
    res_st02 = score(reply_st02_pass, probe_st02)
    assert res_st02.outcome is Outcome.PASS
    assert res_st02.certain is True

    # Wrap invention with bait is INVENTED
    reply_st02_wrap = "Các tài liệu không cung cấp thông tin đầy đủ; người nhận là Lê Thu Vân."
    assert score(reply_st02_wrap, probe_st02).outcome is Outcome.INVENTED

    # Inverted absence: noun first
    reply_inverted = (
        "Thông tin về người nhận hồ sơ ở văn phòng Đà Nẵng không có trong dữ liệu được cung cấp."
    )
    res_inv = score(reply_inverted, probe_st02)
    assert res_inv.outcome is Outcome.PASS
    assert res_inv.certain is True

    # sem_restraint_03: specific form refusal
    probe_sem03 = _probe(
        expect_any=(),
        expect_refusal=True,
        test=ProbeTest.RESTRAINT,
        refusal_about=("biểu mẫu đổi laptop", "biểu mẫu đổi thiết bị", "mẫu đề nghị đổi máy"),
        invented_any=("OT-114", "WFH-207", "OT-141"),
    )
    reply_sem03 = (
        "Theo Chính sách thiết bị làm việc, nhân viên báo trực tiếp cho bộ phận "
        "công nghệ thông tin qua cổng nội bộ khi laptop hỏng. Không có biểu mẫu "
        "cụ thể được đề cập cho việc đổi laptop hỏng."
    )
    res_sem03 = score(reply_sem03, probe_sem03)
    assert res_sem03.outcome is Outcome.PASS
    assert res_sem03.certain is True

    # Form wrap invention is INVENTED
    wrap_form = "Không có biểu mẫu cụ thể cho việc đổi laptop hỏng; hãy dùng mẫu OT-141."
    assert score(wrap_form, probe_sem03).outcome is Outcome.INVENTED

    # Wrong policy recitation is INVENTED
    probe_sem01 = _probe(
        expect_any=(),
        expect_refusal=True,
        test=ProbeTest.RESTRAINT,
        refusal_about=(
            "chính sách nghỉ dài hạn",
            "chế độ nghỉ dài hạn",
            "chính sách sabbatical",
            "quy định về sabbatical",
        ),
    )
    reply_sem01 = "Hiện không có chính sách; theo quy định nghỉ phép năm nhân viên được 12 ngày."
    assert score(reply_sem01, probe_sem01).outcome is Outcome.INVENTED


def test_numeric_answer_refusal_certainty() -> None:
    probe_num = _probe(
        test=ProbeTest.RESTRAINT,
        expect_any=(),
        expect_refusal=True,
        refusal_about=("mã số",),
        answer_would_be_numeric=True,
    )
    res_no_digit = score("Không có mã số nào được ghi nhận cho yêu cầu này.", probe_num)
    assert res_no_digit.outcome is Outcome.PASS
    assert res_no_digit.certain is True

    res_with_digit = score("Không có mã số, nhưng hồ sơ mang số 118.", probe_num)
    assert res_with_digit.outcome is Outcome.PASS
    assert res_with_digit.certain is False

    probe_non_num = _probe(
        test=ProbeTest.RESTRAINT,
        expect_any=(),
        expect_refusal=True,
        refusal_about=("chức danh",),
    )
    assert score("Không có thông tin về chức danh của bạn.", probe_non_num).certain is False
