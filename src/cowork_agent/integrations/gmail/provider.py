"""Google OAuth and read-only Gmail API adapters."""

import asyncio
import base64
import html
import logging
import random
import re
import secrets
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import getaddresses, parseaddr
from typing import Any, Protocol, cast
from uuid import uuid4

import httplib2  # type: ignore[import-untyped]
from google.auth.exceptions import RefreshError, TransportError
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp  # type: ignore[import-untyped]
from google_auth_oauthlib.flow import Flow  # type: ignore[import-untyped]
from googleapiclient.discovery import build  # type: ignore[import-untyped]
from googleapiclient.errors import HttpError  # type: ignore[import-untyped]
from googleapiclient.http import HttpRequest  # type: ignore[import-untyped]

from cowork_agent.config import GmailSettings
from cowork_agent.domain import MailboxConnection
from cowork_agent.domain.target_contracts import (
    BodyFormat,
    EphemeralEmailEnvelope,
    FetchStatus,
)
from cowork_agent.features.email_action_plan.ports import (
    MailboxConnectionRepository,
    MailboxTemporaryError,
)
from cowork_agent.features.email_action_plan.schemas import MessageRef, SearchPage
from cowork_agent.identity import VerifiedPrincipal

from .auth import OAuthStateManager, TokenCipher

logger = logging.getLogger(__name__)


class MailboxNotConnectedError(LookupError):
    pass


class MailboxReauthRequiredError(RuntimeError):
    error_code = "GMAIL_REAUTH_REQUIRED"
    safe_message = "Gmail access needs to be reconnected. Reconnect Gmail and retry."


#: V1-H T5.4 bounded retry budget for transient Gmail failures. Delays use
#: full jitter (uniform in [0, exponential cap]) so concurrent workers do
#: not thunder-herd the API after an outage.
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY_SECONDS = 0.5
_RETRY_MAX_DELAY_SECONDS = 4.0
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


def _retry_delay(attempt: int) -> float:
    """Full-jitter exponential backoff for the given 1-based attempt."""
    ceiling = min(_RETRY_MAX_DELAY_SECONDS, _RETRY_BASE_DELAY_SECONDS * 2 ** (attempt - 1))
    return random.uniform(0.0, ceiling)


@dataclass(frozen=True, slots=True)
class GmailOAuthGrant:
    email_address: str
    refresh_token: str
    scopes: tuple[str, ...]


class GmailOAuthDriver(Protocol):
    def authorization_url(self, state: str, code_verifier: str) -> str: ...
    async def exchange(
        self, state: str, authorization_response: str, code_verifier: str
    ) -> GmailOAuthGrant: ...


class GmailPrincipalResolver(Protocol):
    async def __call__(self, email_address: str) -> VerifiedPrincipal: ...


class WorkspaceMailboxConnectionRepository(Protocol):
    async def upsert_for_workspace(
        self, connection: MailboxConnection, *, workspace_id: str
    ) -> MailboxConnection: ...


class GoogleOAuthDriver:
    def __init__(self, settings: GmailSettings) -> None:
        self._settings = settings

    def authorization_url(self, state: str, code_verifier: str) -> str:
        flow = self._flow(state, code_verifier)
        url, _ = flow.authorization_url(
            access_type="offline",
            prompt="consent",
            state=state,
        )
        return str(url)

    async def exchange(
        self, state: str, authorization_response: str, code_verifier: str
    ) -> GmailOAuthGrant:
        flow = self._flow(state, code_verifier)
        await asyncio.to_thread(
            flow.fetch_token,
            authorization_response=authorization_response,
        )
        credentials = flow.credentials
        if not credentials.refresh_token:
            raise MailboxReauthRequiredError(
                "Google did not return a refresh token; reconnect and grant consent"
            )
        email_address = await asyncio.to_thread(_get_profile_email, credentials)
        return GmailOAuthGrant(
            email_address=email_address,
            refresh_token=credentials.refresh_token,
            scopes=tuple(credentials.scopes or self._settings.scopes),
        )

    def _flow(self, state: str, code_verifier: str) -> Flow:
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": self._settings.client_id,
                    "client_secret": self._settings.client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [self._settings.redirect_uri],
                }
            },
            scopes=self._settings.scopes,
            state=state,
            code_verifier=code_verifier,
            autogenerate_code_verifier=False,
        )
        flow.redirect_uri = self._settings.redirect_uri
        return flow


class GmailConnectionService:
    def __init__(
        self,
        settings: GmailSettings,
        repository: MailboxConnectionRepository,
        cipher: TokenCipher,
        state_manager: OAuthStateManager,
        driver: GmailOAuthDriver | None = None,
        principal_resolver: GmailPrincipalResolver | None = None,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._cipher = cipher
        self._state_manager = state_manager
        self._driver = driver or GoogleOAuthDriver(settings)
        self._principal_resolver = principal_resolver

    def begin(self) -> str:
        """Start the OAuth flow; identity is bound at callback from the verified grant."""
        code_verifier = secrets.token_urlsafe(96)
        state = self._state_manager.issue(context=code_verifier)
        return self._driver.authorization_url(state, code_verifier)

    async def complete(self, state: str, authorization_response: str) -> MailboxConnection:
        code_verifier = await self._state_manager.consume(state)
        if not code_verifier:
            raise ValueError("OAuth PKCE code verifier is missing")
        grant = await self._driver.exchange(state, authorization_response, code_verifier)
        if grant.scopes != self._settings.scopes:
            raise ValueError("Google granted an unexpected Gmail OAuth scope")
        principal = (
            await self._principal_resolver(grant.email_address)
            if self._principal_resolver is not None
            else None
        )
        now = datetime.now(UTC)
        connection = MailboxConnection(
            id=f"mbx_{uuid4().hex}",
            user_id=principal.user_id if principal is not None else grant.email_address,
            provider="gmail",
            external_account_id=grant.email_address.lower(),
            email_address=grant.email_address,
            encrypted_refresh_token=self._cipher.encrypt(grant.refresh_token),
            scopes=grant.scopes,
            status="active",
            created_at=now,
            updated_at=now,
        )
        return await self._repository.upsert(connection)

    async def disconnect(self, connection_id: str, user_id: str) -> bool:
        return await self._repository.delete(connection_id, user_id)


class GmailMailboxAdapter:
    """MailboxPort implementation containing read methods only."""

    def __init__(
        self,
        settings: GmailSettings,
        repository: MailboxConnectionRepository,
        cipher: TokenCipher,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._cipher = cipher
        self._service_cache: dict[str, Any] = {}
        self._service_cache_lock = asyncio.Lock()

    async def search_unread(
        self,
        connection_id: str,
        query: str,
        page_size: int,
        cursor: str | None = None,
    ) -> SearchPage:
        service = await self._service(connection_id)

        def execute() -> Mapping[str, Any]:
            request = (
                service.users()
                .messages()
                .list(
                    userId="me",
                    q=query,
                    maxResults=min(page_size, 500),
                    pageToken=cursor,
                    includeSpamTrash=False,
                )
            )
            return cast(Mapping[str, Any], request.execute())

        response = await self._call(execute)
        messages = tuple(
            MessageRef(str(item["id"]), str(item["threadId"]))
            for item in response.get("messages", ())
        )
        return SearchPage(
            messages=messages,
            next_cursor=_optional_string(response.get("nextPageToken")),
            estimated_total=int(response.get("resultSizeEstimate", len(messages))),
        )

    async def get_thread(
        self, connection_id: str, thread_id: str
    ) -> Sequence[EphemeralEmailEnvelope]:
        service = await self._service(connection_id)

        def execute() -> Mapping[str, Any]:
            request = service.users().threads().get(userId="me", id=thread_id, format="full")
            return cast(Mapping[str, Any], request.execute())

        response = await self._call(execute)
        return tuple(_parse_message(item) for item in response.get("messages", ()))

    async def get_message_received_at(self, connection_id: str, message_id: str) -> datetime:
        """Read only the internal Gmail timestamp needed for deterministic ordering."""
        service = await self._service(connection_id)

        def execute() -> Mapping[str, Any]:
            request = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=message_id,
                    format="metadata",
                    fields="id,internalDate",
                )
            )
            return cast(Mapping[str, Any], request.execute())

        response = await self._call(execute)
        return datetime.fromtimestamp(int(response["internalDate"]) / 1000, tz=UTC)

    async def download_attachment(
        self,
        connection_id: str,
        message_id: str,
        attachment_id: str,
        max_bytes: int,
    ) -> AsyncIterator[bytes]:
        """Deprecated (ADR-003 transition clause): compatibility code, never wired in production."""
        service = await self._service(connection_id)

        def execute() -> Mapping[str, Any]:
            request = (
                service.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=message_id, id=attachment_id)
            )
            return cast(Mapping[str, Any], request.execute())

        response = await self._call(execute)
        encoded = str(response.get("data", ""))
        data = _decode_gmail_data(encoded)
        if len(data) > max_bytes:
            raise ValueError("ATTACHMENT_TOO_LARGE")
        for offset in range(0, len(data), 64 * 1024):
            yield data[offset : offset + 64 * 1024]

    async def _service(self, connection_id: str) -> Any:
        cached = self._service_cache.get(connection_id)
        if cached is not None:
            return cached

        # Discovery resources are safe to cache, but the cache's first build
        # must be serialized so concurrent mailbox reads do not create several
        # services with independent credential state.
        async with self._service_cache_lock:
            cached = self._service_cache.get(connection_id)
            if cached is not None:
                return cached

            connection = await self._repository.get(connection_id)
            if connection is None or connection.status != "active":
                raise MailboxNotConnectedError(connection_id)
            try:
                refresh_token = self._cipher.decrypt(connection.encrypted_refresh_token)
            except ValueError as exc:
                raise MailboxReauthRequiredError(
                    "Stored Gmail token cannot be decrypted; reconnect Gmail"
                ) from exc
            credentials = Credentials(  # type: ignore[no-untyped-call]
                token=None,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=self._settings.client_id,
                client_secret=self._settings.client_secret,
                scopes=list(connection.scopes),
            )
            service = await asyncio.to_thread(
                build,
                "gmail",
                "v1",
                credentials=credentials,
                requestBuilder=_gmail_request_builder(credentials),
                cache_discovery=False,
            )
            self._service_cache[connection_id] = service
            return service

    async def _call(self, operation: Callable[[], Mapping[str, Any]]) -> Mapping[str, Any]:
        """Bounded retry budget (T5.4): transient API/transport failures —
        including token-refresh hiccups, which surface as transport errors —
        are retried with jittered backoff; authorization failures surface
        immediately and exhaust no budget."""
        for attempt in range(1, _RETRY_ATTEMPTS + 1):
            try:
                return cast(Mapping[str, Any], await asyncio.to_thread(operation))
            except RefreshError as exc:
                logger.warning("Gmail OAuth token refresh failed: %s", exc)
                raise MailboxReauthRequiredError("Gmail authorization must be renewed") from exc
            except HttpError as exc:
                status = getattr(exc.resp, "status", None)
                logger.warning(
                    "Gmail API HttpError (status=%s, attempt=%d/%d): %s",
                    status,
                    attempt,
                    _RETRY_ATTEMPTS,
                    exc,
                )
                if status in {401, 403}:
                    raise MailboxReauthRequiredError("Gmail authorization must be renewed") from exc
                if attempt >= _RETRY_ATTEMPTS or status not in _RETRYABLE_STATUSES:
                    raise MailboxTemporaryError("Gmail is temporarily unavailable") from exc
                await asyncio.sleep(_retry_delay(attempt))
            except TransportError as exc:
                logger.warning(
                    "Gmail API TransportError (attempt=%d/%d): %s", attempt, _RETRY_ATTEMPTS, exc
                )
                if attempt >= _RETRY_ATTEMPTS:
                    raise MailboxTemporaryError("Gmail is temporarily unavailable") from exc
                await asyncio.sleep(_retry_delay(attempt))
        raise AssertionError("unreachable: retry loop always returns or raises")


def _gmail_request_builder(credentials: Credentials) -> Callable[..., Any]:
    """Create Gmail requests with a transport that is not shared across threads."""

    def build_request(_http: Any, *args: Any, **kwargs: Any) -> Any:
        # googleapiclient discovery resources retain this builder and invoke it
        # for every request. httplib2.Http is not thread-safe, so each request
        # receives an isolated authorized transport before ``execute`` runs in
        # asyncio.to_thread.
        transport = AuthorizedHttp(credentials, http=httplib2.Http())
        return HttpRequest(transport, *args, **kwargs)

    return build_request


def _get_profile_email(credentials: Credentials) -> str:
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    profile = service.users().getProfile(userId="me").execute()
    email_address = str(profile.get("emailAddress", "")).strip()
    if not email_address:
        raise MailboxTemporaryError("Gmail profile did not contain an email address")
    return email_address


def _parse_message(raw: Mapping[str, Any]) -> EphemeralEmailEnvelope:
    has_payload = "payload" in raw
    payload = cast(Mapping[str, Any], raw.get("payload", {}))
    headers = {
        str(item.get("name", "")).lower(): str(item.get("value", ""))
        for item in payload.get("headers", ())
    }
    sender_name, sender_address = parseaddr(headers.get("from", ""))
    recipients = tuple(
        address
        for _, address in getaddresses([headers.get("to", ""), headers.get("cc", "")])
        if address
    )
    timestamp = datetime.fromtimestamp(int(raw.get("internalDate", 0)) / 1000, tz=UTC)
    message_id = str(raw["id"])
    thread_id = str(raw["threadId"])
    normalized_body, body_format = _extract_text(payload)
    return EphemeralEmailEnvelope(
        run_id="",
        user_id="",
        gmail_message_id=message_id,
        gmail_thread_id=thread_id,
        gmail_url=f"https://mail.google.com/mail/u/0/#inbox/{thread_id}",
        sender_name=sender_name or "",
        sender_email=sender_address,
        recipients=recipients,
        subject=headers.get("subject", "(Không có chủ đề)"),
        received_at=timestamp,
        labels=tuple(str(label) for label in raw.get("labelIds", ())),
        normalized_body=normalized_body,
        body_format=body_format,
        attachments_present=_has_attachments(payload),
        fetch_status=(
            FetchStatus.COMPLETE if has_payload and normalized_body else FetchStatus.PARTIAL
        ),
    )


def _extract_text(part: Mapping[str, Any]) -> tuple[str, BodyFormat]:
    plain: list[str] = []
    rich: list[str] = []

    def visit(current: Mapping[str, Any]) -> None:
        mime = str(current.get("mimeType", "")).lower()
        body = cast(Mapping[str, Any], current.get("body", {}))
        data = body.get("data")
        if data and mime in {"text/plain", "text/html"}:
            text = _decode_gmail_data(str(data)).decode("utf-8", errors="replace")
            (plain if mime == "text/plain" else rich).append(text)
        for child in current.get("parts", ()):
            visit(cast(Mapping[str, Any], child))

    visit(part)
    if plain:
        plain_text = "\n".join(plain).strip()
        links = [link for rich_part in rich for link in _extract_html_links(rich_part)]
        missing_links = [link for link in dict.fromkeys(links) if link not in plain_text]
        if missing_links:
            plain_text += "\n\nLiên kết trong email:\n" + "\n".join(missing_links)
        return plain_text, BodyFormat.TEXT
    if not rich:
        return "", BodyFormat.TEXT
    return _html_to_text("\n".join(rich)).strip(), BodyFormat.HTML_CONVERTED


def _has_attachments(part: Mapping[str, Any]) -> bool:
    """Presence-only detection per ADR-003: attachment content is never downloaded."""

    def visit(current: Mapping[str, Any]) -> bool:
        body = cast(Mapping[str, Any], current.get("body", {}))
        if str(current.get("filename", "")).strip() and body.get("attachmentId"):
            return True
        return any(visit(cast(Mapping[str, Any], child)) for child in current.get("parts", ()))

    return visit(part)


def _decode_gmail_data(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _html_to_text(value: str) -> str:
    without_tags = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    without_tags = re.sub(
        r"(?is)<a\b[^>]*?href\s*=\s*(['\"])(.*?)\1[^>]*>(.*?)</a>",
        _anchor_to_text,
        without_tags,
    )
    without_tags = re.sub(r"(?s)<[^>]+>", " ", without_tags)
    return re.sub(r"\s+", " ", html.unescape(without_tags))


def _anchor_to_text(match: re.Match[str]) -> str:
    url = html.unescape(match.group(2)).strip()
    label = re.sub(r"(?s)<[^>]+>", " ", match.group(3))
    label = re.sub(r"\s+", " ", html.unescape(label)).strip() or url
    if not url.startswith(("https://", "http://")):
        return label
    return f" {label} [{url}] "


def _extract_html_links(value: str) -> list[str]:
    anchor_pattern = r"(?is)<a\b[^>]*?href\s*=\s*(['\"])(.*?)\1[^>]*>(.*?)</a>"
    links = []
    for match in re.finditer(anchor_pattern, value):
        rendered = _anchor_to_text(match).strip()
        if "[http" in rendered:
            links.append(rendered)
    return links


def _optional_string(value: object) -> str | None:
    return str(value) if value else None
