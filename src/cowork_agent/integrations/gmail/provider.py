"""Google OAuth and read-only Gmail API adapters."""

import asyncio
import base64
import html
import logging
import random
import re
import secrets
import ssl
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import getaddresses, parseaddr
from typing import Any, Protocol, cast
from urllib.parse import urlparse
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
    EmailSourceLink,
    EphemeralEmailEnvelope,
    FetchStatus,
)
from cowork_agent.features.email_action_plan.ports import MailboxConnectionRepository
from cowork_agent.features.email_action_plan.schemas import MessageRef, SearchPage
from cowork_agent.identity import VerifiedPrincipal
from cowork_agent.integrations.mailbox.errors import (
    MailboxNotConnectedError,
    MailboxTemporaryError,
)
from cowork_agent.integrations.mailbox.errors import (
    MailboxReauthRequiredError as ProviderMailboxReauthRequiredError,
)
from cowork_agent.integrations.mailbox.normalization import normalize_body

from .auth import OAuthStateManager, TokenCipher

logger = logging.getLogger(__name__)


class MailboxReauthRequiredError(ProviderMailboxReauthRequiredError):
    """Backward-compatible Gmail error with provider-neutral ancestry."""

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
            except (TransportError, httplib2.error.HttpLib2Error, ssl.SSLError, OSError) as exc:
                logger.warning(
                    "Gmail API transport/network error (%s, attempt=%d/%d): %s",
                    type(exc).__name__,
                    attempt,
                    _RETRY_ATTEMPTS,
                    exc,
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
    try:
        service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        profile = service.users().getProfile(userId="me").execute()
        email_address = str(profile.get("emailAddress", "")).strip()
        if not email_address:
            raise MailboxTemporaryError("Gmail profile did not contain an email address")
        return email_address
    except HttpError as exc:
        status = getattr(exc.resp, "status", None)
        if status in {401, 403}:
            raise MailboxReauthRequiredError("Gmail authorization must be renewed") from exc
        raise MailboxTemporaryError("Gmail is temporarily unavailable") from exc
    except (TransportError, httplib2.error.HttpLib2Error, ssl.SSLError, OSError) as exc:
        raise MailboxTemporaryError("Gmail is temporarily unavailable") from exc


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
    normalized_body, body_format, source_links = _extract_text(payload)
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
        source_links=source_links,
    )


def _extract_text(
    part: Mapping[str, Any],
) -> tuple[str, BodyFormat, tuple[EmailSourceLink, ...]]:
    plain: list[str] = []
    rich: list[str] = []

    def visit(current: Mapping[str, Any]) -> None:
        mime = str(current.get("mimeType", "")).lower()
        body = cast(Mapping[str, Any], current.get("body", {}))
        data = body.get("data")
        if data and mime in {"text/plain", "text/html"}:
            text = _strip_suspicious_format_controls(
                _decode_gmail_data(str(data)).decode("utf-8", errors="replace")
            )
            (plain if mime == "text/plain" else rich).append(text)
        for child in current.get("parts", ()):
            visit(cast(Mapping[str, Any], child))

    visit(part)
    return normalize_body(plain, rich)


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


@dataclass(slots=True)
class _LinkCollector:
    _links: list[EmailSourceLink]
    _positions: dict[str, int]

    def __init__(self) -> None:
        self._links = []
        self._positions = {}

    def add(self, url: str, label: str | None = None) -> str:
        position = self._positions.get(url)
        if position is not None:
            existing = self._links[position]
            if existing.label is None and label:
                self._links[position] = EmailSourceLink(existing.ref, label, existing.url)
            return existing.ref
        ref = f"link{len(self._links) + 1}"
        self._positions[url] = len(self._links)
        self._links.append(EmailSourceLink(ref=ref, label=label, url=url))
        return ref

    def as_tuple(self) -> tuple[EmailSourceLink, ...]:
        return tuple(self._links)


_URL_PATTERN = re.compile(r"https?://[^\s<>\"'\[\]]+", re.IGNORECASE)
_MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]\r\n]+)\]\((https?://[^\s)]+)\)", re.IGNORECASE)
_HTML_LIKE_PLAIN_PATTERN = re.compile(
    r"(?is)<!--|</?(?:a|body|br|div|html|p|strong|table|tbody|td|th|thead|tr|"
    r"v:[\w-]+|w:[\w-]+)\b"
)
_TRAILING_URL_PUNCTUATION = ".,;:!?"
_ALWAYS_REMOVE_FORMAT_CONTROLS = frozenset("\u00ad\u034f\u200b\u200e\u200f\ufeff")
_SEPARATOR_LINE_PATTERN = re.compile(r"^[ \t]*[-=_*|—–]{2,}[ \t]*$")
_MULTI_REF_OPEN_WRAPPER = re.compile(
    r"\[([^\]\r\n]+)\]\(\s*((?:\[link\d+\]\s*){2,})\r?$", re.MULTILINE
)
_CROSS_LINE_SINGLE_REF_WRAPPER = re.compile(
    r"\[([^\]\r\n]+)\]\(\s*(\[link\d+\])\r?\n[^\[\]()\r\n]*\)",
    re.MULTILINE,
)


def _split_url_punctuation(value: str) -> tuple[str, str]:
    url = value.rstrip(_TRAILING_URL_PUNCTUATION)
    return url, value[len(url) :]


def _iter_urls(value: str) -> list[str]:
    urls: list[str] = []
    for match in _URL_PATTERN.finditer(value):
        url, _ = _split_url_punctuation(match.group(0))
        if url:
            urls.append(url)
    return urls


def _replace_urls(value: str, links: _LinkCollector) -> str:
    def replace(match: re.Match[str]) -> str:
        url, punctuation = _split_url_punctuation(match.group(0))
        if not url:
            return match.group(0)
        return f"[{links.add(url)}]{punctuation}"

    replaced = _URL_PATTERN.sub(replace, value)
    replaced = re.sub(r"\[\[(link\d+)\]\]", r"[\1]", replaced)
    return re.sub(r"<\[(link\d+)\]>", r"[\1]", replaced)


def _replace_markdown_links(value: str, links: _LinkCollector) -> str:
    def replace(match: re.Match[str]) -> str:
        label = re.sub(r"\s+", " ", match.group(1)).strip()
        url = match.group(2)
        source_label = _source_link_label(label, url)
        ref = links.add(url, source_label)
        return f"{source_label} [{ref}]" if source_label else ""

    return _MARKDOWN_LINK_PATTERN.sub(replace, value)


def _plain_to_text(value: str, links: _LinkCollector) -> str:
    value = html.unescape(value)
    if _HTML_LIKE_PLAIN_PATTERN.search(value):
        return _html_to_text(value, links)
    normalized = _replace_urls(_replace_markdown_links(value, links), links)
    return _normalize_canonical_ref_wrappers(normalized)


def _normalize_canonical_ref_wrappers(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        label = re.sub(r"\s+", " ", match.group(1)).strip()
        refs = " ".join(re.findall(r"\[link\d+\]", match.group(2)))
        return f"{label} {refs}".strip()

    normalized = _MULTI_REF_OPEN_WRAPPER.sub(replace, value)
    return _CROSS_LINE_SINGLE_REF_WRAPPER.sub(replace, normalized)


def _source_link_label(label: str, url: str) -> str | None:
    if not label or label == url or re.match(r"(?i)^https?://", label):
        return None
    return label


_FOOTER_LINK_LABEL_PATTERN = re.compile(
    r"(?i)^(?:unsubscribe|switch to the weekly digest|careers?|help center|"
    r"privacy(?: policy)?|terms(?: of service)?|control your recommendations|"
    r"become a (?:medium )?member|get (?:medium )?on (?:the )?(?:app store|google play))$"
)
_FOOTER_LINK_PATH_PATTERN = re.compile(
    r"(?i)(?:unsubscribe|privacy|terms-of-service|settings/notifications|"
    r"missioncontrol|jobs-at-|/plans(?:/|$))"
)


def _include_in_llm_link_appendix(link: EmailSourceLink) -> bool:
    """Keep semantic content links while retaining all links in source metadata."""

    if link.label is None:
        return False
    label = re.sub(r"\s+", " ", link.label).strip()
    if _FOOTER_LINK_LABEL_PATTERN.fullmatch(label):
        return False

    parsed = urlparse(link.url)
    host = parsed.hostname.lower() if parsed.hostname else ""
    path = parsed.path.rstrip("/")
    if host in {"itunes.apple.com", "play.google.com"}:
        return False
    if host.startswith(("help.", "policy.")) or _FOOTER_LINK_PATH_PATTERN.search(path):
        return False

    segments = [segment for segment in path.split("/") if segment]
    if any(segment.startswith("@") for segment in segments):
        return False
    if re.search(r"(?i)/(?:author|profile|user)/", f"/{path.lstrip('/')}/"):
        return False
    if (host == "medium.com" or host.endswith(".medium.com")) and len(segments) <= 1:
        return False
    return True


def _anchor_label(value: str) -> str:
    visible = re.sub(r"(?s)<[^>]+>", " ", value)
    visible = re.sub(r"\s+", " ", html.unescape(visible)).strip()
    if visible:
        return visible
    alt_values = re.findall(r"(?is)<img\b[^>]*?\balt\s*=\s*(['\"])(.*?)\1", value)
    return re.sub(r"\s+", " ", " ".join(html.unescape(alt) for _, alt in alt_values)).strip()


def _html_to_text(value: str, links: _LinkCollector | None = None) -> str:
    collector = links or _LinkCollector()
    without_tags = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    without_tags = re.sub(r"(?is)<!--.*?-->", "\n", without_tags)
    without_tags = re.sub(
        r"(?is)<a\b[^>]*?href\s*=\s*(['\"])(.*?)\1[^>]*>(.*?)</a>",
        lambda match: _anchor_to_text(match, collector),
        without_tags,
    )
    without_tags = re.sub(
        r"(?is)<\s*/?\s*(?:address|article|aside|blockquote|br|div|dl|dt|dd|"
        r"fieldset|figcaption|figure|footer|form|h[1-6]|header|hr|li|main|nav|"
        r"ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        without_tags,
    )
    without_tags = re.sub(r"(?s)<[^>]+>", " ", without_tags)
    unescaped = html.unescape(without_tags)
    unescaped = _replace_markdown_links(unescaped, collector)
    unescaped = _replace_urls(unescaped, collector)
    unescaped = _normalize_canonical_ref_wrappers(unescaped)
    lines = (re.sub(r"[^\S\r\n]+", " ", line).strip() for line in unescaped.splitlines())
    return "\n".join(line for line in lines if line)


def _strip_suspicious_format_controls(value: str) -> str:
    """Drop rendering artifacts while preserving emoji ZWJ sequences."""

    characters = list(value)
    cleaned: list[str] = []
    for index, character in enumerate(characters):
        if character in _ALWAYS_REMOVE_FORMAT_CONTROLS:
            continue
        if character not in {"\u200c", "\u200d"}:
            cleaned.append(character)
            continue
        left = _nearest_non_variation_selector(characters, index, -1)
        right = _nearest_non_variation_selector(characters, index, 1)
        if left is None or right is None:
            continue
        if character == "\u200c" and _is_joining_script(left) and _is_joining_script(right):
            cleaned.append(character)
        elif character == "\u200d" and (
            (_is_emoji(left) and _is_emoji(right))
            or (_is_joining_script(left) and _is_joining_script(right))
        ):
            cleaned.append(character)
    return "".join(cleaned)


def _remove_separator_lines(value: str) -> str:
    return "\n".join(
        line for line in value.splitlines() if not _SEPARATOR_LINE_PATTERN.fullmatch(line)
    )


def _nearest_non_variation_selector(
    characters: Sequence[str], start: int, direction: int
) -> str | None:
    index = start + direction
    while 0 <= index < len(characters):
        character = characters[index]
        if character not in {"\ufe0e", "\ufe0f"}:
            return character
        index += direction
    return None


def _is_emoji(character: str) -> bool:
    codepoint = ord(character)
    return 0x1F000 <= codepoint <= 0x1FAFF or 0x2600 <= codepoint <= 0x27BF


def _is_joining_script(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x0600 <= codepoint <= 0x08FF
        or 0x0900 <= codepoint <= 0x0D7F
        or 0xFB50 <= codepoint <= 0xFEFF
    )


def _anchor_to_text(match: re.Match[str], links: _LinkCollector) -> str:
    url = html.unescape(match.group(2)).strip()
    label = _anchor_label(match.group(3))
    if not url.startswith(("https://", "http://")):
        return label or url
    source_label = _source_link_label(label, url)
    ref = links.add(url, source_label)
    return f" {source_label} [{ref}] " if source_label else " "


def _extract_html_links(value: str, links: _LinkCollector) -> list[tuple[str, str]]:
    anchor_pattern = r"(?is)<a\b[^>]*?href\s*=\s*(['\"])(.*?)\1[^>]*>(.*?)</a>"
    extracted: list[tuple[str, str]] = []
    for match in re.finditer(anchor_pattern, value):
        url = html.unescape(match.group(2)).strip()
        if url.startswith(("https://", "http://")):
            extracted.append((url, _anchor_to_text(match, links).strip()))
    return extracted


def _optional_string(value: object) -> str | None:
    return str(value) if value else None
