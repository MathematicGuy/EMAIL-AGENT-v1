"""Strict loader for the calendar tool-use QA set.

Three callers read this fixture -- the offline pytest runner, the live scorer,
and the HTML register. A raw dict in three places is three copies of the schema
and three ways to drift, so the parsing lives here once and the callers see
frozen dataclasses.

Validation is deliberately unforgiving. A QA set that silently loads a case with
a missing expectation reports a pass it never earned.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from cowork_agent.domain.chat_contracts import ChatRoute, IntentReasonCode

# The reason codes `finalize_route` appends itself. A classifier cannot emit
# them, so they are the only codes a case may assert -- classifier-owned codes
# vary by model and asserting them would make the set model-specific.
SERVER_OWNED_REASON_CODES = frozenset(
    {
        IntentReasonCode.NO_READY_DOCUMENTS,
        IntentReasonCode.TOOL_REQUESTED_BUT_DISABLED,
        IntentReasonCode.TOOL_NOT_AVAILABLE,
    }
)


class ToolIntentTier(StrEnum):
    """Risk tier, ordered by what the failure costs the user."""

    HAPPY_PATH = "happy_path"
    SILENT_WRONG_WRITE = "silent_wrong_write"
    FALSE_POSITIVE_WRITE = "false_positive_write"
    COMPOUND_REQUEST = "compound_request"
    UNSUPPORTED_VERB = "unsupported_verb"
    DUPLICATE_WRITE = "duplicate_write"
    CAPABILITY_GATE = "capability_gate"
    PROMPT_INJECTION = "prompt_injection"


@dataclass(frozen=True, slots=True)
class FixtureTurn:
    user: str
    assistant: str


@dataclass(frozen=True, slots=True)
class ToolIntentContext:
    """The capability configuration this case is evaluated under."""

    tool_axis_enabled: bool
    available_tools: frozenset[str]
    now: datetime


@dataclass(frozen=True, slots=True)
class ClassifierLabels:
    """What the classifier is taken to have emitted. Input, not expectation."""

    needs_rag: bool
    needs_tool: bool
    needs_clarification: bool
    tool_name: str | None


@dataclass(frozen=True, slots=True)
class ExpectedToolOutcome:
    """What the calendar should hold once the turn is over."""

    ok: bool
    events_created: int
    expect_start: datetime | None = None
    expect_end: datetime | None = None
    idempotency_key: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class ToolIntentCase:
    id: str
    tier: ToolIntentTier
    story: str
    current_message: str
    recent_turns: tuple[FixtureTurn, ...]
    ready_document_titles: tuple[str, ...]
    context: ToolIntentContext
    classifier_labels: ClassifierLabels
    expected_final_route: ChatRoute
    expected_appended_reason_codes: tuple[IntentReasonCode, ...]
    expected_tool_outcome: ExpectedToolOutcome | None
    why_it_matters: str


@dataclass(frozen=True, slots=True)
class ToolIntentFixture:
    """The whole set plus the clock every relative date resolves against."""

    now: datetime
    tool_name: str
    cases: tuple[ToolIntentCase, ...]


DEFAULT_FIXTURE_PATH = Path(__file__).with_name("tool_intent_qa.json")

_CASE_FIELDS = {
    "id",
    "tier",
    "story",
    "current_message",
    "recent_turns",
    "ready_document_titles",
    "context",
    "classifier_labels",
    "expected_final_route",
    "expected_appended_reason_codes",
    "expected_tool_outcome",
    "why_it_matters",
}
_OUTCOME_REQUIRED = {"ok", "events_created"}
_OUTCOME_OPTIONAL = {"expect_start", "expect_end", "idempotency_key", "note"}


def load_tool_intent_cases(path: Path | None = None) -> ToolIntentFixture:
    """Parse and validate the QA set, or raise saying which case is wrong."""

    fixture_path = path or DEFAULT_FIXTURE_PATH
    try:
        raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid tool intent fixture JSON: {error}") from error
    if not isinstance(raw, dict) or not isinstance(raw.get("cases"), list):
        raise ValueError("tool intent fixture must be an object with a `cases` array")
    cases = tuple(_parse_case(item, index) for index, item in enumerate(raw["cases"]))
    ids = tuple(case.id for case in cases)
    if len(set(ids)) != len(ids):
        raise ValueError("tool intent fixture ids must be unique")
    if not cases:
        raise ValueError("tool intent fixture requires at least one case")
    return ToolIntentFixture(
        now=_moment(raw.get("now"), "now"),
        tool_name=_text(raw.get("tool_name"), "tool_name"),
        cases=cases,
    )


def _parse_case(value: Any, index: int) -> ToolIntentCase:
    where = f"case[{index}]"
    if not isinstance(value, dict):
        raise TypeError(f"{where} must be an object")
    if set(value) != _CASE_FIELDS:
        missing = sorted(_CASE_FIELDS - set(value))
        extra = sorted(set(value) - _CASE_FIELDS)
        raise ValueError(f"{where} schema mismatch (missing={missing}, unexpected={extra})")
    where = _text(value["id"], f"{where}.id")
    turns = value["recent_turns"]
    titles = value["ready_document_titles"]
    if not isinstance(turns, list) or not isinstance(titles, list):
        raise TypeError(f"{where}: recent_turns and ready_document_titles must be arrays")
    route = ChatRoute(_text(value["expected_final_route"], f"{where}.expected_final_route"))
    outcome = _parse_outcome(value["expected_tool_outcome"], where)
    # An expectation about the calendar only means something on a turn that
    # reaches the calendar. Either half without the other is a case that would
    # pass without testing anything.
    if (outcome is not None) != (route is ChatRoute.TOOL):
        raise ValueError(
            f"{where}: expected_tool_outcome must be present exactly when the route is `tool`"
        )
    return ToolIntentCase(
        id=where,
        tier=ToolIntentTier(_text(value["tier"], f"{where}.tier")),
        story=_text(value["story"], f"{where}.story"),
        current_message=_text(value["current_message"], f"{where}.current_message"),
        recent_turns=tuple(_parse_turn(turn, where) for turn in turns),
        ready_document_titles=tuple(_text(title, f"{where}.title") for title in titles),
        context=_parse_context(value["context"], where),
        classifier_labels=_parse_labels(value["classifier_labels"], where),
        expected_final_route=route,
        expected_appended_reason_codes=_parse_reason_codes(
            value["expected_appended_reason_codes"], where
        ),
        expected_tool_outcome=outcome,
        why_it_matters=_text(value["why_it_matters"], f"{where}.why_it_matters"),
    )


def _parse_turn(value: Any, where: str) -> FixtureTurn:
    if not isinstance(value, dict) or set(value) != {"user", "assistant"}:
        raise ValueError(f"{where}.recent_turns item is invalid")
    return FixtureTurn(
        _text(value["user"], f"{where}.turn.user"),
        _text(value["assistant"], f"{where}.turn.assistant"),
    )


def _parse_context(value: Any, where: str) -> ToolIntentContext:
    if not isinstance(value, dict) or set(value) != {"tool_axis_enabled", "available_tools", "now"}:
        raise ValueError(f"{where}.context fields do not match the schema")
    tools = value["available_tools"]
    if not isinstance(tools, list):
        raise TypeError(f"{where}.context.available_tools must be an array")
    return ToolIntentContext(
        tool_axis_enabled=_boolean(value["tool_axis_enabled"], f"{where}.context"),
        available_tools=frozenset(_text(name, f"{where}.context.tool") for name in tools),
        now=_moment(value["now"], f"{where}.context.now"),
    )


def _parse_labels(value: Any, where: str) -> ClassifierLabels:
    expected = {"needs_rag", "needs_tool", "needs_clarification", "tool_name"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"{where}.classifier_labels fields do not match the schema")
    name = value["tool_name"]
    if name is not None and not isinstance(name, str):
        raise TypeError(f"{where}.classifier_labels.tool_name must be a string or null")
    return ClassifierLabels(
        needs_rag=_boolean(value["needs_rag"], where),
        needs_tool=_boolean(value["needs_tool"], where),
        needs_clarification=_boolean(value["needs_clarification"], where),
        tool_name=name,
    )


def _parse_reason_codes(value: Any, where: str) -> tuple[IntentReasonCode, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{where}.expected_appended_reason_codes must be an array")
    codes = tuple(IntentReasonCode(_text(code, f"{where}.reason_code")) for code in value)
    unowned = [code for code in codes if code not in SERVER_OWNED_REASON_CODES]
    if unowned:
        raise ValueError(
            f"{where}: {unowned} is classifier-owned; only server-appended codes may be asserted"
        )
    return codes


def _parse_outcome(value: Any, where: str) -> ExpectedToolOutcome | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError(f"{where}.expected_tool_outcome must be an object or null")
    keys = set(value)
    if not _OUTCOME_REQUIRED <= keys or not keys <= _OUTCOME_REQUIRED | _OUTCOME_OPTIONAL:
        raise ValueError(f"{where}.expected_tool_outcome fields do not match the schema")
    count = value["events_created"]
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise ValueError(f"{where}.expected_tool_outcome.events_created must be a count")
    start = value.get("expect_start")
    end = value.get("expect_end")
    return ExpectedToolOutcome(
        ok=_boolean(value["ok"], f"{where}.expected_tool_outcome"),
        events_created=count,
        expect_start=_moment(start, f"{where}.expect_start") if start is not None else None,
        expect_end=_moment(end, f"{where}.expect_end") if end is not None else None,
        idempotency_key=value.get("idempotency_key"),
        note=value.get("note"),
    )


def _moment(value: Any, where: str) -> datetime:
    text = _text(value, where)
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"{where} must be an RFC3339 timestamp: {error}") from error
    if moment.tzinfo is None:
        # A naive timestamp in a fixture about timezone arithmetic is the bug
        # the fixture exists to catch.
        raise ValueError(f"{where} must carry a UTC offset")
    return moment


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} must be a nonempty string")
    return value


def _boolean(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{where} must be a boolean")
    return value
