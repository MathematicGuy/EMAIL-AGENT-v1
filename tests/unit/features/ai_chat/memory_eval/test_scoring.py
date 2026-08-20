from __future__ import annotations

import pytest

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval.probes import Probe, ProbeTest
from cowork_agent.features.ai_chat.memory_eval.scoring import Outcome, score

pytestmark = pytest.mark.extended


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


def test_expected_answer_present_is_a_pass() -> None:
    result = score("It is on Wednesday.", _probe())
    assert result.outcome is Outcome.PASS
    assert result.certain is True


def test_matching_is_case_insensitive() -> None:
    assert score("it is on wednesday", _probe()).outcome is Outcome.PASS


def test_expected_absent_with_superseded_present_is_stale() -> None:
    probe = _probe(stale_any=("Tuesday",))
    result = score("It is on Tuesday.", probe)
    assert result.outcome is Outcome.STALE
    assert "Tuesday" in result.why


def test_expected_present_alongside_superseded_is_still_a_pass() -> None:
    # "Wednesday, it moved from Tuesday" is the most helpful phrasing available.
    # Scoring it STALE would penalise the best answer. SPEC §8.2.
    probe = _probe(stale_any=("Tuesday",))
    assert score("Wednesday - it moved from Tuesday.", probe).outcome is Outcome.PASS


def test_expected_absent_with_no_superseded_is_a_miss() -> None:
    result = score("I have nothing on that.", _probe())
    assert result.outcome is Outcome.MISS


def test_refusal_expected_and_declined_is_a_pass_but_uncertain() -> None:
    probe = _probe(expect_any=(), expect_refusal=True, test=ProbeTest.RESTRAINT)
    result = score("I don't have that information.", probe)
    assert result.outcome is Outcome.PASS
    assert result.certain is False


def test_refusal_expected_and_answered_is_invented_and_uncertain() -> None:
    probe = _probe(expect_any=(), expect_refusal=True, test=ProbeTest.RESTRAINT)
    result = score("The case number is 55-A.", probe)
    assert result.outcome is Outcome.INVENTED
    assert result.certain is False


def test_empty_reply_is_not_an_answer_and_is_not_graded_as_memory() -> None:
    # A turn that produced no text tells you nothing about memory. Grading it
    # MISS put a provider outage into the same bucket as an amnesiac store, and
    # three of the eight conclusions in the first Vietnamese run rested on it.
    result = score("", _probe())
    assert result.outcome is Outcome.NO_ANSWER
    assert result.certain is True


def test_an_empty_reply_is_not_an_invention() -> None:
    # An errored turn emits no delta, so the reply text is "". Calling that
    # INVENTED on a restraint probe reports the harness's headline failure for
    # a turn where the model never spoke.
    assert score("", _probe(expect_refusal=True)).outcome is Outcome.NO_ANSWER


def test_whitespace_only_is_treated_the_same_way() -> None:
    assert score("   \n ", _probe(expect_any=("Wednesday",))).outcome is Outcome.NO_ANSWER


def test_a_vietnamese_refusal_is_a_refusal() -> None:
    # The product answers in Vietnamese unconditionally, so an English-only
    # phrase list scored every honest refusal as INVENTED.
    reply = "Xin lỗi, tôi không có thông tin về chức vụ của bạn trong các tài liệu hiện có."
    assert score(reply, _probe(expect_refusal=True)).outcome is Outcome.PASS


def test_a_vietnamese_not_found_is_a_refusal() -> None:
    reply = "Tôi không tìm thấy thông tin về chức vụ của bạn trong hệ thống."
    assert score(reply, _probe(expect_refusal=True)).outcome is Outcome.PASS


def test_cannot_see_is_a_refusal() -> None:
    assert (
        score(
            "Tôi không thể thấy thông tin về chức vụ của bạn trong hệ thống.",
            _probe(expect_refusal=True),
        ).outcome
        is Outcome.PASS
    )


def test_a_refusal_whose_diacritics_the_model_mangled_is_not_rescued() -> None:
    # gemini-3.5-flash-lite returned exactly this: alternating case at random,
    # and "thể" lost its diacritic to "thê" on the way. It is an honest refusal
    # that no phrase list should be bent to catch — adding the typo would put a
    # model defect into the scorer. This is what `certain=False` is for: the row
    # lands in `needs_reading` and a person reads the text in runs/.
    reply = "TôI khÔng thÊ thẤy thÔnG tin vỀ chứC vỤ cỦa bẠn trOnG hệ thỐng."
    result = score(reply, _probe(expect_refusal=True))
    assert result.outcome is Outcome.INVENTED
    assert result.certain is False


def test_every_negation_pairs_with_every_word_for_what_is_missing() -> None:
    # The phrase list was a hand-written half of a grid. "khong co thong tin"
    # and "khong co du lieu" and "chua co thong tin" were on it; "chua co du
    # lieu" was not, and one reply that used it was graded INVENTED. That single
    # row made semantic memory the most severe conclusion in the whole report.
    replies = (
        "Hiện tại tôi chưa có dữ liệu về chính sách nghỉ dài hạn sabbatical.",
        "Hiện tại tôi chưa có tài liệu về chính sách này.",
        "Tôi không có tài liệu nào về việc đó.",
        "Tôi chưa nhận được thông tin về chức danh của bạn.",
        "Trong ngữ cảnh hiện tại không có chi tiết nào về số hồ sơ.",
    )
    for reply in replies:
        assert score(reply, _probe(expect_refusal=True)).outcome is Outcome.PASS, reply


def test_a_confident_fabrication_is_still_an_invention() -> None:
    # The guard on widening the phrase list. A refusal list broad enough to
    # catch every honest decline is worthless if it also passes an answer that
    # was made up, because every restraint probe would pass forever.
    reply = "Chính sách công ty cho phép nghỉ sabbatical 3 tháng sau 5 năm làm việc."
    assert score(reply, _probe(expect_refusal=True)).outcome is Outcome.INVENTED


def test_a_refusal_that_names_the_thing_asked_for_is_a_refusal() -> None:
    # The noun axis of the grid is a closed list of words for KINDS OF
    # KNOWLEDGE — thông tin, dữ liệu, tài liệu. A model that declines by naming
    # the thing it was asked for instead ("I have no job title") hits no cell,
    # and the reply below was graded INVENTED for it. It invents nothing: it
    # misreads "tôi" as the assistant and then declines.
    #
    # The noun cannot be added to the shared list, because it is different for
    # every restraint probe. The probe knows it, so the probe declares it.
    reply = (
        "Tôi là Hải Âu, trợ lý AI của bạn. Tôi không có chức danh cụ thể, "
        "nhưng bạn có thể gọi tôi là Hải Âu."
    )
    probe = _probe(
        expect_any=(),
        expect_refusal=True,
        refusal_about=("chức danh",),
        test=ProbeTest.RESTRAINT,
    )
    result = score(reply, probe)
    assert result.outcome is Outcome.PASS
    assert result.certain is False


def test_a_declared_noun_pairs_with_every_way_of_having_nothing() -> None:
    # Same grid property as the shared list: declaring the noun once means
    # every negation combines with it, rather than only the one phrasing that
    # happened to be seen.
    probe = _probe(
        expect_any=(),
        expect_refusal=True,
        refusal_about=("số hồ sơ",),
        test=ProbeTest.RESTRAINT,
    )
    replies = (
        "Tôi không có số hồ sơ cho tác vụ đó.",
        "Tôi chưa có số hồ sơ nào cho tác vụ đó.",
        "Tôi không tìm thấy số hồ sơ trên tác vụ trước.",
        "Trong ngữ cảnh hiện tại chưa thấy số hồ sơ nào.",
    )
    for reply in replies:
        assert score(reply, probe).outcome is Outcome.PASS, reply


def test_declaring_the_noun_does_not_pass_an_answer_that_supplies_it() -> None:
    # The guard on the widening. This is the reply that made run 2 of
    # 2026-08-19 report `dangerous`, and it is a true invention — the noun is
    # declared, and it must still be graded INVENTED, because no word for
    # having nothing sits next to it.
    probe = _probe(
        expect_any=(),
        expect_refusal=True,
        refusal_about=("chức danh",),
        test=ProbeTest.RESTRAINT,
    )
    assert (
        score("Chức danh của bạn là điều phối viên vận hành.", probe).outcome is Outcome.INVENTED
    )


def test_a_probe_that_declares_no_noun_grades_exactly_as_before() -> None:
    # refusal_about is additive. A probe that declares nothing keeps the shared
    # list and only the shared list.
    probe = _probe(expect_any=(), expect_refusal=True, test=ProbeTest.RESTRAINT)
    assert score("Tôi không có chức danh cụ thể.", probe).outcome is Outcome.INVENTED
    assert score("Tôi không có thông tin về việc đó.", probe).outcome is Outcome.PASS
