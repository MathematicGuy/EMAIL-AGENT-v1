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


def test_empty_reply_is_a_miss_not_a_crash() -> None:
    assert score("", _probe()).outcome is Outcome.MISS


def test_an_empty_reply_is_not_an_invention() -> None:
    # An errored turn emits no delta, so the reply text is "". Calling that
    # INVENTED on a restraint probe reports the harness's headline failure for
    # a turn where the model never spoke.
    result = score("", _probe(expect_refusal=True))
    assert result.outcome is Outcome.MISS
    assert result.certain is False


def test_whitespace_only_is_treated_the_same_way() -> None:
    assert score("   \n ", _probe(expect_any=("Wednesday",))).outcome is Outcome.MISS


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
