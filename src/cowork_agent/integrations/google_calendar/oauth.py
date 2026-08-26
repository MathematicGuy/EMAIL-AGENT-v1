"""The per-user Google Calendar handshake.

Mirrors `integrations/gmail/provider.py`'s OAuth path deliberately: same PKCE
verifier, same single-use state, same granted-scope check on the way back. The
two grants stay separate records against separate consent screens
([ADR-020](../../../../tasks/adr/ADR-020-google-grants-stay-separate.md)); what
they share is the mechanism, not the token.
"""

from __future__ import annotations

import asyncio
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from google_auth_oauthlib.flow import Flow  # type: ignore[import-untyped]
from googleapiclient.discovery import build  # type: ignore[import-untyped]

from cowork_agent.domain import CalendarConnection
from cowork_agent.features.ai_chat.ports import CalendarConnectionRepository
from cowork_agent.integrations.gmail.auth import OAuthStateManager, TokenCipher

from .provider import CALENDAR_SCOPE, DEFAULT_CALENDAR_ID, DEFAULT_TIMEZONE, GoogleCalendarSettings

PROVIDER = "google_calendar"
_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
_TOKEN_URI = "https://oauth2.googleapis.com/token"


class CalendarReauthRequiredError(RuntimeError):
    """Google returned no refresh token, so nothing durable was granted."""


@dataclass(frozen=True, slots=True)
class GoogleCalendarOAuthSettings:
    """The application's own identity for the calendar consent.

    Deliberately holds no refresh token. Application identity is resolved once
    at boot; the credential is per-user and comes from the repository
    (ADR-019). That split is what stops a turn reading `.env`.
    """

    client_id: str
    client_secret: str
    redirect_uri: str
    frontend_url: str = ""
    scopes: tuple[str, ...] = (CALENDAR_SCOPE,)

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> GoogleCalendarOAuthSettings | None:
        """Settings, or None when the handshake is not configured."""

        source = os.environ if environ is None else environ
        client_id = source.get("GOOGLE_CALENDAR_CLIENT_ID", "").strip()
        client_secret = source.get("GOOGLE_CALENDAR_CLIENT_SECRET", "").strip()
        redirect_uri = source.get("GOOGLE_CALENDAR_REDIRECT_URI", "").strip()
        if not (client_id and client_secret and redirect_uri):
            return None
        scopes = tuple(source.get("GOOGLE_CALENDAR_SCOPES", CALENDAR_SCOPE).split())
        if scopes != (CALENDAR_SCOPE,):
            # The mirror of the Gmail guard. Its job here is the reverse: keep
            # anything mail-shaped off the calendar consent, so neither grant
            # can quietly grow into the other (ADR-020 J3).
            raise ValueError("The calendar grant must use only the calendar scope")
        frontend_url = source.get("FRONTEND_URL", "").strip()
        if frontend_url:
            parts = urlsplit(frontend_url)
            secure_remote = parts.scheme == "https" and bool(parts.hostname)
            local_http = parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1"}
            if not (secure_remote or local_http):
                raise ValueError("FRONTEND_URL must use HTTPS, except for localhost")
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            frontend_url=frontend_url,
            scopes=scopes,
        )


@dataclass(frozen=True, slots=True)
class GoogleCalendarOAuthGrant:
    account_email: str
    refresh_token: str
    scopes: tuple[str, ...]
    calendar_id: str
    timezone: str


class GoogleCalendarOAuthDriver(Protocol):
    def authorization_url(self, state: str, code_verifier: str) -> str: ...
    async def exchange(
        self, state: str, authorization_response: str, code_verifier: str
    ) -> GoogleCalendarOAuthGrant: ...


class GoogleOAuthCalendarDriver:
    def __init__(self, settings: GoogleCalendarOAuthSettings) -> None:
        self._settings = settings

    def authorization_url(self, state: str, code_verifier: str) -> str:
        flow = self._flow(state, code_verifier)
        url, _ = flow.authorization_url(access_type="offline", prompt="consent", state=state)
        return str(url)

    async def exchange(
        self, state: str, authorization_response: str, code_verifier: str
    ) -> GoogleCalendarOAuthGrant:
        flow = self._flow(state, code_verifier)
        await asyncio.to_thread(flow.fetch_token, authorization_response=authorization_response)
        credentials = flow.credentials
        if not credentials.refresh_token:
            raise CalendarReauthRequiredError(
                "Google did not return a refresh token; reconnect and grant consent"
            )
        primary = await asyncio.to_thread(_primary_calendar, credentials)
        return GoogleCalendarOAuthGrant(
            # `calendarList.get('primary')` answers both questions the callback
            # needs -- whose account this is, and what timezone their calendar
            # keeps -- without asking for a userinfo scope we would then have to
            # justify.
            account_email=str(primary.get("id", "")),
            refresh_token=credentials.refresh_token,
            scopes=tuple(credentials.scopes or self._settings.scopes),
            calendar_id=DEFAULT_CALENDAR_ID,
            timezone=str(primary.get("timeZone") or DEFAULT_TIMEZONE),
        )

    def _flow(self, state: str, code_verifier: str) -> Flow:
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": self._settings.client_id,
                    "client_secret": self._settings.client_secret,
                    "auth_uri": _AUTH_URI,
                    "token_uri": _TOKEN_URI,
                    "redirect_uris": [self._settings.redirect_uri],
                }
            },
            scopes=list(self._settings.scopes),
            state=state,
            code_verifier=code_verifier,
            autogenerate_code_verifier=False,
        )
        flow.redirect_uri = self._settings.redirect_uri
        return flow


def _primary_calendar(credentials: Any) -> Mapping[str, Any]:
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    entry: Mapping[str, Any] = service.calendarList().get(calendarId=DEFAULT_CALENDAR_ID).execute()
    return entry


class GoogleCalendarConnectionService:
    """Begin and complete the calendar consent for one signed-in user."""

    def __init__(
        self,
        settings: GoogleCalendarOAuthSettings,
        repository: CalendarConnectionRepository,
        cipher: TokenCipher,
        state_manager: OAuthStateManager,
        driver: GoogleCalendarOAuthDriver | None = None,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._cipher = cipher
        self._state_manager = state_manager
        self._driver = driver or GoogleOAuthCalendarDriver(settings)

    def begin(self) -> str:
        code_verifier = secrets.token_urlsafe(96)
        state = self._state_manager.issue(context=code_verifier)
        return self._driver.authorization_url(state, code_verifier)

    async def complete(
        self, state: str, authorization_response: str, *, user_id: str
    ) -> CalendarConnection:
        """Store the grant against an already-authenticated principal.

        `user_id` is required rather than derived. The Gmail callback binds
        identity from its own verified grant because no session exists yet; this
        one runs after the cookie is set, so the caller supplies the principal
        and a calendar grant can never mint an identity of its own
        (SPEC J5).
        """

        if not user_id:
            raise ValueError("A calendar grant needs an authenticated user")
        code_verifier = await self._state_manager.consume(state)
        if not code_verifier:
            raise ValueError("OAuth PKCE code verifier is missing")
        grant = await self._driver.exchange(state, authorization_response, code_verifier)
        if grant.scopes != self._settings.scopes:
            raise ValueError("Google granted an unexpected Calendar OAuth scope")
        now = datetime.now(UTC)
        return await self._repository.upsert(
            CalendarConnection(
                id=f"cal_{uuid4().hex}",
                user_id=user_id,
                provider=PROVIDER,
                external_account_id=grant.account_email.lower(),
                calendar_id=grant.calendar_id,
                encrypted_refresh_token=self._cipher.encrypt(grant.refresh_token),
                scopes=grant.scopes,
                timezone=grant.timezone,
                status="active",
                created_at=now,
                updated_at=now,
            )
        )


def calendar_settings_for(
    connection: CalendarConnection,
    oauth_settings: GoogleCalendarOAuthSettings,
    cipher: TokenCipher,
) -> GoogleCalendarSettings:
    """The adapter's per-calendar credential, assembled from the two halves.

    Application identity comes from boot-time settings, the refresh token from
    this user's connection. `enabled=True` because reaching here means a grant
    exists; whether the feature is on at all was decided before the lookup.
    """

    return GoogleCalendarSettings(
        client_id=oauth_settings.client_id,
        client_secret=oauth_settings.client_secret,
        refresh_token=cipher.decrypt(connection.encrypted_refresh_token),
        calendar_id=connection.calendar_id,
        timezone=connection.timezone,
        enabled=True,
    )
