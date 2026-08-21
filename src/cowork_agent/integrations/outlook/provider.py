"""Microsoft OAuth and read-only Microsoft Graph mail adapter."""

import asyncio
import base64
import binascii
import hashlib
import json
import random
import secrets
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from urllib.parse import quote, unquote, urlencode, urlparse
from uuid import uuid4

import httpx

from cowork_agent.domain import MailboxConnection
from cowork_agent.domain.target_contracts import (
    EphemeralEmailEnvelope,
    FetchStatus,
)
from cowork_agent.features.email_action_plan.ports import MailboxConnectionRepository
from cowork_agent.features.email_action_plan.schemas import MessageRef, SearchPage
from cowork_agent.integrations.gmail.auth import OAuthStateManager, TokenCipher
from cowork_agent.integrations.mailbox.errors import (
    MailboxNotConnectedError,
    MailboxPermissionDeniedError,
    MailboxRateLimitedError,
    MailboxReauthRequiredError,
    MailboxTemporaryError,
)
from cowork_agent.integrations.mailbox.normalization import normalize_body

MICROSOFT_MAIL_READ_SCOPE = "https://graph.microsoft.com/Mail.Read"
MICROSOFT_DEFAULT_SCOPES = (
    "openid",
    "profile",
    "email",
    "offline_access",
    MICROSOFT_MAIL_READ_SCOPE,
)
_RETRY_ATTEMPTS = 3
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_TOKEN_EXPIRY_SKEW_SECONDS = 60.0


class OutlookSettings(Protocol):
    @property
    def client_id(self) -> str: ...

    @property
    def client_secret(self) -> str: ...

    @property
    def tenant(self) -> str: ...

    @property
    def redirect_uri(self) -> str: ...

    @property
    def scopes(self) -> tuple[str, ...]: ...

    @property
    def graph_base_url(self) -> str: ...


@dataclass(frozen=True, slots=True)
class OutlookOAuthGrant:
    external_account_id: str
    email_address: str
    refresh_token: str
    scopes: tuple[str, ...]


class OutlookOAuthDriver(Protocol):
    def authorization_url(self, state: str, code_verifier: str) -> str: ...
    async def exchange(self, code: str, code_verifier: str) -> OutlookOAuthGrant: ...


class MicrosoftOAuthDriver:
    def __init__(
        self, settings: OutlookSettings, client: httpx.AsyncClient | None = None
    ) -> None:
        _validate_scopes(settings.scopes)
        self._settings = settings
        self._client = client

    def authorization_url(self, state: str, code_verifier: str) -> str:
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).rstrip(b"=").decode()
        params = {
            "client_id": self._settings.client_id,
            "response_type": "code",
            "redirect_uri": self._settings.redirect_uri,
            "response_mode": "query",
            "scope": " ".join(self._settings.scopes),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{self._authority}/oauth2/v2.0/authorize?{urlencode(params)}"

    async def exchange(self, code: str, code_verifier: str) -> OutlookOAuthGrant:
        response = await self._post_token(
            {
                "client_id": self._settings.client_id,
                "client_secret": self._settings.client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._settings.redirect_uri,
                "scope": " ".join(self._settings.scopes),
                "code_verifier": code_verifier,
            }
        )
        refresh_token = str(response.get("refresh_token", ""))
        if not refresh_token:
            raise MailboxReauthRequiredError("Microsoft did not return a refresh token")
        claims = _jwt_claims(str(response.get("id_token", "")))
        external_id = str(claims.get("oid") or claims.get("sub") or "").strip()
        email_address = str(
            claims.get("preferred_username") or claims.get("email") or claims.get("name") or ""
        ).strip()
        if not external_id or not email_address:
            raise MailboxTemporaryError("Microsoft account identity was incomplete")
        scopes = tuple(str(response.get("scope", "")).split())
        if not _has_mail_read(scopes):
            raise MailboxPermissionDeniedError("Microsoft did not grant Mail.Read")
        return OutlookOAuthGrant(external_id, email_address, refresh_token, scopes)

    async def _post_token(self, form: Mapping[str, str]) -> Mapping[str, Any]:
        if self._client is not None:
            response = await self._client.post(self._token_url, data=form)
        else:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(self._token_url, data=form)
        _raise_token_error(response)
        return _response_json(response)

    @property
    def _authority(self) -> str:
        return f"https://login.microsoftonline.com/{quote(self._settings.tenant, safe='')}"

    @property
    def _token_url(self) -> str:
        return f"{self._authority}/oauth2/v2.0/token"


class OutlookConnectionService:
    """Link Outlook to the user who owns an existing active Gmail connection."""

    def __init__(
        self,
        settings: OutlookSettings,
        repository: MailboxConnectionRepository,
        cipher: TokenCipher,
        state_manager: OAuthStateManager,
        driver: OutlookOAuthDriver | None = None,
    ) -> None:
        _validate_scopes(settings.scopes)
        self._settings = settings
        self._repository = repository
        self._cipher = cipher
        self._state_manager = state_manager
        self._driver = driver or MicrosoftOAuthDriver(settings)

    def begin(self, owner: MailboxConnection) -> str:
        if owner.status != "active" or owner.provider != "gmail":
            raise MailboxNotConnectedError(owner.id)
        code_verifier = secrets.token_urlsafe(96)
        context = json.dumps(
            {"owner_connection_id": owner.id, "user_id": owner.user_id, "pkce": code_verifier},
            separators=(",", ":"),
        )
        state = self._state_manager.issue(context=context)
        return self._driver.authorization_url(state, code_verifier)

    async def complete(self, state: str, code: str) -> MailboxConnection:
        raw_context = await self._state_manager.consume(state)
        try:
            context = cast(Mapping[str, Any], json.loads(raw_context or ""))
            owner_connection_id = str(context["owner_connection_id"])
            expected_user_id = str(context["user_id"])
            code_verifier = str(context["pkce"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Outlook OAuth state context is invalid") from exc
        owner = await self._gmail_owner(owner_connection_id)
        if owner.user_id != expected_user_id:
            raise ValueError("Outlook OAuth owner binding changed")
        grant = await self._driver.exchange(code, code_verifier)
        if not _has_mail_read(grant.scopes):
            raise MailboxPermissionDeniedError("Microsoft did not grant Mail.Read")
        now = datetime.now(UTC)
        return await self._repository.upsert(
            MailboxConnection(
                id=f"mbx_{uuid4().hex}",
                user_id=owner.user_id,
                provider="outlook",
                external_account_id=grant.external_account_id,
                email_address=grant.email_address,
                encrypted_refresh_token=self._cipher.encrypt(grant.refresh_token),
                scopes=grant.scopes,
                status="active",
                created_at=now,
                updated_at=now,
            )
        )

    async def disconnect(self, connection_id: str, user_id: str) -> bool:
        return await self._repository.delete(connection_id, user_id)

    async def _gmail_owner(self, connection_id: str) -> MailboxConnection:
        connection = await self._repository.get(connection_id)
        if (
            connection is None
            or connection.status != "active"
            or connection.provider != "gmail"
        ):
            raise MailboxNotConnectedError(connection_id)
        return connection


@dataclass(frozen=True, slots=True)
class _CachedToken:
    value: str
    expires_at: float


class OutlookMailboxAdapter:
    """Read-only MailboxPort backed by Microsoft Graph."""

    def __init__(
        self,
        settings: OutlookSettings,
        repository: MailboxConnectionRepository,
        cipher: TokenCipher,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        _validate_scopes(settings.scopes)
        self._settings = settings
        self._repository = repository
        self._cipher = cipher
        self._client = client
        self._token_cache: dict[str, _CachedToken] = {}
        self._token_lock = asyncio.Lock()

    async def search_unread(
        self,
        connection_id: str,
        query: str,
        page_size: int,
        cursor: str | None = None,
    ) -> SearchPage:
        if not _is_unread_inbox_query(query):
            raise ValueError("Outlook supports only the unread inbox filter")
        base_path = "/me/mailFolders/inbox/messages"
        url = cursor or f"{self._graph_base}{base_path}"
        if cursor:
            self._validate_graph_url(cursor, base_path)
        params = None if cursor else {
            "$filter": "isRead eq false",
            "$select": "id,conversationId",
            "$top": str(min(max(page_size, 1), 1000)),
            "$count": "true",
        }
        response = await self._request_json(connection_id, url, params=params)
        messages = tuple(
            MessageRef(
                _outlook_id(str(item["id"])),
                _outlook_id(str(item.get("conversationId") or item["id"])),
            )
            for raw in response.get("value", ())
            if isinstance(raw, Mapping)
            for item in (raw,)
        )
        next_link = _optional_string(response.get("@odata.nextLink"))
        if next_link:
            self._validate_graph_url(next_link, base_path)
        estimated = response.get("@odata.count")
        return SearchPage(messages, next_link, int(estimated) if estimated is not None else None)

    async def get_thread(
        self, connection_id: str, thread_id: str
    ) -> Sequence[EphemeralEmailEnvelope]:
        provider_thread_id = _strip_outlook_id(thread_id)
        base_path = "/me/messages"
        url: str | None = f"{self._graph_base}{base_path}"
        params: Mapping[str, str] | None = {
            "$filter": f"conversationId eq '{provider_thread_id.replace(chr(39), chr(39) * 2)}'",
            "$select": (
                "id,conversationId,subject,from,toRecipients,ccRecipients,bccRecipients,"
                "sentDateTime,receivedDateTime,body,hasAttachments,webLink,categories"
            ),
            "$top": "100",
        }
        messages: list[EphemeralEmailEnvelope] = []
        while url:
            response = await self._request_json(connection_id, url, params=params)
            for raw in response.get("value", ()):
                if isinstance(raw, Mapping):
                    messages.append(_parse_message(raw))
            url = _optional_string(response.get("@odata.nextLink"))
            params = None
            if url:
                self._validate_graph_url(url, base_path)
        messages.sort(key=lambda item: item.received_at)
        return tuple(messages)

    async def get_message_received_at(
        self, connection_id: str, message_id: str
    ) -> datetime:
        provider_message_id = _strip_outlook_id(message_id)
        path = f"/me/messages/{quote(provider_message_id, safe='')}"
        response = await self._request_json(
            connection_id,
            f"{self._graph_base}{path}",
            params={"$select": "receivedDateTime"},
        )
        return _parse_datetime(str(response.get("receivedDateTime") or ""))

    async def download_attachment(
        self,
        connection_id: str,
        message_id: str,
        attachment_id: str,
        max_bytes: int,
    ) -> AsyncIterator[bytes]:
        """Attachment content is intentionally unavailable under ADR-003."""
        del connection_id, message_id, attachment_id, max_bytes
        if False:  # pragma: no cover - retains AsyncIterator protocol shape
            yield b""
        raise RuntimeError("Outlook attachment content is not processed")

    async def _request_json(
        self,
        connection_id: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> Mapping[str, Any]:
        token = await self._access_token(connection_id)
        for attempt in range(1, _RETRY_ATTEMPTS + 1):
            try:
                response = await self._request(url, params, token)
            except httpx.HTTPError as exc:
                if attempt == _RETRY_ATTEMPTS:
                    raise MailboxTemporaryError("Microsoft Graph request failed") from exc
                await asyncio.sleep(_retry_delay(attempt, None))
                continue
            if response.status_code < 400:
                return _response_json(response)
            if response.status_code not in _RETRYABLE_STATUSES or attempt == _RETRY_ATTEMPTS:
                _raise_graph_error(response)
            await asyncio.sleep(_retry_delay(attempt, response))
        raise AssertionError("unreachable")

    async def _request(
        self, url: str, params: Mapping[str, str] | None, token: str
    ) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {token}",
            "ConsistencyLevel": "eventual",
            "Prefer": 'IdType="ImmutableId", outlook.body-content-type="html"',
        }
        if self._client is not None:
            return await self._client.get(url, params=params, headers=headers)
        async with httpx.AsyncClient(timeout=30) as client:
            return await client.get(url, params=params, headers=headers)

    async def _access_token(self, connection_id: str) -> str:
        now = time.monotonic()
        cached = self._token_cache.get(connection_id)
        if cached is not None and cached.expires_at > now + _TOKEN_EXPIRY_SKEW_SECONDS:
            return cached.value
        async with self._token_lock:
            now = time.monotonic()
            cached = self._token_cache.get(connection_id)
            if cached is not None and cached.expires_at > now + _TOKEN_EXPIRY_SKEW_SECONDS:
                return cached.value
            connection = await self._repository.get(connection_id)
            if (
                connection is None
                or connection.status != "active"
                or connection.provider != "outlook"
            ):
                raise MailboxNotConnectedError(connection_id)
            try:
                refresh_token = self._cipher.decrypt(connection.encrypted_refresh_token)
            except ValueError as exc:
                raise MailboxReauthRequiredError(
                    "Stored Outlook token cannot be decrypted"
                ) from exc
            data = await self._refresh(refresh_token)
            access_token = str(data.get("access_token", ""))
            if not access_token:
                raise MailboxReauthRequiredError("Microsoft did not return an access token")
            rotated = str(data.get("refresh_token", ""))
            if rotated and rotated != refresh_token:
                await self._repository.upsert(
                    replace(
                        connection,
                        encrypted_refresh_token=self._cipher.encrypt(rotated),
                        updated_at=datetime.now(UTC),
                    )
                )
            try:
                expires_in = max(float(data.get("expires_in", 3600)), 0.0)
            except (TypeError, ValueError):
                expires_in = 3600.0
            self._token_cache[connection_id] = _CachedToken(
                access_token, time.monotonic() + expires_in
            )
            return access_token

    async def _refresh(self, refresh_token: str) -> Mapping[str, Any]:
        url = (
            f"https://login.microsoftonline.com/{quote(self._settings.tenant, safe='')}"
            "/oauth2/v2.0/token"
        )
        form = {
            "client_id": self._settings.client_id,
            "client_secret": self._settings.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": " ".join(self._settings.scopes),
        }
        for attempt in range(1, _RETRY_ATTEMPTS + 1):
            try:
                if self._client is not None:
                    response = await self._client.post(url, data=form)
                else:
                    async with httpx.AsyncClient(timeout=30) as client:
                        response = await client.post(url, data=form)
            except httpx.HTTPError as exc:
                if attempt == _RETRY_ATTEMPTS:
                    raise MailboxTemporaryError("Microsoft token refresh failed") from exc
                await asyncio.sleep(_retry_delay(attempt, None))
                continue
            if response.status_code < 400:
                return _response_json(response)
            if response.status_code not in _RETRYABLE_STATUSES or attempt == _RETRY_ATTEMPTS:
                _raise_token_error(response)
            await asyncio.sleep(_retry_delay(attempt, response))
        raise AssertionError("unreachable")

    @property
    def _graph_base(self) -> str:
        return self._settings.graph_base_url.rstrip("/")

    def _validate_graph_url(self, value: str, required_path: str) -> None:
        candidate = urlparse(value)
        base = urlparse(self._graph_base)
        expected_prefix = f"{base.path.rstrip('/')}{required_path}"
        allowed_paths = {expected_prefix}
        if required_path == "/me/mailFolders/inbox/messages":
            # Graph canonicalizes the Inbox well-known folder to OData's
            # ``mailFolders('inbox')`` form in some @odata.nextLink values.
            # Keep the allowed path narrow: arbitrary folder IDs remain invalid.
            allowed_paths.add(
                f"{base.path.rstrip('/')}/me/mailFolders('inbox')/messages"
            )
        if (
            candidate.scheme != base.scheme
            or candidate.netloc != base.netloc
            or candidate.username is not None
            or candidate.password is not None
            or unquote(candidate.path).casefold()
            not in {path.casefold() for path in allowed_paths}
        ):
            raise ValueError("Invalid Outlook paging cursor")


def _parse_message(raw: Mapping[str, Any]) -> EphemeralEmailEnvelope:
    sender = cast(Mapping[str, Any], raw.get("from") or {})
    sender_address = cast(Mapping[str, Any], sender.get("emailAddress") or {})
    body = cast(Mapping[str, Any], raw.get("body") or {})
    content = str(body.get("content") or "")
    content_type = str(body.get("contentType") or "").lower()
    normalized, body_format, source_links = normalize_body(
        (content,) if content and content_type != "html" else (),
        (content,) if content and content_type == "html" else (),
    )
    message_id = str(raw["id"])
    thread_id = str(raw.get("conversationId") or message_id)
    received = _parse_datetime(str(raw.get("receivedDateTime") or raw.get("sentDateTime") or ""))
    recipients = _recipients(raw)
    deep_link = _optional_string(raw.get("webLink")) or (
        f"https://outlook.office.com/mail/deeplink/read/{quote(message_id, safe='')}"
    )
    return EphemeralEmailEnvelope(
        run_id="",
        user_id="",
        gmail_message_id=_outlook_id(message_id),
        gmail_thread_id=_outlook_id(thread_id),
        gmail_url=deep_link,
        sender_name=str(sender_address.get("name") or ""),
        sender_email=str(sender_address.get("address") or ""),
        recipients=recipients,
        subject=str(raw.get("subject") or "(Không có chủ đề)"),
        received_at=received,
        labels=tuple(str(item) for item in raw.get("categories", ())),
        normalized_body=normalized,
        body_format=body_format,
        attachments_present=bool(raw.get("hasAttachments")),
        fetch_status=FetchStatus.COMPLETE if normalized else FetchStatus.PARTIAL,
        source_links=source_links,
    )


def _recipients(raw: Mapping[str, Any]) -> tuple[str, ...]:
    result: list[str] = []
    for key in ("toRecipients", "ccRecipients", "bccRecipients"):
        for item in raw.get(key, ()):
            if not isinstance(item, Mapping):
                continue
            address = item.get("emailAddress")
            if isinstance(address, Mapping):
                value = str(address.get("address") or "").strip()
                if value:
                    result.append(value)
    return tuple(result)


def _validate_scopes(scopes: Sequence[str]) -> None:
    if set(scopes) != set(MICROSOFT_DEFAULT_SCOPES):
        raise ValueError("Outlook must use only Mail.Read and standard OIDC/offline scopes")


def _has_mail_read(scopes: Sequence[str]) -> bool:
    granted = {item.lower() for item in scopes}
    return bool({MICROSOFT_MAIL_READ_SCOPE.lower(), "mail.read"} & granted)


def _outlook_id(value: str) -> str:
    return value if value.startswith("outlook:") else f"outlook:{value}"


def _strip_outlook_id(value: str) -> str:
    return value.removeprefix("outlook:")


def _parse_datetime(value: str) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, tz=UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _jwt_claims(token: str) -> Mapping[str, Any]:
    try:
        payload = token.split(".")[1]
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        value = json.loads(decoded)
        if not isinstance(value, Mapping):
            raise ValueError
        return value
    except (
        IndexError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
    ) as exc:
        raise MailboxTemporaryError("Microsoft ID token was invalid") from exc


def _response_json(response: httpx.Response) -> Mapping[str, Any]:
    try:
        value = response.json()
    except ValueError as exc:
        raise MailboxTemporaryError("Microsoft returned an invalid response") from exc
    if not isinstance(value, Mapping):
        raise MailboxTemporaryError("Microsoft returned an invalid response")
    return cast(Mapping[str, Any], value)


def _raise_token_error(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    if response.status_code in {400, 401}:
        raise MailboxReauthRequiredError("Microsoft OAuth credential was rejected")
    if response.status_code == 403:
        raise MailboxPermissionDeniedError("Microsoft OAuth permission was denied")
    if response.status_code == 429:
        raise MailboxRateLimitedError("Microsoft OAuth is rate limited")
    raise MailboxTemporaryError("Microsoft OAuth is temporarily unavailable")


def _raise_graph_error(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    if response.status_code == 401:
        raise MailboxReauthRequiredError("Microsoft Graph rejected the access token")
    if response.status_code == 403:
        raise MailboxPermissionDeniedError("Microsoft Graph denied Mail.Read access")
    if response.status_code == 429:
        raise MailboxRateLimitedError("Microsoft Graph rate limit exceeded")
    raise MailboxTemporaryError("Microsoft Graph is temporarily unavailable")


def _retry_delay(attempt: int, response: httpx.Response | None) -> float:
    if response is not None:
        try:
            return min(max(float(response.headers.get("Retry-After", "")), 0.0), 5.0)
        except ValueError:
            pass
    return random.uniform(0.0, min(4.0, 0.5 * 2 ** (attempt - 1)))


def _optional_string(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _is_unread_inbox_query(query: str) -> bool:
    """Accept the shared Gmail-normalized unread-inbox query as a Graph no-op.

    ``normalize_query`` adds ``category:primary`` for the legacy Gmail
    workflow. Microsoft Graph has no equivalent category, so Outlook ignores
    precisely that additional term while retaining a strict read-only filter.
    """
    normalized = query.strip().lower()
    if normalized == "unread_inbox":
        return True
    terms = frozenset(normalized.split())
    required = frozenset({"is:unread", "in:inbox"})
    return required <= terms and terms <= required | {"category:primary"}
