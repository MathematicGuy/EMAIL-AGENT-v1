"""The `list_calendar_events` tool: window rules, ordering, bounding, rendering.

Read-side counterpart to `test_calendar_tool.py`. Several tests here exist to
pin behaviour that deliberately *differs* from the writing tool, so a later
refactor that unifies the two has to break something first.
"""

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


# --- the tool's identity ---------------------------------------------------


def test_the_tool_is_named_and_described_for_a_reader() -> None:
    tool = build_agenda_tool(InMemoryCalendar(), timezone=TIMEZONE)

    assert tool.name == AGENDA_TOOL_NAME
    assert tool.parameters["required"] == ["start", "end"]
    assert tool.parameters["additionalProperties"] is False


# --- what it returns -------------------------------------------------------


@pytest.mark.asyncio
async def test_events_come_back_soonest_first_regardless_of_storage_order() -> None:
    """Ordering is contract. "What is next" cannot be answered by an unordered list."""

    result = await _call(_stocked(), "2026-08-28", "2026-08-30")

    assert result.ok
    assert result.text.index("Gym") < result.text.index("Standup") < result.text.index("Retro")


@pytest.mark.asyncio
async def test_an_empty_window_says_so_rather_than_returning_nothing() -> None:
    """`ok=True` with empty text would read to the reply model as a failure."""

    result = await _call(_stocked(), "2026-09-10", "2026-09-11")

    assert result.ok
    assert "Nothing on the calendar" in result.text


@pytest.mark.asyncio
async def test_an_event_spanning_the_window_boundary_is_included() -> None:
    """Overlap, not containment -- an all-nighter belongs in the next day's agenda."""

    calendar = InMemoryCalendar()
    calendar.events = {"x": _draft("x", "Deploy window", _at(27, 22), _at(28, 4))}

    result = await _call(calendar, "2026-08-28", "2026-08-29")

    assert "Deploy window" in result.text


@pytest.mark.asyncio
async def test_a_truncated_listing_admits_it() -> None:
    """A list that silently stops at the cap reads as the complete answer."""

    calendar = InMemoryCalendar()
    calendar.events = {
        str(index): _draft(str(index), f"Event {index}", _at(28, 0), _at(28, 1))
        for index in range(MAX_EVENTS + 5)
    }

    result = await _call(calendar, "2026-08-28", "2026-08-29")

    assert result.text.count("\n- ") == MAX_EVENTS
    assert f"showing the first {MAX_EVENTS}" in result.text


@pytest.mark.asyncio
async def test_an_all_day_event_is_labelled_rather_than_given_a_fake_hour() -> None:
    calendar = InMemoryCalendar()
    calendar.events = {"h": _draft("h", "Public holiday", date(2026, 9, 2), date(2026, 9, 3))}

    result = await _call(calendar, "2026-09-01", "2026-09-05")

    assert "(all day)" in result.text
    assert "00:00" not in result.text


# --- the window rules, which are not the write rules -----------------------


@pytest.mark.asyncio
async def test_reading_the_past_is_allowed() -> None:
    """The write tool refuses a start more than a day back; this must not.

    "What did I have last week?" is a legitimate question, and the bound it
    would trip exists to catch a model writing the wrong year -- a failure with
    no read-side equivalent.
    """

    calendar = InMemoryCalendar()
    calendar.events = {"o": _draft("o", "Last month's review", _at(1, 10), _at(1, 11))}

    result = await _call(calendar, "2026-08-01", "2026-08-02")

    assert result.ok
    assert "Last month's review" in result.text


@pytest.mark.asyncio
async def test_an_inverted_window_is_refused() -> None:
    result = await _call(_stocked(), "2026-08-30", "2026-08-28")

    assert not result.ok
    assert "ends before it starts" in result.text


@pytest.mark.asyncio
async def test_a_window_wider_than_the_cap_is_refused() -> None:
    """Not a safety bound -- a relevance one. See `MAX_WINDOW_DAYS`."""

    result = await _call(_stocked(), "2026-01-01", "2026-12-31")

    assert not result.ok
    assert str(MAX_WINDOW_DAYS) in result.text


@pytest.mark.asyncio
async def test_a_window_exactly_at_the_cap_is_allowed() -> None:
    edge = (date(2026, 8, 1) + timedelta(days=MAX_WINDOW_DAYS)).isoformat()

    result = await _call(_stocked(), "2026-08-01", edge)

    assert result.ok


@pytest.mark.asyncio
async def test_a_mixed_kind_window_is_refused() -> None:
    """Same rule as the writing tool, and the shared parser is why."""

    result = await _call(_stocked(), "2026-08-28", "2026-08-29T10:00:00+07:00")

    assert not result.ok
    assert "both be dates or both be times" in result.text


@pytest.mark.asyncio
async def test_an_unreadable_bound_is_refused_by_name() -> None:
    result = await _call(_stocked(), "next Friday", "2026-08-29")

    assert not result.ok
    assert "next Friday" in result.text


# --- failure is data -------------------------------------------------------


@pytest.mark.asyncio
async def test_a_calendar_error_comes_back_as_a_result_not_an_exception() -> None:
    result = await _call(
        InMemoryCalendar(fail_with="403 accessNotConfigured"), "2026-08-28", "2026-08-29"
    )

    assert not result.ok
    assert "403 accessNotConfigured" in result.text
    assert "could not be read" in result.text


# --- the two tools share one calendar --------------------------------------


@pytest.mark.asyncio
async def test_an_event_written_by_the_other_tool_is_readable_by_this_one() -> None:
    """One fake for both ports, so this is the same object the deployment uses."""

    calendar = InMemoryCalendar()
    await calendar.create_event(_draft("w", "Tập gym", _at(28, 2), _at(28, 3)))

    result = await _call(calendar, "2026-08-28", "2026-08-29")

    assert "Tập gym" in result.text


@pytest.mark.asyncio
async def test_the_reader_does_not_leak_fields_the_question_did_not_ask_for() -> None:
    """`CalendarEventSummary` carries when and what. Ids and links are not agenda."""

    calendar = InMemoryCalendar()
    await calendar.create_event(
        CalendarEventDraft(
            event_id="coagentsecret",
            title="Standup",
            start=_at(28, 9),
            end=_at(28, 10),
            timezone=TIMEZONE,
            description="internal notes that were never requested",
        )
    )

    result = await _call(calendar, "2026-08-28", "2026-08-29")

    assert "coagentsecret" not in result.text
    assert "internal notes" not in result.text


@pytest.mark.asyncio
async def test_an_early_morning_event_is_on_its_own_day_not_the_previous_one() -> None:
    """A day-shaped window is a day in the calendar's zone, not in UTC.

    06:00+07 is 23:00Z the day before, so resolving `2026-08-28` to midnight
    UTC drops a breakfast meeting out of its own agenda and into yesterday's.
    F6 again -- the same bug the writing tool already had once.
    """

    calendar = InMemoryCalendar()
    calendar.events = {"e": _draft("e", "Early gym", _at(28, 6), _at(28, 7))}

    own_day = await _call(calendar, "2026-08-28", "2026-08-29")
    day_before = await _call(calendar, "2026-08-27", "2026-08-28")

    assert "Early gym" in own_day.text
    assert "Early gym" not in day_before.text
