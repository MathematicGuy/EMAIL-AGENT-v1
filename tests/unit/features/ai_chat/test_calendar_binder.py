"""Which calendar a turn writes to, and what happens when there is none.

Owns SPEC-per-user-google-calendar-oauth J1 and J2 at the binder seam — the
one place where "whose grant is this" turns into a credential. The storage half
of J1 lives in `tests/unit/integrations/google_calendar/test_calendar_oauth.py`.

`GoogleCalendar` is monkeypatched at its `app` module lookup rather than
injected, because the whole point of these tests is what the production binder
constructs; injecting a port would prove the injection, not the resolution.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from cowork_agent import app as app_module
from cowork_agent.composition import CalendarRuntime
from cowork_agent.domain import CalendarConnection
from cowork_agent.features.ai_chat.tools import AGENDA_TOOL_NAME, CALENDAR_TOOL_NAME
from cowork_agent.integrations.gmail.auth import TokenCipher
from cowork_agent.integrations.google_calendar import (
    GoogleCalendarOAuthSettings,
    GoogleCalendarSettings,
)
from cowork_agent.persistence.repositories.calendar_connections import (
    SQLiteCalendarConnectionRepository,
)

NOW = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
ENVIRONMENT_TOKEN = "refresh-from-the-environment"

ARGUMENTS = {
    "title": "Gym",
    "start": "2026-08-28T02:00:00+07:00",
    "end": "2026-08-28T03:00:00+07:00",
}


class _RecordingCalendar:
    """Stands in for `GoogleCalendar`, remembering the credential it was given."""

    constructed: list[GoogleCalendarSettings] = []

    def __init__(self, settings: GoogleCalendarSettings) -> None:
        self._settings = settings
        _RecordingCalendar.constructed.append(settings)

    async def create_event(self, draft: object) -> str:
        del draft
        return "https://calendar.google.com/event?eid=recorded"


class _Providers:
    """The one field `_chat_tool_runner` reads off the provider bundle."""

    def __init__(self) -> None:
        self.tool_arguments = self._complete

    async def _complete(self, prompt: str, schema: object) -> dict[str, object]:
        del prompt, schema
        return dict(ARGUMENTS)


@pytest.fixture(autouse=True)
def recording_calendar(monkeypatch: pytest.MonkeyPatch) -> type[_RecordingCalendar]:
    _RecordingCalendar.constructed = []
    monkeypatch.setattr(app_module, "GoogleCalendar", _RecordingCalendar)
    return _RecordingCalendar


@pytest.fixture
def plane(tmp_path: Path) -> CalendarRuntime:
    repository = SQLiteCalendarConnectionRepository(tmp_path / "calendar_connections.db")
    asyncio.run(repository.initialize())
    return CalendarRuntime(
        oauth_settings=GoogleCalendarOAuthSettings(
            client_id="calendar.apps.googleusercontent.com",
            client_secret="calendar-secret",
            redirect_uri="http://localhost:8000/v1/calendar/oauth/google/callback",
        ),
        connections=None,  # type: ignore[arg-type]  # unused on the read path
        repository=repository,
        cipher=TokenCipher(Fernet.generate_key().decode()),
    )


def _environment_settings() -> GoogleCalendarSettings:
    return GoogleCalendarSettings(
        client_id="calendar.apps.googleusercontent.com",
        client_secret="calendar-secret",
        refresh_token=ENVIRONMENT_TOKEN,
        timezone="Asia/Ho_Chi_Minh",
        enabled=True,
    )


def _connect(plane: CalendarRuntime, user_id: str, token: str) -> None:
    asyncio.run(
        plane.repository.upsert(
            CalendarConnection(
                id=f"cal-{user_id}",
                user_id=user_id,
                provider="google_calendar",
                external_account_id=f"{user_id}@example.com",
                calendar_id="primary",
                encrypted_refresh_token=plane.cipher.encrypt(token),
                scopes=("https://www.googleapis.com/auth/calendar",),
                timezone="Asia/Ho_Chi_Minh",
                status="active",
                created_at=NOW,
                updated_at=NOW,
            )
        )
    )


def _run(plane: CalendarRuntime | None, user_id: str | None) -> str:
    runner = app_module._chat_tool_runner(_Providers(), _environment_settings(), plane)  # type: ignore[arg-type]
    assert runner is not None
    result = asyncio.run(
        runner.run_for_turn(
            CALENDAR_TOOL_NAME,
            user_message="Đặt lịch tập gym 2h sáng thứ Sáu",
            idempotency_key="idem-1",
            now=NOW,
            user_id=user_id,
        )
    )
    return result.text if result.ok else f"REFUSED: {result.text}"


# --- J1 -------------------------------------------------------------------


def test_a_turn_writes_through_its_own_users_grant(plane: CalendarRuntime) -> None:
    _connect(plane, "user-a", "refresh-a")
    _connect(plane, "user-b", "refresh-b")

    _run(plane, "user-a")
    _run(plane, "user-b")

    assert [settings.refresh_token for settings in _RecordingCalendar.constructed] == [
        "refresh-a",
        "refresh-b",
    ]


def test_the_connections_timezone_beats_the_process_default(plane: CalendarRuntime) -> None:
    _connect(plane, "user-a", "refresh-a")

    _run(plane, "user-a")

    (settings,) = _RecordingCalendar.constructed
    assert settings.timezone == "Asia/Ho_Chi_Minh"
    assert settings.calendar_id == "primary"


# --- J2 -------------------------------------------------------------------


def test_a_signed_in_user_without_a_grant_is_told_so(plane: CalendarRuntime) -> None:
    outcome = _run(plane, "user-without-a-grant")

    assert outcome.startswith("REFUSED: ")
    assert "not connected" in outcome
    # The environment token is configured and still unused: substituting it
    # here is how one person's event lands on another person's calendar.
    assert _RecordingCalendar.constructed == []


def test_a_missing_calendar_plane_refuses_rather_than_falling_back() -> None:
    outcome = _run(None, "user-a")

    assert outcome.startswith("REFUSED: ")
    assert _RecordingCalendar.constructed == []


def test_the_environment_token_survives_only_where_there_is_no_principal(
    plane: CalendarRuntime,
) -> None:
    # Local development: no session, no principal, one developer's own token.
    _run(plane, None)

    (settings,) = _RecordingCalendar.constructed
    assert settings.refresh_token == ENVIRONMENT_TOKEN


# --- the tool exists for everyone, even unconnected ------------------------


def test_the_tool_name_does_not_depend_on_the_user(plane: CalendarRuntime) -> None:
    runner = app_module._chat_tool_runner(_Providers(), _environment_settings(), plane)  # type: ignore[arg-type]
    assert runner is not None

    # The router narrows on `names` before any binding happens. A name that
    # appeared per user would make routing depend on connection state.
    assert runner.names == frozenset({CALENDAR_TOOL_NAME, AGENDA_TOOL_NAME})
