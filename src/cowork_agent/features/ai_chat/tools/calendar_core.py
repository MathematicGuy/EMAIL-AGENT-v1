"""Vocabulary shared by the calendar tools that read and the one that writes.

This module exists because the second calendar tool arrived. Before it, every
name here lived in `calendar.py` and there was no reason to think any of it was
shared -- one tool cannot tell its own vocabulary from the domain's. `agenda.py`
needs the same range parsing and the same error type, and importing them from
the writing tool would have made a read depend on a write.

What is *not* here is as deliberate: `_validate_range` stayed in `calendar.py`
because its bounds encode what is safe to write, and the two tools genuinely
disagree about that (PROGRESS.md F9).
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo


class CalendarError(Exception):
    """The calendar could not be reached. Carries text a model can read."""


def parse_range(
    raw_start: str, raw_end: str, tz: ZoneInfo
) -> tuple[datetime, datetime] | tuple[date, date] | str:
    """Both values as dates or both as datetimes, or a problem description.

    The mixed-kind rule is a property of the strings rather than of the
    operation, which is why both tools can share it.
    """

    start = parse_moment(raw_start, tz)
    end = parse_moment(raw_end, tz)
    if start is None or end is None:
        unreadable = raw_start if start is None else raw_end
        return f"Could not read {unreadable!r} as a date or time."
    if isinstance(start, datetime) != isinstance(end, datetime):
        # An all-day start with a timed end is a model that changed its mind
        # halfway through. Guessing which half it meant creates the wrong event.
        return "Start and end must both be dates or both be times."
    return (start, end)


def parse_moment(raw: str, tz: ZoneInfo) -> datetime | date | None:
    """One bound as a date or an aware datetime, or None if it is unreadable."""

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
    # calendar's zone (e.g. 02:00 UTC emitted by an LLM becomes 02:00 in
    # Asia/Ho_Chi_Minh). Google accepts an explicit offset and honors it over
    # timeZone, so attaching the target calendar's tzinfo directly ensures the
    # event lands on the exact wall-clock hour the user requested.
    return moment.replace(tzinfo=tz)
