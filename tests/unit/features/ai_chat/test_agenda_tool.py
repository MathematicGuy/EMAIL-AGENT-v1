from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from cowork_agent.features.ai_chat.tools import (
    AGENDA_TOOL_NAME,
    CalendarEventDraft,
    InMemoryCalendar,
    ToolResult,
    build_agenda_tool,
)
from cowork_agent.features.ai_chat.tools.agenda import MAX_EVENTS, MAX_WINDOW_DAYS

TIMEZONE = "Asia/Ho_Chi_Minh"
TZ = ZoneInfo(TIMEZONE)


def _at(day: int, hour: int) -> datetime:
    return datetime(2026, 8, day, hour, tzinfo=TZ)


def _draft(event_id: str, title: str, start: datetime | date, end: datetime | date):
    return CalendarEventDraft(
        event_id=event_id, title=title, start=start, end=end, timezone=TIMEZONE
    )


async def _call(calendar: InMemoryCalendar, start: str, end: str) -> ToolResult:
    tool = build_agenda_tool(calendar, timezone=TIMEZONE)
    return await tool.handler({"start": start, "end": end})


def _stocked() -> InMemoryCalendar:
    calendar = InMemoryCalendar()
    calendar.events = {
        "b": _draft("b", "Standup", _at(28, 9), _at(28, 10)),
        "a": _draft("a", "Gym", _at(28, 6), _at(28, 7)),
        "c": _draft("c", "Retro", _at(29, 15), _at(29, 16)),
    }
    return calendar


@pytest.mark.asyncio
async def test_agenda_tool_spec_and_ordering() -> None:
    tool = build_agenda_tool(InMemoryCalendar(), timezone=TIMEZONE)
    assert tool.name == AGENDA_TOOL_NAME
    assert tool.parameters["required"] == ["start", "end"]

    # Soonest first ordering
    result = await _call(_stocked(), "2026-08-28", "2026-08-30")
    assert result.ok is True
    assert result.text.index("Gym") < result.text.index("Standup") < result.text.index("Retro")

    # Empty window message
    empty_res = await _call(_stocked(), "2026-09-10", "2026-09-11")
    assert "Nothing on the calendar" in empty_res.text


@pytest.mark.asyncio
async def test_agenda_tool_bounds_truncation_and_overlap() -> None:
    # Overlap included
    cal = InMemoryCalendar()
    cal.events = {"x": _draft("x", "Deploy window", _at(27, 22), _at(28, 4))}
    res = await _call(cal, "2026-08-28", "2026-08-29")
    assert "Deploy window" in res.text

    # Max events truncation
    cal_many = InMemoryCalendar()
    for i in range(MAX_EVENTS + 5):
        cal_many.events[f"e{i}"] = _draft(
            f"e{i}",
            f"Meeting {i}",
            _at(28, 8) + timedelta(minutes=i * 10),
            _at(28, 8) + timedelta(minutes=i * 10 + 5),
        )
    res_trunc = await _call(cal_many, "2026-08-28", "2026-08-29")
    assert f"showing the first {MAX_EVENTS}" in res_trunc.text


@pytest.mark.asyncio
async def test_agenda_tool_guards_and_rejections() -> None:
    cal = InMemoryCalendar()
    # Inverted window
    assert (await _call(cal, "2026-08-30", "2026-08-28")).ok is False
    # Window exceeds MAX_WINDOW_DAYS
    assert (await _call(cal, "2026-08-01", f"2026-08-{MAX_WINDOW_DAYS + 5}")).ok is False
    # Invalid date string
    assert (await _call(cal, "not a date", "2026-08-28")).ok is False
