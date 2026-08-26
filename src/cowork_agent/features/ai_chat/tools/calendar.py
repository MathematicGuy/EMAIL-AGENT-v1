"""The `create_calendar_event` tool: its port, its schema, and its handler."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from .ambiguous_hour import ambiguous_hour_question
from .registry import Tool, ToolResult

CALENDAR_TOOL_NAME = "create_calendar_event"
CALENDAR_TOOL_DESCRIPTION = "create an event or todo on the user's Google Calendar"

# How far ahead an event may be scheduled. Together with the lower bound in
# `_validate_range` this is the guard against a model that writes the wrong
# year -- see the note there.
MAX_DAYS_AHEAD = 365
# A start slightly behind `now` is legitimate (logging a todo for earlier
# today); a start further back than this is a mistake, not an intention.
MAX_DAYS_BEHIND = 1

CALENDAR_TOOL_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "maxLength": 200},
        "start": {
            "type": "string",
            "description": "RFC3339 with offset, or YYYY-MM-DD for all-day",
        },
        "end": {
            "type": "string",
            "description": "RFC3339 with offset, or YYYY-MM-DD for all-day",
        },
        "description": {"type": "string", "maxLength": 2000},
    },
    "required": ["title", "start", "end"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class CalendarEventDraft:
    """One event to create. `start`/`end` are both dates or both datetimes."""

    event_id: str
    title: str
    start: datetime | date
    end: datetime | date
    timezone: str
    description: str | None = None

    @property
    def all_day(self) -> bool:
        return not isinstance(self.start, datetime)


class CalendarError(Exception):
    """The calendar could not be written to. Carries text a model can read."""


class CalendarPort(Protocol):
    """Create one event and return a link to it.

    Deliberately one method: retry semantics, duplicate handling and timezone
    encoding are the adapter's problem, not the caller's. A duplicate
    `event_id` must resolve to the existing event rather than an error -- that
    is what makes a retried chat turn safe.
    """

    async def create_event(self, draft: CalendarEventDraft) -> str: ...


def google_event_id(seed: str) -> str:
    """Derive a valid Google event id from a chat turn's idempotency key.

    Google accepts base32hex only -- lowercase `a`-`v` and `0`-`9`, 5-1024
    characters. `w`, `x`, `y` and `z` are outside the alphabet, which rules out
    an obvious prefix like "cowork"; anything else returns a bare
    400 "Invalid resource id value." See
    `docs/references/google-calendar-api-notes.md` §2.
    """

    digest = base64.b32hexencode(seed.encode()).decode().rstrip("=").lower()
    return f"coagent{digest}"[:1024]


def build_calendar_tool(
    calendar: CalendarPort,
    *,
    idempotency_key: str,
    timezone: str,
    now: datetime,
    user_message: str,
) -> Tool:
    """Bind the calendar tool to one chat turn.

    `idempotency_key`, `now` and `user_message` belong to the turn, not the
    process, so this is built per turn. `name` and `description` are constant,
    which is what lets the same factory feed the classifier prompt.

    `user_message` is what the guard in `ambiguous_hour` reads: an hour the
    user named but did not determine cannot be seen in the filled arguments,
    only in the message they were filled from (PROGRESS.md F5/F7). It is
    required rather than defaulted so that a new call site has to decide;
    passing `""` -- as the classifier's inert tool spec does -- turns the guard
    off, which is only correct where no turn exists to guard.
    """

    tz = ZoneInfo(timezone)

    async def handler(arguments: Mapping[str, object]) -> ToolResult:
        title = str(arguments["title"]).strip()
        if not title:
            return ToolResult(ok=False, text="The event needs a title.")

        parsed = _parse_range(str(arguments["start"]), str(arguments["end"]), tz)
        if isinstance(parsed, str):
            return ToolResult(ok=False, text=parsed)
        start, end = parsed

        problem = _validate_range(start, end, now=now)
        if problem is not None:
            return ToolResult(ok=False, text=problem)

        # Only for a timed event: an all-day event has no hour to get wrong.
        # This reads the message rather than `start` on purpose -- the filler
        # always resolves to *some* hour, and the arguments cannot say whether
        # the user chose it or the model did.
        if isinstance(start, datetime):
            question = ambiguous_hour_question(user_message)
            if question is not None:
                return ToolResult(ok=False, text=question)

        description = arguments.get("description")
        draft = CalendarEventDraft(
            event_id=google_event_id(idempotency_key),
            title=title,
            start=start,
            end=end,
            timezone=timezone,
            description=str(description) if description else None,
        )
        try:
            link = await calendar.create_event(draft)
        except CalendarError as exc:
            return ToolResult(ok=False, text=f"The calendar rejected the event: {exc}")
        return ToolResult(ok=True, text=_confirmation(draft, link))

    return Tool(
        name=CALENDAR_TOOL_NAME,
        description=CALENDAR_TOOL_DESCRIPTION,
        parameters=CALENDAR_TOOL_SCHEMA,
        handler=handler,
    )


def _parse_range(
    raw_start: str, raw_end: str, tz: ZoneInfo
) -> tuple[datetime, datetime] | tuple[date, date] | str:
    """Both values as dates or both as datetimes, or a problem description."""

    start = _parse_moment(raw_start, tz)
    end = _parse_moment(raw_end, tz)
    if start is None or end is None:
        unreadable = raw_start if start is None else raw_end
        return f"Could not read {unreadable!r} as a date or time."
    if isinstance(start, datetime) != isinstance(end, datetime):
        # An all-day start with a timed end is a model that changed its mind
        # halfway through. Guessing which half it meant creates the wrong event.
        return "Start and end must both be dates or both be times."
    return (start, end)


def _parse_moment(raw: str, tz: ZoneInfo) -> datetime | date | None:
    text = raw.strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            return date.fromisoformat(text)
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    # Option A (Wall-clock wins): Re-interpret the wall-clock digits in the
    # calendar's zone (e.g. 02:00 UTC emitted by an LLM becomes 02:00 in Asia/Ho_Chi_Minh).
    # Google accepts an explicit offset and honors it over timeZone, so attaching
    # the target calendar's tzinfo directly ensures the event lands on the exact
    # wall-clock hour the user requested.
    return moment.replace(tzinfo=tz)


def _validate_range(start: datetime | date, end: datetime | date, *, now: datetime) -> str | None:
    if end <= start:
        return "The event ends before it starts."

    today = now.date()
    start_day = start.date() if isinstance(start, datetime) else start
    days_ahead = (start_day - today).days
    # The lower bound is the one that matters. The failure this catches is a
    # model resolving "next January" against the year that just ended, and in
    # August that lands only months in the past -- a symmetric window would
    # wave it through. Writing a past date to a real calendar is the mistake;
    # rejecting it costs a clarifying question.
    if days_ahead < -MAX_DAYS_BEHIND:
        return f"{start_day.isoformat()} is in the past. Confirm the intended date."
    if days_ahead > MAX_DAYS_AHEAD:
        return f"{start_day.isoformat()} is more than a year away. Confirm the intended date."
    return None


def _confirmation(draft: CalendarEventDraft, link: str) -> str:
    when = (
        draft.start.isoformat()
        if draft.all_day
        else draft.start.strftime("%Y-%m-%d %H:%M %Z").strip()
    )
    parts = [f'Created "{draft.title}" on {when}.']
    if link:
        parts.append(link)
    return " ".join(parts)


class InMemoryCalendar:
    """Deterministic `CalendarPort` for tests: no network, no credentials.

    Mirrors the two Google behaviours the handler depends on -- a re-used
    `event_id` resolves to the existing event instead of duplicating it, and a
    created event always has a link.
    """

    def __init__(self, *, fail_with: str | None = None) -> None:
        self.events: dict[str, CalendarEventDraft] = {}
        self._fail_with = fail_with

    async def create_event(self, draft: CalendarEventDraft) -> str:
        if self._fail_with is not None:
            raise CalendarError(self._fail_with)
        self.events.setdefault(draft.event_id, draft)
        return f"https://calendar.google.com/event?eid={draft.event_id}"
