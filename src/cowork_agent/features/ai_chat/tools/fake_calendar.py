"""One in-memory calendar satisfying both calendar ports, for tests.

Its own module because it is the only thing that needs both the writing
vocabulary and the reading one, and putting it in either tool's module would
have made that tool import the other.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from .agenda import CalendarEventSummary, CalendarWindow
from .calendar import CalendarEventDraft
from .calendar_core import CalendarError


class InMemoryCalendar:
    """Deterministic `CalendarPort` and `CalendarQueryPort`: no network, no keys.

    Mirrors the Google behaviours the handlers depend on -- a re-used
    `event_id` resolves to the existing event instead of duplicating it, a
    created event always has a link, and a listing is overlap-based, ordered
    soonest first, and bounded by the window's limit.

    One fake for both ports, matching `GoogleCalendar`. A test that creates an
    event and then reads it back exercises the same object the real deployment
    does; two separate fakes could drift into agreeing with each other and with
    nothing else.
    """

    def __init__(self, *, fail_with: str | None = None) -> None:
        self.events: dict[str, CalendarEventDraft] = {}
        self._fail_with = fail_with

    async def create_event(self, draft: CalendarEventDraft) -> str:
        if self._fail_with is not None:
            raise CalendarError(self._fail_with)
        self.events.setdefault(draft.event_id, draft)
        return f"https://calendar.google.com/event?eid={draft.event_id}"

    async def list_events(self, window: CalendarWindow) -> tuple[CalendarEventSummary, ...]:
        if self._fail_with is not None:
            raise CalendarError(self._fail_with)
        matched = [
            (_instant(draft.start, draft.timezone), draft)
            for draft in self.events.values()
            if _overlaps(draft, window)
        ]
        matched.sort(key=lambda pair: pair[0])
        return tuple(
            CalendarEventSummary(title=draft.title, start=draft.start, end=draft.end)
            for _, draft in matched[: window.limit]
        )


def _overlaps(draft: CalendarEventDraft, window: CalendarWindow) -> bool:
    """Overlap, not containment.

    An event that started yesterday and runs through today belongs in today's
    agenda, and asking Google the same question returns it -- `timeMin`/
    `timeMax` bound the interval, not the start.
    """

    start = _instant(window.start, window.timezone)
    end = _instant(window.end, window.timezone)
    return _instant(draft.start, draft.timezone) < end and start < _instant(
        draft.end, draft.timezone
    )


def _instant(moment: datetime | date, timezone: str) -> datetime:
    """One comparable instant, so a date and a datetime can be ordered.

    An all-day bound is widened to midnight in the *calendar's* zone, never in
    UTC. Widening in UTC was the first version and it silently dropped an 06:00
    event from its own day's agenda -- 06:00+07 is 23:00Z the day before, which
    lands outside a window that starts at 00:00Z. That is F6 again, in a fake
    rather than in a handler, and it would have made every read test agree with
    something the deployment does not do.
    """

    if isinstance(moment, datetime):
        return moment
    return datetime.combine(moment, time.min, tzinfo=ZoneInfo(timezone))
