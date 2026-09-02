"""What the calendar OAuth router does on every path out of the callback.

Owns SPEC-per-user-google-calendar-oauth J4 (a refused calendar consent costs
nothing already earned) and J5 (a calendar grant cannot log anyone in).

J4 is the invariant this router is most likely to break and the one a green
suite hides: the second consent runs *after* the user is already logged in with
mail connected, so any branch that forgets to carry `gmail=connected` silently
tells the frontend the mail connection failed. Every branch is enumerated here
rather than sampled.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI

from cowork_agent.api.calendars import CONNECT_PATH, create_calendar_router
from cowork_agent.composition import CalendarRuntime, ControlPlane, CoworkRuntime
from cowork_agent.config import SessionSettings
from cowork_agent.domain import CalendarConnection
from cowork_agent.identity import VerifiedPrincipal
from cowork_agent.integrations.gmail.auth import TokenCipher
from cowork_agent.integrations.google_calendar import (
    CalendarReauthRequiredError,
    GoogleCalendarOAuthSettings,
)

FRONTEND_URL = "https://app.example.com"
COOKIE_NAME = "cowork_session"
NOW = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
CALLBACK = "/v1/calendar/oauth/google/callback"
AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/auth?state=state-1"


class _Sessions:
    """Resolves exactly one token, to exactly one principal."""

    def __init__(self, token: str = "session-token") -> None:
        self._token = token

    async def resolve(self, token: str, *, now: datetime) -> VerifiedPrincipal | None:
        del now
        return VerifiedPrincipal(user_id="user-a") if token == self._token else None


class _Connections:
    """The consent service, scripted to succeed or to fail."""

    def __init__(self, failure: Exception | None = None) -> None:
        self._failure = failure
        self.completed: list[str] = []

    def begin(self) -> str:
        return AUTHORIZE_URL

    async def complete(
        self, state: str, authorization_response: str, *, user_id: str
    ) -> CalendarConnection:
        del state, authorization_response
        if self._failure is not None:
            raise self._failure
        self.completed.append(user_id)
        return CalendarConnection(
            id="cal-1",
            user_id=user_id,
            provider="google_calendar",
            external_account_id="a@example.com",
            calendar_id="primary",
            encrypted_refresh_token="encrypted",
            scopes=("https://www.googleapis.com/auth/calendar",),
            timezone="Asia/Ho_Chi_Minh",
            status="active",
            created_at=NOW,
            updated_at=NOW,
        )


class _Repository:
    def __init__(self, connection: CalendarConnection | None = None) -> None:
        self.connection = connection
        self.deleted: list[str] = []

    async def upsert(self, connection: CalendarConnection) -> CalendarConnection:
        self.connection = connection
        return connection

    async def get_for_user(self, user_id: str) -> CalendarConnection | None:
        del user_id
        return self.connection

    async def delete_for_user(self, user_id: str) -> bool:
        self.deleted.append(user_id)
        removed = self.connection is not None
        self.connection = None
        return removed


def _plane(
    connections: _Connections | None = None,
    repository: _Repository | None = None,
    *,
    frontend_url: str = FRONTEND_URL,
) -> CalendarRuntime:
    return CalendarRuntime(
        oauth_settings=GoogleCalendarOAuthSettings(
            client_id="calendar.apps.googleusercontent.com",
            client_secret="calendar-secret",
            redirect_uri="http://localhost:8000" + CALLBACK,
            frontend_url=frontend_url,
        ),
        connections=cast(Any, connections or _Connections()),
        repository=cast(Any, repository or _Repository()),
        cipher=TokenCipher(Fernet.generate_key().decode()),
    )


def _control_plane() -> ControlPlane:
    # Only two fields are on this router's path; a full ControlPlane would be
    # thirty constructors of noise for no extra coverage.
    return cast(
        ControlPlane,
        SimpleNamespace(
            session_settings=SessionSettings(
                session_ttl_seconds=3600, cookie_name=COOKIE_NAME, cookie_secure=True
            ),
            session_repository=_Sessions(),
        ),
    )


@asynccontextmanager
async def _client(
    plane: CalendarRuntime | None, *, signed_in: bool = False
) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(create_calendar_router())
    app.state.runtime = CoworkRuntime(
        reports=cast(Any, None), control_plane=_control_plane(), calendar=plane
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={COOKIE_NAME: "session-token"} if signed_in else None,
    ) as client:
        yield client


def _query(response: httpx.Response) -> dict[str, list[str]]:
    return parse_qs(urlsplit(response.headers["location"]).query)


# --- the happy path -------------------------------------------------------


def test_a_signed_in_user_is_sent_to_the_google_consent() -> None:
    async def scenario() -> None:
        async with _client(_plane(), signed_in=True) as client:
            response = await client.get(CONNECT_PATH)
        assert response.status_code == 302
        assert response.headers["location"] == AUTHORIZE_URL

    asyncio.run(scenario())


def test_a_completed_consent_reports_both_outcomes() -> None:
    connections = _Connections()

    async def scenario() -> None:
        async with _client(_plane(connections), signed_in=True) as client:
            response = await client.get(f"{CALLBACK}?state=state-1&code=abc")
        assert response.status_code == 302
        query = _query(response)
        assert query["calendar"] == ["connected"]
        assert query["gmail"] == ["connected"]
        assert connections.completed == ["user-a"]

    asyncio.run(scenario())


# --- J4: the second leg never unwinds the first ---------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"{CALLBACK}?state=state-1&error=access_denied", "denied"),
        (f"{CALLBACK}?state=state-1", "error"),
    ],
    ids=["user-denied-consent", "no-authorization-code"],
)
def test_a_refused_consent_keeps_the_mail_connection(url: str, expected: str) -> None:
    async def scenario() -> None:
        async with _client(_plane(), signed_in=True) as client:
            response = await client.get(url)
        assert response.status_code == 302
        query = _query(response)
        assert query["calendar"] == [expected]
        # The whole of J4 in one assertion: mail connected before this leg ran
        # and is still connected after it failed.
        assert query["gmail"] == ["connected"]

    asyncio.run(scenario())


def test_a_failed_exchange_keeps_the_mail_connection_and_the_cookie() -> None:
    plane = _plane(_Connections(failure=ValueError("OAuth state has already been used")))

    async def scenario() -> None:
        async with _client(plane, signed_in=True) as client:
            response = await client.get(f"{CALLBACK}?state=state-1&code=abc")
        assert response.status_code == 302
        assert _query(response)["gmail"] == ["connected"]
        # Nothing clears the session on the way out: the user stays logged in.
        assert "set-cookie" not in response.headers

    asyncio.run(scenario())


def test_any_exchange_failure_keeps_the_mail_connection_not_just_expected_ones() -> None:
    # `CalendarReauthRequiredError` is a RuntimeError and Google's client
    # raises its own types. Narrowing the callback's except clause to the
    # failures we thought of is how a 500 replaces the redirect and the
    # frontend never learns that mail connected.
    plane = _plane(_Connections(failure=CalendarReauthRequiredError("no refresh token")))

    async def scenario() -> None:
        async with _client(plane, signed_in=True) as client:
            response = await client.get(f"{CALLBACK}?state=state-1&code=abc")
        assert response.status_code == 302
        query = _query(response)
        assert query["calendar"] == ["error"]
        assert query["gmail"] == ["connected"]

    asyncio.run(scenario())


def test_an_unauthenticated_callback_still_preserves_the_mail_outcome() -> None:
    async def scenario() -> None:
        async with _client(_plane()) as client:
            response = await client.get(f"{CALLBACK}?state=state-1&code=abc")
        assert response.status_code == 302
        query = _query(response)
        assert query["calendar"] == ["unauthenticated"]
        assert query["gmail"] == ["connected"]

    asyncio.run(scenario())


# --- J5: a calendar grant cannot log anyone in ----------------------------


def test_a_callback_without_a_session_stores_nothing_and_sets_no_cookie() -> None:
    connections = _Connections()

    async def scenario() -> None:
        async with _client(_plane(connections)) as client:
            response = await client.get(f"{CALLBACK}?state=state-1&code=abc")
        assert "set-cookie" not in response.headers
        # The exchange never ran, so no account email could become a principal.
        assert connections.completed == []

    asyncio.run(scenario())


def test_connecting_without_a_session_never_starts_a_consent() -> None:
    async def scenario() -> None:
        async with _client(_plane()) as client:
            response = await client.get(CONNECT_PATH)
        assert response.status_code == 302
        assert _query(response)["calendar"] == ["unauthenticated"]
        assert "accounts.google.com" not in response.headers["location"]

    asyncio.run(scenario())


def test_a_successful_callback_sets_no_session_cookie() -> None:
    async def scenario() -> None:
        async with _client(_plane(), signed_in=True) as client:
            response = await client.get(f"{CALLBACK}?state=state-1&code=abc")
        assert "set-cookie" not in response.headers

    asyncio.run(scenario())


# --- the status read and the disconnect -----------------------------------


def test_the_status_read_reports_a_connection_without_its_token() -> None:
    repository = _Repository(
        CalendarConnection(
            id="cal-1",
            user_id="user-a",
            provider="google_calendar",
            external_account_id="a@example.com",
            calendar_id="primary",
            encrypted_refresh_token="the-encrypted-secret",
            scopes=("https://www.googleapis.com/auth/calendar",),
            timezone="Asia/Ho_Chi_Minh",
            status="active",
            created_at=NOW,
            updated_at=NOW,
        )
    )

    async def scenario() -> None:
        async with _client(_plane(repository=repository), signed_in=True) as client:
            response = await client.get("/v1/calendar/connection")
        payload = response.json()
        assert payload["connected"] is True
        assert payload["connection"]["account"] == "a@example.com"
        # Neither the token nor the scopes leave the server.
        assert "the-encrypted-secret" not in response.text
        assert "refresh" not in response.text

    asyncio.run(scenario())


def test_an_unconnected_user_reads_as_unconnected() -> None:
    async def scenario() -> None:
        async with _client(_plane(), signed_in=True) as client:
            response = await client.get("/v1/calendar/connection")
        assert response.json() == {"connected": False, "connection": None}

    asyncio.run(scenario())


def test_the_status_read_requires_a_session() -> None:
    async def scenario() -> None:
        async with _client(_plane()) as client:
            response = await client.get("/v1/calendar/connection")
        assert response.status_code == 401

    asyncio.run(scenario())


def test_disconnecting_removes_only_the_calendar_grant() -> None:
    repository = _Repository(
        CalendarConnection(
            id="cal-1",
            user_id="user-a",
            provider="google_calendar",
            external_account_id="a@example.com",
            calendar_id="primary",
            encrypted_refresh_token="encrypted",
            scopes=("https://www.googleapis.com/auth/calendar",),
            timezone="Asia/Ho_Chi_Minh",
            status="active",
            created_at=NOW,
            updated_at=NOW,
        )
    )

    async def scenario() -> None:
        async with _client(_plane(repository=repository), signed_in=True) as client:
            response = await client.delete("/v1/calendar/connection")
        assert response.json() == {"disconnected": True}
        assert repository.deleted == ["user-a"]
        # J6 at the transport: the session that mail minted is untouched.
        assert "set-cookie" not in response.headers

    asyncio.run(scenario())


# --- an unconfigured deployment -------------------------------------------


def test_an_unconfigured_calendar_plane_is_a_service_error_not_a_crash() -> None:
    async def scenario() -> None:
        async with _client(None, signed_in=True) as client:
            response = await client.get(CONNECT_PATH)
        assert response.status_code == 503

    asyncio.run(scenario())
