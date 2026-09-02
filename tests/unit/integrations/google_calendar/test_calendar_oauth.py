"""The calendar grant: whose it is, what scope it carries, how it is stored.

Owns SPEC-per-user-google-calendar-oauth J1, J3, J5, J6 and J7 at the storage
and service level. The router half of J4/J5 lives in
`tests/unit/api/test_calendar_router.py`; the binder half of J1/J2 lives in
`tests/unit/features/ai_chat/test_calendar_binder.py`.

Offline by construction: the OAuth driver is faked at the same seam
`GmailOAuthDriver` is, so nothing here reaches Google.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from cowork_agent.domain import CalendarConnection
from cowork_agent.integrations.gmail.auth import OAuthStateManager, TokenCipher
from cowork_agent.integrations.google_calendar import (
    GoogleCalendarConnectionService,
    GoogleCalendarOAuthGrant,
    GoogleCalendarOAuthSettings,
    calendar_settings_for,
)
from cowork_agent.integrations.google_calendar.provider import CALENDAR_SCOPE
from cowork_agent.persistence.repositories.calendar_connections import (
    SQLiteCalendarConnectionRepository,
)

STATE_SECRET = "calendar-state-secret-at-least-32-characters"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


class _FakeDriver:
    """Records the PKCE verifier and returns a scripted grant."""

    def __init__(self, grant: GoogleCalendarOAuthGrant) -> None:
        self._grant = grant
        self.verifiers: list[str] = []

    def authorization_url(self, state: str, code_verifier: str) -> str:
        self.verifiers.append(code_verifier)
        return f"https://accounts.google.com/o/oauth2/auth?state={state}"

    async def exchange(
        self, state: str, authorization_response: str, code_verifier: str
    ) -> GoogleCalendarOAuthGrant:
        del state, authorization_response
        self.verifiers.append(code_verifier)
        return self._grant


def _grant(
    email: str, token: str, scopes: tuple[str, ...] = (CALENDAR_SCOPE,)
) -> GoogleCalendarOAuthGrant:
    return GoogleCalendarOAuthGrant(
        account_email=email,
        refresh_token=token,
        scopes=scopes,
        calendar_id="primary",
        timezone="Asia/Ho_Chi_Minh",
    )


def _oauth_settings() -> GoogleCalendarOAuthSettings:
    return GoogleCalendarOAuthSettings(
        client_id="calendar.apps.googleusercontent.com",
        client_secret="calendar-secret",
        redirect_uri="http://localhost:8000/v1/calendar/oauth/google/callback",
    )


def _service(
    repository: SQLiteCalendarConnectionRepository,
    driver: _FakeDriver,
    cipher: TokenCipher | None = None,
) -> GoogleCalendarConnectionService:
    return GoogleCalendarConnectionService(
        _oauth_settings(),
        repository,
        cipher or TokenCipher(Fernet.generate_key().decode()),
        OAuthStateManager(STATE_SECRET, 600),
        driver,
    )


@pytest.fixture
def repository(tmp_path: Path) -> SQLiteCalendarConnectionRepository:
    store = SQLiteCalendarConnectionRepository(tmp_path / "calendar_connections.db")
    asyncio.run(store.initialize())
    return store


def _connect(service: GoogleCalendarConnectionService, user_id: str) -> CalendarConnection:
    url = service.begin()
    state = url.rpartition("state=")[2]
    return asyncio.run(service.complete(state, "http://callback?code=abc", user_id=user_id))


# --- J1: a grant belongs to exactly one user ------------------------------


def test_each_user_resolves_only_their_own_grant(
    repository: SQLiteCalendarConnectionRepository,
) -> None:
    cipher = TokenCipher(Fernet.generate_key().decode())
    _connect(_service(repository, _FakeDriver(_grant("a@example.com", "refresh-a")), cipher), "u-a")
    _connect(_service(repository, _FakeDriver(_grant("b@example.com", "refresh-b")), cipher), "u-b")

    stored_a = asyncio.run(repository.get_for_user("u-a"))
    stored_b = asyncio.run(repository.get_for_user("u-b"))
    assert stored_a is not None and stored_b is not None
    assert calendar_settings_for(stored_a, _oauth_settings(), cipher).refresh_token == "refresh-a"
    assert calendar_settings_for(stored_b, _oauth_settings(), cipher).refresh_token == "refresh-b"


def test_the_refresh_token_is_encrypted_at_rest(
    repository: SQLiteCalendarConnectionRepository,
) -> None:
    cipher = TokenCipher(Fernet.generate_key().decode())
    connection = _connect(
        _service(repository, _FakeDriver(_grant("a@example.com", "refresh-a")), cipher), "u-a"
    )

    assert "refresh-a" not in connection.encrypted_refresh_token
    assert cipher.decrypt(connection.encrypted_refresh_token) == "refresh-a"


def test_reconnecting_replaces_the_grant_and_keeps_the_record(
    repository: SQLiteCalendarConnectionRepository,
) -> None:
    cipher = TokenCipher(Fernet.generate_key().decode())
    first = _connect(
        _service(repository, _FakeDriver(_grant("a@example.com", "refresh-old")), cipher), "u-a"
    )
    second = _connect(
        _service(repository, _FakeDriver(_grant("a@example.com", "refresh-new")), cipher), "u-a"
    )

    assert second.id == first.id
    assert second.created_at == first.created_at
    stored = asyncio.run(repository.get_for_user("u-a"))
    assert stored is not None
    assert cipher.decrypt(stored.encrypted_refresh_token) == "refresh-new"


# --- J3: neither grant grows into the other -------------------------------


def test_the_calendar_consent_refuses_any_scope_but_calendar() -> None:
    environ = {
        "GOOGLE_CALENDAR_CLIENT_ID": "calendar.apps.googleusercontent.com",
        "GOOGLE_CALENDAR_CLIENT_SECRET": "calendar-secret",
        "GOOGLE_CALENDAR_REDIRECT_URI": "http://localhost:8000/cb",
        "GOOGLE_CALENDAR_SCOPES": f"{CALENDAR_SCOPE} {GMAIL_SCOPE}",
    }
    with pytest.raises(ValueError, match="only the calendar scope"):
        GoogleCalendarOAuthSettings.from_env(environ)


def test_a_grant_google_widened_is_rejected_and_stored_nowhere(
    repository: SQLiteCalendarConnectionRepository,
) -> None:
    widened = _grant("a@example.com", "refresh-a", scopes=(CALENDAR_SCOPE, GMAIL_SCOPE))

    with pytest.raises(ValueError, match="unexpected Calendar OAuth scope"):
        _connect(_service(repository, _FakeDriver(widened)), "u-a")
    assert asyncio.run(repository.get_for_user("u-a")) is None


# --- J5: a calendar grant is not an identity ------------------------------


def test_a_calendar_grant_cannot_stand_in_for_an_identity(
    repository: SQLiteCalendarConnectionRepository,
) -> None:
    service = _service(repository, _FakeDriver(_grant("a@example.com", "refresh-a")))
    url = service.begin()
    state = url.rpartition("state=")[2]

    # The account email inside the token is never allowed to become the user
    # the grant is stored against.
    with pytest.raises(ValueError, match="authenticated user"):
        asyncio.run(service.complete(state, "http://callback?code=abc", user_id=""))


def test_a_state_is_single_use(repository: SQLiteCalendarConnectionRepository) -> None:
    service = _service(repository, _FakeDriver(_grant("a@example.com", "refresh-a")))
    url = service.begin()
    state = url.rpartition("state=")[2]

    asyncio.run(service.complete(state, "http://callback?code=abc", user_id="u-a"))
    with pytest.raises(ValueError, match="already been used"):
        asyncio.run(service.complete(state, "http://callback?code=abc", user_id="u-a"))


# --- J6: either grant is independently revocable --------------------------


def test_disconnecting_one_user_leaves_the_other_connected(
    repository: SQLiteCalendarConnectionRepository,
) -> None:
    cipher = TokenCipher(Fernet.generate_key().decode())
    for user in ("u-a", "u-b"):
        _connect(
            _service(repository, _FakeDriver(_grant(f"{user}@example.com", user)), cipher), user
        )

    assert asyncio.run(repository.delete_for_user("u-a")) is True
    assert asyncio.run(repository.get_for_user("u-a")) is None
    assert asyncio.run(repository.get_for_user("u-b")) is not None
    # Deleting what is already gone is not an error, and not a success either.
    assert asyncio.run(repository.delete_for_user("u-a")) is False


# --- J7: a calendar connection is never a mailbox -------------------------


def test_a_calendar_connection_is_invisible_to_mail_routing(tmp_path: Path) -> None:
    from cowork_agent.persistence.repositories.mailbox_connections import (
        SQLiteMailboxConnectionRepository,
    )

    # Deliberately the same database file: the separation that matters is the
    # table, not the file, and sharing one here is the harsher test.
    database = tmp_path / "connections.db"
    mailboxes = SQLiteMailboxConnectionRepository(database)
    asyncio.run(mailboxes.initialize())
    calendars = SQLiteCalendarConnectionRepository(database)
    asyncio.run(calendars.initialize())
    _connect(_service(calendars, _FakeDriver(_grant("a@example.com", "refresh-a"))), "u-a")

    # `ProviderRoutingMailboxAdapter` iterates this list to route mail; a
    # calendar row appearing in it is a mail-routing bug.
    assert list(asyncio.run(mailboxes.list_for_user("u-a"))) == []
