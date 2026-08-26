"""The loader's rejections, which are what keep a silently-broken case out."""

import json
from collections import Counter
from pathlib import Path

import pytest

from tests.fixtures.tool_intent.loader import (
    SERVER_OWNED_REASON_CODES,
    ToolIntentTier,
    load_tool_intent_cases,
)


def _fixture() -> dict:
    return json.loads(
        (
            Path(__file__).resolve().parents[2] / "fixtures" / "tool_intent" / "tool_intent_qa.json"
        ).read_text(encoding="utf-8")
    )


def _written(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "tool_intent_qa.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_the_shipped_set_loads_and_covers_every_tier() -> None:
    fixture = load_tool_intent_cases()

    assert len(fixture.cases) == 25
    assert fixture.tool_name == "create_calendar_event"
    # A Wednesday. Every relative date in the set resolves against it, so a
    # changed anchor silently changes what "thứ Sáu" means.
    assert fixture.now.isoformat() == "2026-08-26T09:00:00+07:00"
    assert fixture.now.weekday() == 2
    assert {case.tier for case in fixture.cases} == set(ToolIntentTier)


def test_the_expensive_tiers_carry_most_of_the_set() -> None:
    """The 20% beyond the happy path is where user trust is actually decided."""

    counts = Counter(case.tier for case in load_tool_intent_cases().cases)
    expensive = (
        counts[ToolIntentTier.SILENT_WRONG_WRITE]
        + counts[ToolIntentTier.FALSE_POSITIVE_WRITE]
        + counts[ToolIntentTier.UNSUPPORTED_VERB]
    )

    assert expensive > counts[ToolIntentTier.HAPPY_PATH]


def test_every_case_anchors_to_the_set_wide_clock() -> None:
    fixture = load_tool_intent_cases()

    assert all(case.context.now == fixture.now for case in fixture.cases)


def test_a_case_missing_a_field_is_rejected(tmp_path: Path) -> None:
    payload = _fixture()
    del payload["cases"][0]["why_it_matters"]

    with pytest.raises(ValueError, match="schema mismatch"):
        load_tool_intent_cases(_written(tmp_path, payload))


def test_an_unknown_tier_is_rejected(tmp_path: Path) -> None:
    payload = _fixture()
    payload["cases"][0]["tier"] = "mildly_annoying"

    with pytest.raises(ValueError):
        load_tool_intent_cases(_written(tmp_path, payload))


def test_a_tool_outcome_without_a_tool_route_is_rejected(tmp_path: Path) -> None:
    """An expectation about the calendar on a turn that never reaches it passes
    without testing anything."""

    payload = _fixture()
    payload["cases"][0]["expected_final_route"] = "chat"

    with pytest.raises(ValueError, match="exactly when the route is"):
        load_tool_intent_cases(_written(tmp_path, payload))


def test_a_tool_route_without_a_tool_outcome_is_rejected(tmp_path: Path) -> None:
    payload = _fixture()
    payload["cases"][0]["expected_tool_outcome"] = None

    with pytest.raises(ValueError, match="exactly when the route is"):
        load_tool_intent_cases(_written(tmp_path, payload))


def test_a_classifier_owned_reason_code_cannot_be_asserted(tmp_path: Path) -> None:
    """Classifier codes vary by model; asserting one makes the set model-specific."""

    payload = _fixture()
    payload["cases"][0]["expected_appended_reason_codes"] = ["general_chat"]

    with pytest.raises(ValueError, match="classifier-owned"):
        load_tool_intent_cases(_written(tmp_path, payload))


def test_the_shipped_set_only_asserts_server_owned_codes() -> None:
    for case in load_tool_intent_cases().cases:
        assert set(case.expected_appended_reason_codes) <= SERVER_OWNED_REASON_CODES


def test_a_naive_timestamp_is_rejected(tmp_path: Path) -> None:
    """A fixture about timezone arithmetic must not contain a zoneless moment."""

    payload = _fixture()
    payload["cases"][0]["expected_tool_outcome"]["expect_start"] = "2026-08-28T02:00:00"

    with pytest.raises(ValueError, match="UTC offset"):
        load_tool_intent_cases(_written(tmp_path, payload))


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    payload = _fixture()
    payload["cases"][1]["id"] = payload["cases"][0]["id"]

    with pytest.raises(ValueError, match="ids must be unique"):
        load_tool_intent_cases(_written(tmp_path, payload))
