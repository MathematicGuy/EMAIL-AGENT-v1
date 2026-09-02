"""The `list_calendar_events` tool: its port, its schema, and its handler.

The second executable tool, and the one SPEC-chat-tools-registry §10 item 2
asked for: one adapter is a hypothetical seam, two is a real one. It reads
rather than writes, deliberately -- a second writing tool would have re-used
every shape unchanged and proven nothing. What it does not need is as
informative as what it does; see `docs/evaluations/CHAT/PROGRESS.md` F9.

It needs no new consent. The grant is already
`https://www.googleapis.com/auth/calendar`, which covers reads, so nothing in
`SPEC-per-user-google-calendar-oauth` §3's J1-J7 moves.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from .calendar_core import CalendarError, parse_range
from .registry import Tool, ToolResult

AGENDA_TOOL_NAME = "list_calendar_events"
AGENDA_TOOL_DESCRIPTION = "look up what is already on the user's Google Calendar in a date range"

# How wide a window may be. Not a safety bound -- a read cannot damage anything
# -- but a relevance one: a model that answers "what's on tomorrow?" with a year
# of events has answered a different question, and the reply model then has to
# summarise a wall of text it was never asked about.
MAX_WINDOW_DAYS = 62
# How many events come back. Google pages beyond this; a chat reply cannot use
# them. A truncated answer says so rather than looking complete -- an agenda
# that silently omits the meeting you asked about is worse than one that admits
# it stopped counting.
MAX_EVENTS = 20

AGENDA_TOOL_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "properties": {
        "start": {
            "type": "string",
            "description": "start of the window, RFC3339 with offset or YYYY-MM-DD",
        },
        "end": {
            "type": "string",
            "description": "end of the window, RFC3339 with offset or YYYY-MM-DD",
        },
    },
    "required": ["start", "end"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class CalendarEventSummary:
    """One event as a reader needs it: when, what, and nothing else.

    No id, no link, no description, no attendees. This is the answer to "what
    is on my calendar", and every field not here is a field the reply model
    could leak into a sentence the user did not ask for.
    """

    title: str
    start: datetime | date
    end: datetime | date

    @property
    def all_day(self) -> bool:
        return not isinstance(self.start, datetime)


@dataclass(frozen=True, slots=True)
class CalendarWindow:
    """A bounded, half-open range to read. Both bounds are the same kind."""

    start: datetime | date
    end: datetime | date
    timezone: str
    limit: int = MAX_EVENTS


class CalendarQueryPort(Protocol):
    """Read events in a window, soonest first, at most `window.limit` of them.

    Separate from `CalendarPort` rather than added to it: a tool that only
    reads should not have to be handed something that can write, and a fake for
    one should not have to implement the other. One adapter happens to satisfy
    both, which is a fact about Google's API rather than about the interface.

    Ordering is part of the contract, not an implementation detail -- "what is
    next" is the question being asked, and an unordered answer cannot serve it.
    """

    async def list_events(self, window: CalendarWindow) -> tuple[CalendarEventSummary, ...]: ...


def build_agenda_tool(calendar: CalendarQueryPort, *, timezone: str) -> Tool:
    """Bind the agenda tool.

    One argument where `build_calendar_tool` takes four, and the three it does
    not take are the interesting part. `idempotency_key` has nothing to make
    idempotent, since a repeated read is not a second event. `user_message` has
    no guard to feed, because the guard it feeds protects a write. `now` is
    needed to *fill* this tool's arguments -- "next week" is arithmetic -- but
    the filler already holds it; the handler validates a window that is already
    absolute.

    So this tool is not bound to a turn at all, only to a grant. That is a
    finding about `ToolTurnContext`, not about this tool (PROGRESS.md F9).
    """

    tz = ZoneInfo(timezone)

    async def handler(arguments: Mapping[str, object]) -> ToolResult:
        parsed = parse_range(str(arguments["start"]), str(arguments["end"]), tz)
        if isinstance(parsed, str):
            return ToolResult(ok=False, text=parsed)
        start, end = parsed

        problem = _validate_window(start, end)
        if problem is not None:
            return ToolResult(ok=False, text=problem)

        window = CalendarWindow(start=start, end=end, timezone=timezone, limit=MAX_EVENTS)
        try:
            events = await calendar.list_events(window)
        except CalendarError as exc:
            return ToolResult(ok=False, text=f"The calendar could not be read: {exc}")
        return ToolResult(ok=True, text=_agenda(events, window))

    return Tool(
        name=AGENDA_TOOL_NAME,
        description=AGENDA_TOOL_DESCRIPTION,
        parameters=AGENDA_TOOL_SCHEMA,
        handler=handler,
    )


def _validate_window(start: datetime | date, end: datetime | date) -> str | None:
    """The read-side range rules, which are not the write-side ones.

    There is no lower bound here on purpose. `_validate_range` refuses a start
    more than a day behind `now` because writing to a past date is the
    year-rollover bug; *reading* a past date is what "what did I have last
    week?" means. Copying that bound across would have refused a legitimate
    question in the name of a guard that has nothing to protect here.
    """

    if end <= start:
        return "The window ends before it starts."
    start_day = start.date() if isinstance(start, datetime) else start
    end_day = end.date() if isinstance(end, datetime) else end
    if (end_day - start_day).days > MAX_WINDOW_DAYS:
        return (
            f"That window is longer than {MAX_WINDOW_DAYS} days. "
            "Narrow it to the period actually being asked about."
        )
    return None


def _agenda(events: Sequence[CalendarEventSummary], window: CalendarWindow) -> str:
    """The events as text, because `ToolResult` carries text and nothing else.

    That is not a limitation being worked around: both consumers of a
    `ToolResult` are models reading a string -- the reply model today, a ReAct
    loop later -- so a structured field would have to be serialised for either
    of them anyway. What the second tool did reveal is that the *rendering* has
    no home: this function is one tool's private idea of a list, and a third
    tool returning a list will write its own (PROGRESS.md F9).
    """

    if not events:
        return f"Nothing on the calendar between {_day(window.start)} and {_day(window.end)}."
    lines = [f"{len(events)} event(s) between {_day(window.start)} and {_day(window.end)}:"]
    lines.extend(f"- {_when(event)} {event.title}" for event in events)
    if len(events) >= window.limit:
        # Never a bare truncation. A list that stops at the limit and does not
        # say so reads as the complete answer.
        lines.append(f"(showing the first {window.limit}; there may be more)")
    return "\n".join(lines)


def _when(event: CalendarEventSummary) -> str:
    if event.all_day:
        return f"{event.start.isoformat()} (all day)"
    return event.start.strftime("%Y-%m-%d %H:%M")


def _day(moment: datetime | date) -> str:
    return (moment.date() if isinstance(moment, datetime) else moment).isoformat()
