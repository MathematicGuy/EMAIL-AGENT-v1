"""Google OAuth and read-only Gmail API adapters."""

import asyncio
import base64
import html
import re
import secrets
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parseaddr
from typing import Any, Protocol, cast
from uuid import uuid4

from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow  # type: ignore[import-untyped]
from googleapiclient.discovery import build  # type: ignore[import-untyped]
from googleapiclient.errors import HttpError  # type: ignore[import-untyped]

from mail_todo.application.contracts import MessageRef, SearchPage
from mail_todo.application.ports import MailboxConnectionRepository
from mail_todo.domain import AttachmentRef, EmailEnvelope, MailboxConnection

from .config import GmailSettings
from .security import OAuthStateManager, TokenCipher


class MailboxNotConnectedError(LookupError):
    pass


class MailboxReauthRequiredError(RuntimeError):
    pass


class MailboxTemporaryError(RuntimeError):
    pass


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
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._cipher = cipher
        self._state_manager = state_manager
        self._driver = driver or GoogleOAuthDriver(settings)

    def begin(self, user_id: str) -> str:
        code_verifier = secrets.token_urlsafe(96)
        state = self._state_manager.issue(user_id, context=code_verifier)
        return self._driver.authorization_url(state, code_verifier)

    async def complete(self, state: str, authorization_response: str) -> MailboxConnection:
        user_id, code_verifier = await self._state_manager.consume_with_context(state)
        if not code_verifier:
            raise ValueError("OAuth PKCE code verifier is missing")
        grant = await self._driver.exchange(state, authorization_response, code_verifier)
        if grant.scopes != self._settings.scopes:
            raise ValueError("Google granted an unexpected Gmail OAuth scope")
        now = datetime.now(UTC)
        connection = MailboxConnection(
            id=f"mbx_{uuid4().hex}",
            user_id=user_id,
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

    async def list_connections(self, user_id: str) -> Sequence[MailboxConnection]:
        return await self._repository.list_for_user(user_id)

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

    async def get_thread(self, connection_id: str, thread_id: str) -> Sequence[EmailEnvelope]:
        service = await self._service(connection_id)

        def execute() -> Mapping[str, Any]:
            request = service.users().threads().get(userId="me", id=thread_id, format="full")
            return cast(Mapping[str, Any], request.execute())

        response = await self._call(execute)
        return tuple(_parse_message(item) for item in response.get("messages", ()))

    async def download_attachment(
        self,
        connection_id: str,
        message_id: str,
        attachment_id: str,
        max_bytes: int,
    ) -> AsyncIterator[bytes]:
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
        connection = await self._repository.get(connection_id)
        if connection is None or connection.status != "active":
            raise MailboxNotConnectedError(connection_id)
        credentials = Credentials(  # type: ignore[no-untyped-call]
            token=None,
            refresh_token=self._cipher.decrypt(connection.encrypted_refresh_token),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self._settings.client_id,
            client_secret=self._settings.client_secret,
            scopes=list(connection.scopes),
        )
        return await asyncio.to_thread(
            build, "gmail", "v1", credentials=credentials, cache_discovery=False
        )

    async def _call(self, operation: Callable[[], Mapping[str, Any]]) -> Mapping[str, Any]:
        try:
            return cast(Mapping[str, Any], await asyncio.to_thread(operation))
        except RefreshError as exc:
            raise MailboxReauthRequiredError("Gmail authorization must be renewed") from exc
        except HttpError as exc:
            status = getattr(exc.resp, "status", None)
            if status in {401, 403}:
                raise MailboxReauthRequiredError("Gmail authorization must be renewed") from exc
            raise MailboxTemporaryError("Gmail is temporarily unavailable") from exc


def _get_profile_email(credentials: Credentials) -> str:
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    profile = service.users().getProfile(userId="me").execute()
    email_address = str(profile.get("emailAddress", "")).strip()
    if not email_address:
        raise MailboxTemporaryError("Gmail profile did not contain an email address")
    return email_address


def _parse_message(raw: Mapping[str, Any]) -> EmailEnvelope:
    payload = cast(Mapping[str, Any], raw.get("payload", {}))
    headers = {
        str(item.get("name", "")).lower(): str(item.get("value", ""))
        for item in payload.get("headers", ())
    }
    sender_name, sender_address = parseaddr(headers.get("from", ""))
    timestamp = datetime.fromtimestamp(int(raw.get("internalDate", 0)) / 1000, tz=UTC)
    message_id = str(raw["id"])
    thread_id = str(raw["threadId"])
    return EmailEnvelope(
        provider_message_id=message_id,
        provider_thread_id=thread_id,
        deep_link=f"https://mail.google.com/mail/u/0/#inbox/{thread_id}",
        subject=headers.get("subject", "(Không có chủ đề)"),
        sender_name=sender_name or None,
        sender_address=sender_address,
        sent_at=timestamp,
        received_at=timestamp,
        text_body=_extract_text(payload),
        attachments=tuple(_extract_attachments(payload)),
    )


def _extract_text(part: Mapping[str, Any]) -> str:
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
        return plain_text
    return _html_to_text("\n".join(rich)).strip()


def _extract_attachments(part: Mapping[str, Any]) -> list[AttachmentRef]:
    attachments: list[AttachmentRef] = []

    def visit(current: Mapping[str, Any]) -> None:
        body = cast(Mapping[str, Any], current.get("body", {}))
        filename = str(current.get("filename", "")).strip()
        attachment_id = body.get("attachmentId")
        if filename and attachment_id:
            attachments.append(
                AttachmentRef(
                    attachment_id=str(attachment_id),
                    filename=filename,
                    declared_mime_type=str(current.get("mimeType", "application/octet-stream")),
                    size_bytes=int(body["size"]) if body.get("size") is not None else None,
                )
            )
        for child in current.get("parts", ()):
            visit(cast(Mapping[str, Any], child))

    visit(part)
    return attachments


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
