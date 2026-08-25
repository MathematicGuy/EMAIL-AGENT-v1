"""Google Calendar adapter for `CalendarPort`.

Behaviour here is not inferred from documentation -- every branch corresponds to
something observed live by `scripts/smoke_test_google_calendar.py` and written
down in `docs/references/google-calendar-api-notes.md`.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build  # type: ignore[import-untyped]
from googleapiclient.errors import HttpError  # type: ignore[import-untyped]

from cowork_agent.features.ai_chat.tools.calendar import (
    CalendarError,
    CalendarEventDraft,
)

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
TOKEN_URI = "https://oauth2.googleapis.com/token"
DEFAULT_CALENDAR_ID = "primary"
DEFAULT_TIMEZONE = "Asia/Ho_Chi_Minh"


@dataclass(frozen=True, slots=True)
class GoogleCalendarSettings:
    """Credentials for the single service-level calendar grant.

    One refresh token shared by every chat user. That is a demo shortcut and
    the largest piece of debt in `tasks/specs/SPEC-chat-tools-registry.md`
    (§10); it is deliberately separate from the Gmail connection, whose
    read-only scope guard must not be loosened to carry this.
    """

    client_id: str
    client_secret: str
    refresh_token: str
    calendar_id: str = DEFAULT_CALENDAR_ID
    timezone: str = DEFAULT_TIMEZONE
    enabled: bool = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> GoogleCalendarSettings | None:
        """Settings, or None when the calendar tool is not configured."""

        source = os.environ if environ is None else environ
        client_id = source.get("GOOGLE_CALENDAR_CLIENT_ID", "").strip()
        client_secret = source.get("GOOGLE_CALENDAR_CLIENT_SECRET", "").strip()
        refresh_token = source.get("GOOGLE_CALENDAR_REFRESH_TOKEN", "").strip()
        if not (client_id and client_secret and refresh_token):
            return None
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            calendar_id=source.get("GOOGLE_CALENDAR_ID", "").strip() or DEFAULT_CALENDAR_ID,
            timezone=source.get("GOOGLE_CALENDAR_TIMEZONE", "").strip() or DEFAULT_TIMEZONE,
            enabled=source.get("GOOGLE_CALENDAR_ENABLED", "").strip().lower() == "true",
        )


class GoogleCalendar:
    """Insert events into one Google calendar with a service-level grant."""

    def __init__(self, settings: GoogleCalendarSettings, *, service: Any | None = None) -> None:
        self._settings = settings
        self._service = service

    async def create_event(self, draft: CalendarEventDraft) -> str:
        """Insert the event and return its `htmlLink`.

        A re-used `event_id` comes back as `409`, which is treated as success:
        the turn already created this event, so the retry has nothing to do.
        That is the whole idempotency mechanism and it needs no local state.
        """

        return await asyncio.to_thread(self._insert, draft)

    def _insert(self, draft: CalendarEventDraft) -> str:
        service = self._service or self._build_service()
        body = event_body(draft)
        try:
            created = (
                service.events()
                .insert(calendarId=self._settings.calendar_id, body=body)
                .execute()
            )
        except HttpError as exc:
            if exc.resp.status == 409:
                return self._existing_link(service, draft.event_id)
            raise CalendarError(_readable(exc)) from exc
        link: str = created.get("htmlLink", "")
        return link

    def _existing_link(self, service: Any, event_id: str) -> str:
        try:
            existing = (
                service.events()
                .get(calendarId=self._settings.calendar_id, eventId=event_id)
                .execute()
            )
        except HttpError as exc:
            raise CalendarError(_readable(exc)) from exc
        # A deleted event is tombstoned rather than removed, so the id can
        # still resolve while the event is gone. Reporting that as created
        # would be a lie the user finds out about later.
        if existing.get("status") == "cancelled":
            raise CalendarError("this event was created earlier and has since been deleted")
        link: str = existing.get("htmlLink", "")
        return link

    def _build_service(self) -> Any:
        credentials = Credentials(  # type: ignore[no-untyped-call]
            token=None,
            refresh_token=self._settings.refresh_token,
            client_id=self._settings.client_id,
            client_secret=self._settings.client_secret,
            token_uri=TOKEN_URI,
            scopes=[CALENDAR_SCOPE],
        )
        credentials.refresh(Request())  # type: ignore[no-untyped-call]
        service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        self._service = service
        return service


def event_body(draft: CalendarEventDraft) -> dict[str, object]:
    """The Calendar v3 request body for one draft."""

    body: dict[str, object] = {
        "id": draft.event_id,
        "summary": draft.title,
        "start": _bound(draft.start, draft.timezone),
        "end": _bound(draft.end, draft.timezone),
    }
    if draft.description:
        body["description"] = draft.description
    return body


def _bound(moment: datetime | date, timezone: str) -> dict[str, str]:
    if isinstance(moment, datetime):
        # Both an explicit offset and `timeZone`. Google accepts an offset-less
        # dateTime and reads it in the named zone, which means a missing offset
        # is silently not an error -- so send both and leave nothing to infer.
        return {"dateTime": moment.isoformat(), "timeZone": timezone}
    return {"date": moment.isoformat()}


def _readable(exc: Exception) -> str:
    reason = getattr(exc, "reason", None) or str(exc)
    status = getattr(getattr(exc, "resp", None), "status", None)
    return f"{status} {reason}".strip() if status else str(reason)
