import asyncio
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httplib2
import pytest
from googleapiclient.errors import HttpError

from cowork_agent.features.ai_chat.tools import (
    CalendarError,
    CalendarEventDraft,
    CalendarEventSummary,
    CalendarWindow,
)
from cowork_agent.integrations.google_calendar import GoogleCalendar, GoogleCalendarSettings
from cowork_agent.integrations.google_calendar.provider import event_body

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
SETTINGS = GoogleCalendarSettings(
    client_id="id",
    client_secret="secret",
    refresh_token="refresh",
    calendar_id="primary",
    timezone="Asia/Ho_Chi_Minh",
)

TIMED = CalendarEventDraft(
    event_id="coagent0123456789",
    title="Họp team",
    start=datetime(2026, 8, 26, 15, 0, tzinfo=TZ),
    end=datetime(2026, 8, 26, 15, 30, tzinfo=TZ),
    timezone="Asia/Ho_Chi_Minh",
    description="weekly sync",
)
ALL_DAY = CalendarEventDraft(
    event_id="coagent9876543210",
    title="Nộp báo cáo",
    start=date(2026, 8, 27),
    end=date(2026, 8, 28),
    timezone="Asia/Ho_Chi_Minh",
)
WINDOW = CalendarWindow(
    start=date(2026, 8, 26),
    end=date(2026, 8, 29),
    timezone="Asia/Ho_Chi_Minh",
    limit=20,
)


def _http_error(status: int, reason: str = "boom") -> HttpError:
    return HttpError(httplib2.Response({"status": status, "reason": reason}), b"{}")


class _Request:
    def __init__(self, outcome: Any) -> None:
        self._outcome = outcome

    def execute(self) -> Any:
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class _Events:
    def __init__(self, *, insert: Any = None, get: Any = None, listed: Any = None) -> None:
        self._insert = insert
        self._get = get
        self._listed = listed
        self.insert_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
        self.list_calls: list[dict[str, Any]] = []

    def insert(self, **kwargs: Any) -> _Request:
        self.insert_calls.append(kwargs)
        return _Request(self._insert)

    def get(self, **kwargs: Any) -> _Request:
        self.get_calls.append(kwargs)
        return _Request(self._get)

    def list(self, **kwargs: Any) -> _Request:
        self.list_calls.append(kwargs)
        return _Request(self._listed)


class _Service:
    def __init__(self, events: _Events) -> None:
        self._events = events

    def events(self) -> _Events:
        return self._events


def _create(events: _Events, draft: CalendarEventDraft = TIMED) -> str:
    calendar = GoogleCalendar(SETTINGS, service=_Service(events))
    return asyncio.run(calendar.create_event(draft))


def _list(events: _Events, window: CalendarWindow = WINDOW) -> tuple[CalendarEventSummary, ...]:
    calendar = GoogleCalendar(SETTINGS, service=_Service(events))
    return asyncio.run(calendar.list_events(window))


def test_a_timed_body_carries_both_an_offset_and_the_named_timezone() -> None:
    """An offset-less dateTime is accepted by Google, so a missing offset is not an error there."""

    body = event_body(TIMED)

    assert body["start"] == {
        "dateTime": "2026-08-26T15:00:00+07:00",
        "timeZone": "Asia/Ho_Chi_Minh",
    }
    assert body["id"] == TIMED.event_id
    assert body["summary"] == "Họp team"
    assert body["description"] == "weekly sync"


def test_an_all_day_body_uses_date_only_bounds_and_omits_description() -> None:
    body = event_body(ALL_DAY)

    assert body["start"] == {"date": "2026-08-27"}
    assert body["end"] == {"date": "2026-08-28"}
    assert "description" not in body


def test_a_successful_insert_returns_the_html_link() -> None:
    events = _Events(insert={"id": TIMED.event_id, "htmlLink": "https://cal/x"})

    assert _create(events) == "https://cal/x"
    assert events.insert_calls[0]["calendarId"] == "primary"


def test_a_duplicate_id_resolves_to_the_existing_event_instead_of_failing() -> None:
    """409 on re-insert is the whole idempotency mechanism -- it needs no local state."""

    events = _Events(
        insert=_http_error(409, "duplicate"),
        get={"id": TIMED.event_id, "htmlLink": "https://cal/x", "status": "confirmed"},
    )

    assert _create(events) == "https://cal/x"
    assert events.get_calls[0]["eventId"] == TIMED.event_id


def test_a_duplicate_id_whose_event_was_deleted_is_not_reported_as_created() -> None:
    """Delete tombstones rather than removes, so the id still resolves after deletion."""

    events = _Events(
        insert=_http_error(409, "duplicate"),
        get={"id": TIMED.event_id, "htmlLink": "https://cal/x", "status": "cancelled"},
    )

    with pytest.raises(CalendarError, match="since been deleted"):
        _create(events)


@pytest.mark.parametrize("status", [400, 403, 404, 500])
def test_other_api_errors_become_calendar_errors_carrying_the_status(status: int) -> None:
    events = _Events(insert=_http_error(status, "accessNotConfigured"))

    with pytest.raises(CalendarError, match=str(status)):
        _create(events)


def test_a_listing_asks_google_to_expand_recurrences_and_order_them() -> None:
    """`singleEvents` and `orderBy` are contract, not tuning.

    Without expansion Google refuses to order at all, and the caller's port
    promises soonest-first. A change here breaks `list_calendar_events`
    silently, which is why it is asserted on the request rather than the reply.
    """

    events = _Events(listed={"items": []})

    _list(events)

    sent = events.list_calls[0]
    assert sent["singleEvents"] is True
    assert sent["orderBy"] == "startTime"
    assert sent["maxResults"] == WINDOW.limit
    assert sent["calendarId"] == SETTINGS.calendar_id


def test_an_all_day_window_bound_is_widened_in_the_calendars_zone_not_utc() -> None:
    """`timeMin`/`timeMax` reject a bare date, and UTC would start the day early."""

    events = _Events(listed={"items": []})

    _list(events)

    sent = events.list_calls[0]
    assert sent["timeMin"] == "2026-08-26T00:00:00+07:00"
    assert sent["timeMax"] == "2026-08-29T00:00:00+07:00"


def test_a_listed_event_keeps_its_title_and_bounds() -> None:
    events = _Events(
        listed={
            "items": [
                {
                    "summary": "Họp team",
                    "start": {"dateTime": "2026-08-26T15:00:00+07:00"},
                    "end": {"dateTime": "2026-08-26T15:30:00+07:00"},
                }
            ]
        }
    )

    (event,) = _list(events)

    assert event.title == "Họp team"
    assert event.start == datetime(2026, 8, 26, 15, 0, tzinfo=TZ)
    assert event.all_day is False


def test_an_all_day_item_comes_back_as_a_date_rather_than_midnight() -> None:
    events = _Events(
        listed={
            "items": [
                {
                    "summary": "Nộp báo cáo",
                    "start": {"date": "2026-08-27"},
                    "end": {"date": "2026-08-28"},
                }
            ]
        }
    )

    (event,) = _list(events)

    assert event.start == date(2026, 8, 27)
    assert event.all_day is True


def test_a_cancelled_recurring_instance_is_skipped_rather_than_given_a_time() -> None:
    """It arrives with no `start` at all; inventing one puts a dead meeting on the agenda."""

    events = _Events(
        listed={
            "items": [
                {"summary": "Standup", "status": "cancelled"},
                {
                    "summary": "Retro",
                    "start": {"dateTime": "2026-08-28T15:00:00+07:00"},
                    "end": {"dateTime": "2026-08-28T16:00:00+07:00"},
                },
            ]
        }
    )

    listed = _list(events)

    assert [event.title for event in listed] == ["Retro"]


def test_an_item_without_a_title_is_named_rather_than_blank() -> None:
    events = _Events(
        listed={
            "items": [
                {
                    "start": {"dateTime": "2026-08-28T15:00:00+07:00"},
                    "end": {"dateTime": "2026-08-28T16:00:00+07:00"},
                }
            ]
        }
    )

    (event,) = _list(events)

    assert event.title == "(no title)"


def test_a_failed_listing_becomes_a_calendar_error_carrying_the_status() -> None:
    """A read failure is data too -- the handler turns this into `ok=False` text."""

    events = _Events(listed=_http_error(403))

    with pytest.raises(CalendarError) as caught:
        _list(events)

    assert "403" in str(caught.value)


def test_from_env_returns_none_without_a_complete_grant() -> None:
    assert GoogleCalendarSettings.from_env({}) is None
    assert (
        GoogleCalendarSettings.from_env(
            {"GOOGLE_CALENDAR_CLIENT_ID": "id", "GOOGLE_CALENDAR_CLIENT_SECRET": "secret"}
        )
        is None
    )


def test_from_env_defaults_the_calendar_and_stays_disabled_unless_asked() -> None:
    settings = GoogleCalendarSettings.from_env(
        {
            "GOOGLE_CALENDAR_CLIENT_ID": "id",
            "GOOGLE_CALENDAR_CLIENT_SECRET": "secret",
            "GOOGLE_CALENDAR_REFRESH_TOKEN": "refresh",
        }
    )

    assert settings is not None
    assert settings.calendar_id == "primary"
    assert settings.timezone == "Asia/Ho_Chi_Minh"
    assert settings.enabled is False


def test_from_env_reads_the_enable_flag_and_overrides() -> None:
    settings = GoogleCalendarSettings.from_env(
        {
            "GOOGLE_CALENDAR_CLIENT_ID": "id",
            "GOOGLE_CALENDAR_CLIENT_SECRET": "secret",
            "GOOGLE_CALENDAR_REFRESH_TOKEN": "refresh",
            "GOOGLE_CALENDAR_ID": "team@group.calendar.google.com",
            "GOOGLE_CALENDAR_TIMEZONE": "Europe/London",
            "GOOGLE_CALENDAR_ENABLED": "true",
        }
    )

    assert settings is not None
    assert settings.calendar_id == "team@group.calendar.google.com"
    assert settings.timezone == "Europe/London"
    assert settings.enabled is True
