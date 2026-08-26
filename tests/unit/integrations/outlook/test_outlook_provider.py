"""Unit tests for the read-only Microsoft Graph provider."""

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet

import cowork_agent.integrations.outlook.provider as outlook_provider
from cowork_agent.config import OutlookSettings as ConfigOutlookSettings
from cowork_agent.domain import MailboxConnection
from cowork_agent.features.email_action_plan.schemas import SearchPage
from cowork_agent.integrations.gmail.auth import OAuthStateManager, TokenCipher
from cowork_agent.integrations.mailbox import (
    MailboxNotConnectedError,
    MailboxPermissionDeniedError,
    MailboxRateLimitedError,
    MailboxTemporaryError,
)
from cowork_agent.integrations.outlook import (
    MICROSOFT_DEFAULT_SCOPES,
    MicrosoftOAuthDriver,
    OutlookConnectionService,
    OutlookMailboxAdapter,
    OutlookOAuthGrant,
)


@dataclass(frozen=True)
class Settings:
    client_id: str = "client-id"
    client_secret: str = "client-secret"
    tenant: str = "common"
    redirect_uri: str = "http://localhost:8000/v1/mail-todo/oauth/outlook/callback"
    scopes: tuple[str, ...] = MICROSOFT_DEFAULT_SCOPES
    graph_base_url: str = "https://graph.microsoft.com/v1.0"


class Repository:
    def __init__(self, *connections: MailboxConnection) -> None:
        self.items = {item.id: item for item in connections}

    async def upsert(self, connection: MailboxConnection) -> MailboxConnection:
        for item in self.items.values():
            if (
                item.user_id,
                item.provider,
                item.external_account_id,
            ) == (
                connection.user_id,
                connection.provider,
                connection.external_account_id,
            ):
                connection = MailboxConnection(
                    item.id,
                    connection.user_id,
                    connection.provider,
                    connection.external_account_id,
                    connection.email_address,
                    connection.encrypted_refresh_token,
                    connection.scopes,
                    connection.status,
                    item.created_at,
                    connection.updated_at,
                )
                break
        self.items[connection.id] = connection
        return connection

    async def get(self, connection_id: str) -> MailboxConnection | None:
        return self.items.get(connection_id)

    async def list_for_user(self, user_id: str) -> tuple[MailboxConnection, ...]:
        return tuple(item for item in self.items.values() if item.user_id == user_id)

    async def delete(self, connection_id: str, user_id: str) -> bool:
        item = self.items.get(connection_id)
        if item is None or item.user_id != user_id:
            return False
        del self.items[connection_id]
        return True


def mailbox(
    cipher: TokenCipher, *, provider: str = "outlook", status: str = "active"
) -> MailboxConnection:
    now = datetime.now(UTC)
    return MailboxConnection(
        f"mbx_{provider}",
        "user-1",
        provider,
        "account-id",
        f"user@{provider}.com",
        cipher.encrypt("refresh-old"),
        MICROSOFT_DEFAULT_SCOPES if provider == "outlook" else ("gmail.readonly",),
        status,
        now,
        now,
    )


def cipher() -> TokenCipher:
    return TokenCipher(Fernet.generate_key().decode())


def outlook_environment() -> dict[str, str]:
    return {
        "MICROSOFT_CLIENT_ID": "client-id",
        "MICROSOFT_CLIENT_SECRET": "client-secret",
        "TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "OAUTH_STATE_SECRET": "a-state-secret-that-is-at-least-32-characters",
    }


def id_token() -> str:
    claims = base64.urlsafe_b64encode(
        json.dumps({"oid": "outlook-account", "preferred_username": "user@outlook.com"}).encode()
    ).rstrip(b"=").decode()
    return f"header.{claims}.signature"


def test_authorization_url_is_pkce_and_read_only() -> None:
    url = MicrosoftOAuthDriver(Settings()).authorization_url("state", "verifier")
    params = parse_qs(urlparse(url).query)
    assert params["code_challenge_method"] == ["S256"]
    assert "Mail.Read" in params["scope"][0]
    assert "Mail.ReadWrite" not in params["scope"][0]


def test_config_defaults_to_common_and_the_exact_read_only_scope_set() -> None:
    settings = ConfigOutlookSettings.from_env(
        outlook_environment()
    )
    assert settings.tenant == "common"
    assert settings.scopes == MICROSOFT_DEFAULT_SCOPES


def test_config_rejects_mail_write_scope() -> None:
    values = outlook_environment()
    values["MICROSOFT_SCOPES"] = " ".join((*MICROSOFT_DEFAULT_SCOPES, "Mail.ReadWrite"))
    with pytest.raises(ValueError, match="only Mail.Read"):
        ConfigOutlookSettings.from_env(values)


def test_driver_rejects_any_scope_change() -> None:
    with pytest.raises(ValueError, match="only Mail.Read"):
        MicrosoftOAuthDriver(Settings(scopes=(*MICROSOFT_DEFAULT_SCOPES, "Mail.Send")))


def test_rate_limit_is_a_publicly_safe_temporary_mailbox_error() -> None:
    error = MailboxRateLimitedError("provider payload must stay private")
    assert isinstance(error, MailboxTemporaryError)
    assert error.safe_message == "The email service is temporarily rate limiting requests."


@pytest.mark.asyncio
async def test_connection_is_bound_to_active_gmail_owner() -> None:
    class Driver:
        def authorization_url(self, state: str, code_verifier: str) -> str:
            return f"https://login.example/authorize?state={state}&v={code_verifier}"

        async def exchange(self, code: str, code_verifier: str) -> OutlookOAuthGrant:
            assert code == "code"
            assert code_verifier
            return OutlookOAuthGrant(
                "outlook-account", "user@outlook.com", "refresh-new", ("Mail.Read",)
            )

    token_cipher = cipher()
    owner = mailbox(token_cipher, provider="gmail")
    repository = Repository(owner)
    service = OutlookConnectionService(
        Settings(),
        repository,  # type: ignore[arg-type]
        token_cipher,
        OAuthStateManager("a-state-secret-that-is-at-least-32-characters", 600),
        Driver(),
    )
    url = service.begin(owner)
    state = parse_qs(urlparse(url).query)["state"][0]
    created = await service.complete(state, "code")
    assert created.user_id == owner.user_id
    assert created.provider == "outlook"
    assert token_cipher.decrypt(created.encrypted_refresh_token) == "refresh-new"

    with pytest.raises(MailboxNotConnectedError):
        service.begin(mailbox(token_cipher, provider="outlook"))


@pytest.mark.asyncio
async def test_graph_accepts_normalized_unread_query_and_maps_envelope() -> None:
    token_cipher = cipher()
    connection = mailbox(token_cipher)
    repository = Repository(connection)
    token_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.host == "login.microsoftonline.com":
            token_calls += 1
            return httpx.Response(
                200,
                json={"access_token": "access", "refresh_token": "refresh-new", "expires_in": 3600},
            )
        if request.url.path.endswith("/mailFolders/inbox/messages"):
            return httpx.Response(
                200,
                json={
                    "@odata.count": 1,
                    "value": [{"id": "message-1", "conversationId": "thread-1"}],
                    "@odata.nextLink": (
                        "https://graph.microsoft.com/v1.0/me/mailFolders('Inbox')/"
                        "messages?$skiptoken=next"
                    ),
                },
            )
        if request.url.path.endswith("/me/messages"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "message-1",
                            "conversationId": "thread-1",
                            "subject": "Review",
                            "from": {"emailAddress": {"name": "Alex", "address": "a@example.com"}},
                            "toRecipients": [{"emailAddress": {"address": "user@example.com"}}],
                            "receivedDateTime": "2026-08-04T01:01:00Z",
                            "body": {
                                "contentType": "html",
                                "content": '<p>Review <a href="https://example.com/x">here</a></p>',
                            },
                            "hasAttachments": True,
                            "webLink": "https://outlook.office.com/mail/deeplink/read/1",
                        }
                    ]
                },
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = OutlookMailboxAdapter(
            Settings(), repository, token_cipher, client  # type: ignore[arg-type]
        )
        page = await adapter.search_unread(
            connection.id, "is:unread in:inbox category:primary", 10
        )
        assert page.messages[0].message_id == "outlook:message-1"
        assert page.messages[0].thread_id == "outlook:thread-1"
        thread = await adapter.get_thread(connection.id, page.messages[0].thread_id)
        assert thread[0].gmail_message_id == "outlook:message-1"
        assert thread[0].gmail_thread_id == "outlook:thread-1"
        assert thread[0].recipients == ("user@example.com",)
        assert thread[0].attachments_present is True
        assert thread[0].attachments_processed is False
        assert thread[0].source_links[0].url == "https://example.com/x"
        await adapter.search_unread(
            connection.id, "is:unread in:inbox category:primary", 10
        )
    assert token_calls == 1
    stored = await repository.get(connection.id)
    assert stored is not None
    assert token_cipher.decrypt(stored.encrypted_refresh_token) == "refresh-new"


@pytest.mark.asyncio
async def test_graph_rejects_filters_other_than_unread_inbox() -> None:
    token_cipher = cipher()
    connection = mailbox(token_cipher)
    adapter = OutlookMailboxAdapter(
        Settings(), Repository(connection), token_cipher  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="only the unread inbox"):
        await adapter.search_unread(connection.id, "is:read in:inbox", 10)


@pytest.mark.asyncio
async def test_graph_paginates_complete_conversation_and_sorts_chronologically() -> None:
    token_cipher = cipher()
    connection = mailbox(token_cipher)

    def message(message_id: str, received_at: str) -> dict[str, object]:
        return {
            "id": message_id,
            "conversationId": "thread-1",
            "subject": "Thread",
            "from": {"emailAddress": {"address": "sender@example.com"}},
            "receivedDateTime": received_at,
            "body": {"contentType": "text", "content": message_id},
            "hasAttachments": False,
        }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "login.microsoftonline.com":
            return httpx.Response(200, json={"access_token": "access", "expires_in": 3600})
        if "$skiptoken" not in request.url.params:
            return httpx.Response(
                200,
                json={
                    "value": [message("newer", "2026-08-04T02:00:00Z")],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/messages?$skiptoken=2",
                },
            )
        return httpx.Response(
            200, json={"value": [message("older", "2026-08-04T01:00:00Z")]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = OutlookMailboxAdapter(
            Settings(), Repository(connection), token_cipher, client  # type: ignore[arg-type]
        )
        thread = await adapter.get_thread(connection.id, "outlook:thread-1")
    assert [item.gmail_message_id for item in thread] == ["outlook:older", "outlook:newer"]


@pytest.mark.asyncio
async def test_graph_rejects_cross_origin_and_wrong_path_cursors() -> None:
    token_cipher = cipher()
    connection = mailbox(token_cipher)
    adapter = OutlookMailboxAdapter(
        Settings(), Repository(connection), token_cipher  # type: ignore[arg-type]
    )
    for cursor in (
        "https://attacker.example/v1.0/me/mailFolders/inbox/messages?$skip=1",
        "https://graph.microsoft.com/v1.0/me/messages?$skip=1",
        "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages-evil?$skip=1",
        "https://graph.microsoft.com/v1.0/me/mailFolders('arbitrary-id')/messages?$skip=1",
    ):
        with pytest.raises(ValueError, match="paging cursor"):
            await adapter.search_unread(connection.id, "unread_inbox", 10, cursor)


@pytest.mark.asyncio
async def test_graph_permission_failure_is_safe() -> None:
    token_cipher = cipher()
    connection = mailbox(token_cipher)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "login.microsoftonline.com":
            return httpx.Response(200, json={"access_token": "access", "expires_in": 3600})
        return httpx.Response(403, json={"secret": "must-not-leak"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = OutlookMailboxAdapter(
            Settings(), Repository(connection), token_cipher, client  # type: ignore[arg-type]
        )
        with pytest.raises(MailboxPermissionDeniedError) as raised:
            await adapter.search_unread(connection.id, "unread_inbox", 10)
    assert "secret" not in str(raised.value)


@pytest.mark.asyncio
async def test_graph_transient_failures_use_a_bounded_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_cipher = cipher()
    connection = mailbox(token_cipher)
    graph_calls = 0
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(outlook_provider.asyncio, "sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal graph_calls
        if request.url.host == "login.microsoftonline.com":
            return httpx.Response(200, json={"access_token": "access", "expires_in": 3600})
        graph_calls += 1
        if graph_calls < 3:
            return httpx.Response(503, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"value": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = OutlookMailboxAdapter(
            Settings(), Repository(connection), token_cipher, client  # type: ignore[arg-type]
        )
        assert await adapter.search_unread(connection.id, "unread_inbox", 10) == SearchPage(())
    assert graph_calls == 3
    assert sleeps == [0.0, 0.0]
