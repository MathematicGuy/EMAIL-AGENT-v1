import asyncio
import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from cowork_agent.features.ai_chat.tools import (
    CALENDAR_TOOL_NAME,
    InMemoryCalendar,
    ToolRegistry,
    ToolResult,
    build_calendar_tool,
    google_event_id,
)

TIMEZONE = "Asia/Ho_Chi_Minh"
TZ = ZoneInfo(TIMEZONE)
NOW = datetime(2026, 8, 25, 10, 0, tzinfo=TZ)


def _call(
    arguments: Mapping[str, object],
    *,
    calendar: InMemoryCalendar | None = None,
    idempotency_key: str = "turn-1",
    now: datetime = NOW,
) -> ToolResult:
    tool = build_calendar_tool(
        calendar or InMemoryCalendar(),
        idempotency_key=idempotency_key,
        timezone=TIMEZONE,
        now=now,
    )
    return asyncio.run(ToolRegistry([tool]).run(CALENDAR_TOOL_NAME, arguments))


def test_a_timed_event_is_created_and_confirmed_with_a_link() -> None:
    calendar = InMemoryCalendar()

    result = _call(
        {
            "title": "Họp team",
            "start": "2026-08-26T15:00:00+07:00",
            "end": "2026-08-26T15:30:00+07:00",
            "description": "weekly sync",
        },
        calendar=calendar,
    )

    assert result.ok is True
    assert "Họp team" in result.text
    assert "2026-08-26 15:00" in result.text
    assert "https://calendar.google.com/event" in result.text

    (draft,) = calendar.events.values()
    assert draft.all_day is False
    assert draft.description == "weekly sync"
    assert draft.timezone == TIMEZONE


def test_a_date_only_range_becomes_an_all_day_event() -> None:
    calendar = InMemoryCalendar()

    result = _call(
        {"title": "Nộp báo cáo", "start": "2026-08-27", "end": "2026-08-28"}, calendar=calendar
    )

    assert result.ok is True
    (draft,) = calendar.events.values()
    assert draft.all_day is True


def test_a_start_without_an_offset_is_read_in_the_users_timezone() -> None:
    calendar = InMemoryCalendar()

    _call(
        {"title": "Standup", "start": "2026-08-26T09:00:00", "end": "2026-08-26T09:15:00"},
        calendar=calendar,
    )

    (draft,) = calendar.events.values()
    assert draft.start.utcoffset() == timedelta(hours=7)


def test_mixing_a_date_with_a_time_is_rejected_rather_than_guessed() -> None:
    result = _call(
        {"title": "Ambiguous", "start": "2026-08-27", "end": "2026-08-27T10:00:00+07:00"}
    )

    assert result.ok is False
    assert "both be dates or both be times" in result.text


@pytest.mark.parametrize("raw", ["tomorrow", "26/08/2026", ""])
def test_unparseable_bounds_fail_closed(raw: str) -> None:
    result = _call({"title": "Bad", "start": raw, "end": "2026-08-26T10:00:00+07:00"})

    assert result.ok is False
    assert "Could not read" in result.text


def test_an_end_before_the_start_is_rejected() -> None:
    result = _call(
        {
            "title": "Backwards",
            "start": "2026-08-26T15:00:00+07:00",
            "end": "2026-08-26T14:00:00+07:00",
        }
    )

    assert result.ok is False
    assert "ends before it starts" in result.text


def test_a_zero_length_event_is_rejected() -> None:
    moment = "2026-08-26T15:00:00+07:00"

    assert _call({"title": "Instant", "start": moment, "end": moment}).ok is False


def test_the_january_that_already_passed_is_rejected() -> None:
    """The year-rollover failure: 'next January' resolved against the wrong year."""

    result = _call(
        {
            "title": "Kickoff",
            "start": "2026-01-05T09:00:00+07:00",
            "end": "2026-01-05T10:00:00+07:00",
        }
    )

    assert result.ok is False
    assert "in the past" in result.text


def test_an_event_more_than_a_year_ahead_is_rejected() -> None:
    result = _call(
        {
            "title": "Far future",
            "start": "2027-12-01T09:00:00+07:00",
            "end": "2027-12-01T10:00:00+07:00",
        }
    )

    assert result.ok is False
    assert "more than a year away" in result.text


def test_earlier_today_is_still_allowed() -> None:
    result = _call(
        {
            "title": "Logged after the fact",
            "start": "2026-08-25T08:00:00+07:00",
            "end": "2026-08-25T08:30:00+07:00",
        }
    )

    assert result.ok is True


def test_a_blank_title_is_rejected() -> None:
    result = _call(
        {
            "title": "   ",
            "start": "2026-08-26T15:00:00+07:00",
            "end": "2026-08-26T15:30:00+07:00",
        }
    )

    assert result.ok is False
    assert "needs a title" in result.text


def test_a_missing_title_is_caught_by_the_schema_before_the_handler() -> None:
    result = _call({"start": "2026-08-26T15:00:00+07:00", "end": "2026-08-26T15:30:00+07:00"})

    assert result.ok is False
    assert "missing required title" in result.text


def test_a_retried_turn_reuses_the_event_id_instead_of_duplicating() -> None:
    calendar = InMemoryCalendar()
    arguments = {
        "title": "Họp team",
        "start": "2026-08-26T15:00:00+07:00",
        "end": "2026-08-26T15:30:00+07:00",
    }

    first = _call(arguments, calendar=calendar, idempotency_key="turn-7")
    second = _call(arguments, calendar=calendar, idempotency_key="turn-7")

    assert first.ok is True
    assert second == first
    assert len(calendar.events) == 1


def test_different_turns_create_different_events() -> None:
    calendar = InMemoryCalendar()
    arguments = {
        "title": "Họp team",
        "start": "2026-08-26T15:00:00+07:00",
        "end": "2026-08-26T15:30:00+07:00",
    }

    _call(arguments, calendar=calendar, idempotency_key="turn-7")
    _call(arguments, calendar=calendar, idempotency_key="turn-8")

    assert len(calendar.events) == 2


def test_a_calendar_failure_is_reported_not_raised() -> None:
    result = _call(
        {
            "title": "Họp team",
            "start": "2026-08-26T15:00:00+07:00",
            "end": "2026-08-26T15:30:00+07:00",
        },
        calendar=InMemoryCalendar(fail_with="403 accessNotConfigured"),
    )

    assert result.ok is False
    assert "403 accessNotConfigured" in result.text


@pytest.mark.parametrize("seed", ["turn-7", "a", "cowork-turn-xyz", "тест", "1" * 500])
def test_derived_event_ids_stay_inside_googles_alphabet(seed: str) -> None:
    """Google accepts base32hex only -- `w`, `x`, `y` and `z` are illegal."""

    event_id = google_event_id(seed)

    assert re.fullmatch(r"[a-v0-9]{5,1024}", event_id), event_id


def test_the_tool_is_named_and_described_for_the_classifier() -> None:
    tool = build_calendar_tool(
        InMemoryCalendar(), idempotency_key="turn-1", timezone=TIMEZONE, now=NOW
    )

    assert tool.name == CALENDAR_TOOL_NAME
    assert "calendar" in tool.description.lower()
