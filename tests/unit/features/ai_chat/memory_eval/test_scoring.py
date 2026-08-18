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


def test_expect_all_partially_present_is_a_miss_naming_what_is_missing() -> None:
    probe = _probe(expect_any=(), expect_all=("Marcus", "Thursday"))
    result = score("Marcus is unavailable.", probe)
    assert result.outcome is Outcome.MISS
    assert "Thursday" in result.why


def test_expect_all_fully_present_is_a_pass() -> None:
    probe = _probe(expect_any=(), expect_all=("Marcus", "Thursday"))
    assert score("Marcus is out on Thursday.", probe).outcome is Outcome.PASS


def test_superseded_answer_with_no_expectation_declared_is_stale() -> None:
    probe = _probe(expect_any=(), expect_all=("Marcus",), stale_any=("Tuesday",))
    result = score("Marcus said Tuesday.", probe)
    assert result.outcome is Outcome.STALE


def test_empty_reply_is_a_miss_not_a_crash() -> None:
    assert score("", _probe()).outcome is Outcome.MISS
