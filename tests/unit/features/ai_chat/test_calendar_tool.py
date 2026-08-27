import asyncio
import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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
    user_message: str = "",
) -> ToolResult:
    tool = build_calendar_tool(
        calendar or InMemoryCalendar(),
        idempotency_key=idempotency_key,
        timezone=TIMEZONE,
        now=now,
        user_message=user_message,
    )
    return asyncio.run(ToolRegistry([tool]).run(CALENDAR_TOOL_NAME, arguments))


def test_calendar_tool_event_creation_and_timezone_interpretation() -> None:
    calendar = InMemoryCalendar()
    # Timed event creation
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
    assert "Họp team" in result.text and "https://calendar.google.com/event" in result.text
    (draft,) = calendar.events.values()
    assert draft.all_day is False and draft.timezone == TIMEZONE

    # Timezone interpretation without offset
    cal2 = InMemoryCalendar()
    _call(
        {"title": "Standup", "start": "2026-08-26T09:00:00", "end": "2026-08-26T09:15:00"},
        calendar=cal2,
    )
    (d2,) = cal2.events.values()
    assert d2.start.isoformat() == "2026-08-26T09:00:00+07:00"


def test_calendar_tool_all_day_and_duration_defaults() -> None:
    cal = InMemoryCalendar()
    # Date only -> all day
    res_all_day = _call(
        {"title": "Nộp báo cáo", "start": "2026-08-27", "end": "2026-08-28"}, calendar=cal
    )
    assert res_all_day.ok is True
    (d1,) = cal.events.values()
    assert d1.all_day is True

    # Valid timed event
    cal2 = InMemoryCalendar()
    _call(
        {
            "title": "Coffee",
            "start": "2026-08-26T14:00:00+07:00",
            "end": "2026-08-26T14:30:00+07:00",
        },
        calendar=cal2,
    )
    (d2,) = cal2.events.values()
    assert d2.end == d2.start + timedelta(minutes=30)


def test_calendar_tool_guard_validations_and_rejections() -> None:
    # Empty / whitespace title
    assert (
        _call(
            {
                "title": "   ",
                "start": "2026-08-26T14:00:00+07:00",
                "end": "2026-08-26T14:30:00+07:00",
            }
        ).ok
        is False
    )
    # Unparseable start
    assert (
        _call({"title": "Bad", "start": "not a date", "end": "2026-08-26T14:30:00+07:00"}).ok
        is False
    )
    # End before start
    assert (
        _call(
            {
                "title": "Inverted",
                "start": "2026-08-26T15:00:00+07:00",
                "end": "2026-08-26T14:00:00+07:00",
            }
        ).ok
        is False
    )


def test_google_event_id_determinism_and_rfc_compliance() -> None:
    event_id = google_event_id("idem-001")
    assert 5 <= len(event_id) <= 1024
    assert re.fullmatch(r"[a-v0-9]+", event_id)
    # Deterministic
    assert google_event_id("idem-001") == event_id
    # Distinct inputs get distinct IDs
    assert google_event_id("idem-002") != event_id
