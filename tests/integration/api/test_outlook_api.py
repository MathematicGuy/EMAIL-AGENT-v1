"""Outlook HTTP contracts layered on the existing mailbox API.

These tests stay at the ASGI boundary and replace the Microsoft service seam;
OAuth cryptography and Graph response mapping belong to the Outlook unit tests.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI

from cowork_agent.app import create_app
from cowork_agent.config import (
    GMAIL_READONLY_SCOPE,
    MICROSOFT_DEFAULT_SCOPES,
    MICROSOFT_MAIL_READ_SCOPE,
)
from cowork_agent.domain import MailboxConnection
from cowork_agent.domain.target_contracts import (
    BodyFormat,
    EphemeralEmailEnvelope,
    FetchStatus,
)
from cowork_agent.features.email_action_plan.schemas import MessageRef, SearchPage

OWNER_ID = "mbx-gmail-owner"
OWNER_EMAIL = "owner@example.com"
OUTLOOK_ID = "mbx-outlook-owner"


@pytest.fixture(autouse=True)
def outlook_api_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("POSTGRES_MODE", "off")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("GMAIL_CLIENT_ID", "outlook-api.apps.googleusercontent.com")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "gmail-test-secret")
    monkeypatch.setenv(
        "GMAIL_REDIRECT_URI", "http://localhost:8000/v1/mail-todo/oauth/gmail/callback"
    )
    monkeypatch.setenv("GMAIL_SCOPES", GMAIL_READONLY_SCOPE)
    monkeypatch.setenv("GMAIL_CONNECTION_DB_PATH", str(tmp_path / "connections.db"))
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("OAUTH_STATE_SECRET", "outlook-api-state-secret-at-least-32-characters")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("FRONTEND_URL", "")
    for name in (
        "MICROSOFT_CLIENT_ID",
        "MICROSOFT_CLIENT_SECRET",
        "MICROSOFT_TENANT",
        "MICROSOFT_REDIRECT_URI",
        "MICROSOFT_SCOPES",
    ):
        # ``load_runtime_environment`` loads .env without overriding an
        # existing value, so an empty value is the deterministic way to make
        # optional Outlook configuration unavailable for this test.
        monkeypatch.setenv(name, "")


def _configure_outlook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", "microsoft-client")
    monkeypatch.setenv("MICROSOFT_CLIENT_SECRET", "microsoft-secret")
    monkeypatch.setenv("MICROSOFT_TENANT", "common")
    monkeypatch.setenv("MICROSOFT_SCOPES", " ".join(MICROSOFT_DEFAULT_SCOPES))
    monkeypatch.setenv(
        "MICROSOFT_REDIRECT_URI",
        "http://localhost:8000/v1/mail-todo/oauth/outlook/callback",
    )


def _connection(
    connection_id: str,
    *,
    provider: str,
    email: str = OWNER_EMAIL,
    user_id: str = OWNER_EMAIL,
    status: str = "active",
) -> MailboxConnection:
    now = datetime.now(UTC)
    return MailboxConnection(
        id=connection_id,
        user_id=user_id,
        provider=provider,
        external_account_id=email.lower(),
        email_address=email,
        encrypted_refresh_token="encrypted-test-token",
        scopes=(GMAIL_READONLY_SCOPE,) if provider == "gmail" else (MICROSOFT_MAIL_READ_SCOPE,),
        status=status,
        created_at=now,
        updated_at=now,
    )


@asynccontextmanager
async def _running_app() -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    app = create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://outlook-api.test"
        ) as client:
            yield app, client


def test_connections_report_outlook_not_configured_safely() -> None:
    async def scenario() -> None:
        async with _running_app() as (_, client):
            response = await client.get("/v1/mail-todo/connections")
            connect = await client.get(
                "/v1/mail-todo/oauth/outlook/connect",
                params={"ownerConnectionId": OWNER_ID},
                follow_redirects=False,
            )

        assert response.status_code == 200
        assert response.json() == {
            "connections": [],
            "providerAvailability": {
                "gmail": {"enabled": True, "reason": None},
                "outlook": {"enabled": False, "reason": "not_configured"},
            },
        }
        assert connect.status_code == 503
        assert "microsoft-secret" not in connect.text

    asyncio.run(scenario())


def test_sqlite_only_unavailability_is_reported_before_owner_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the HTTP behavior used by Postgres composition without opening a DB."""
    _configure_outlook(monkeypatch)

    async def scenario() -> None:
        async with _running_app() as (app, client):
            # PostgreSQL composition exposes this same disabled capability state.
            # Override only that state here so this API test stays offline.
            app.state.outlook_connections = None
            app.state.provider_availability["outlook"] = {
                "enabled": False,
                "reason": "sqlite_only",
            }
            connections = await client.get("/v1/mail-todo/connections")
            connect = await client.get(
                "/v1/mail-todo/oauth/outlook/connect",
                params={"ownerConnectionId": "does-not-exist"},
                follow_redirects=False,
            )

        assert connections.json()["providerAvailability"]["outlook"] == {
            "enabled": False,
            "reason": "sqlite_only",
        }
        assert connect.status_code == 503
        assert connect.json()["detail"].endswith("sqlite_only")

    asyncio.run(scenario())


def test_outlook_connect_requires_an_active_gmail_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_outlook(monkeypatch)

    async def scenario() -> None:
        async with _running_app() as (app, client):
            missing = await client.get(
                "/v1/mail-todo/oauth/outlook/connect",
                params={"ownerConnectionId": "missing"},
                follow_redirects=False,
            )
            await app.state.connection_repository.upsert(
                _connection(OUTLOOK_ID, provider="outlook")
            )
            wrong_provider = await client.get(
                "/v1/mail-todo/oauth/outlook/connect",
                params={"ownerConnectionId": OUTLOOK_ID},
                follow_redirects=False,
            )
            await app.state.connection_repository.upsert(
                _connection(OWNER_ID, provider="gmail", status="disconnected")
            )
            inactive = await client.get(
                "/v1/mail-todo/oauth/outlook/connect",
                params={"ownerConnectionId": OWNER_ID},
                follow_redirects=False,
            )

        assert missing.status_code == 404
        assert wrong_provider.status_code == 400
        assert inactive.status_code == 400
        assert "Gmail" in missing.json()["detail"]
        assert all(
            "active Gmail" in response.json()["detail"]
            for response in (wrong_provider, inactive)
        )

    asyncio.run(scenario())


def test_outlook_connect_uses_the_selected_gmail_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_outlook(monkeypatch)

    class RecordingOutlookConnections:
        owner_ids: list[str]

        def __init__(self) -> None:
            self.owner_ids = []

        def begin(self, owner: MailboxConnection) -> str:
            self.owner_ids.append(owner.id)
            return "https://login.microsoftonline.test/common/oauth2/v2.0/authorize?safe=1"

    async def scenario() -> None:
        async with _running_app() as (app, client):
            await app.state.connection_repository.upsert(_connection(OWNER_ID, provider="gmail"))
            service = RecordingOutlookConnections()
            app.state.outlook_connections = service
            response = await client.get(
                "/v1/mail-todo/oauth/outlook/connect",
                params={"ownerConnectionId": OWNER_ID},
                follow_redirects=False,
            )

        assert response.status_code == 302
        assert response.headers["location"].startswith("https://login.microsoftonline.test/")
        assert service.owner_ids == [OWNER_ID]

    asyncio.run(scenario())


def test_outlook_callback_links_under_owner_and_redirects_without_replacing_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_outlook(monkeypatch)
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:5173")

    class CompletingOutlookConnections:
        def __init__(self, repository: object) -> None:
            self._repository = repository

        async def complete(self, state: str, code: str) -> MailboxConnection:
            assert state == "signed-owner-state"
            assert code == "test-code"
            connection = _connection(
                OUTLOOK_ID,
                provider="outlook",
                email="owner@outlook.com",
                user_id=OWNER_EMAIL,
            )
            return await self._repository.upsert(connection)  # type: ignore[attr-defined,no-any-return]

    async def scenario() -> None:
        async with _running_app() as (app, client):
            await app.state.connection_repository.upsert(_connection(OWNER_ID, provider="gmail"))
            app.state.outlook_connections = CompletingOutlookConnections(
                app.state.connection_repository
            )
            response = await client.get(
                "/v1/mail-todo/oauth/outlook/callback",
                params={"state": "signed-owner-state", "code": "test-code"},
                follow_redirects=False,
            )
            denied = await client.get(
                "/v1/mail-todo/oauth/outlook/callback",
                params={"state": "safe", "error": "access_denied"},
                follow_redirects=False,
            )
            missing_code = await client.get(
                "/v1/mail-todo/oauth/outlook/callback",
                params={"state": "safe"},
                follow_redirects=False,
            )
            stored = await app.state.connection_repository.get(OUTLOOK_ID)

        assert response.status_code == 302
        assert response.headers["location"] == (
            "http://localhost:5173/?page=dashboard&view=mail&outlook=connected#dashboard"
        )
        assert "outlook=denied" in denied.headers["location"]
        assert "outlook=error" in missing_code.headers["location"]
        assert "set-cookie" not in response.headers
        assert stored is not None
        assert stored.provider == "outlook"
        assert stored.user_id == OWNER_EMAIL

    asyncio.run(scenario())


def test_outlook_preview_and_disconnect_use_generic_mailbox_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_outlook(monkeypatch)
    received_at = datetime(2026, 8, 21, 3, 0, tzinfo=UTC)
    envelope = EphemeralEmailEnvelope(
        run_id="preview",
        user_id=OWNER_EMAIL,
        gmail_message_id="outlook:message-1",
        gmail_thread_id="outlook:conversation-1",
        gmail_url="https://outlook.office.com/mail/deeplink/read/message-1",
        sender_name="Outlook Sender",
        sender_email="sender@example.com",
        recipients=(OWNER_EMAIL,),
        subject="Outlook preview",
        received_at=received_at,
        labels=("UNREAD", "INBOX"),
        normalized_body="must stay out of the API response",
        body_format=BodyFormat.TEXT,
        attachments_present=True,
        fetch_status=FetchStatus.COMPLETE,
    )

    class RecordingMailboxRouter:
        calls: list[tuple[object, ...]]

        def __init__(self) -> None:
            self.calls = []

        async def search_unread(
            self,
            connection_id: str,
            query: str,
            page_size: int,
            cursor: str | None = None,
        ) -> SearchPage:
            self.calls.append(("search", connection_id, query, page_size, cursor))
            return SearchPage(
                (MessageRef(envelope.gmail_message_id, envelope.gmail_thread_id),),
                None,
                1,
            )

        async def get_thread(
            self, connection_id: str, thread_id: str
        ) -> tuple[EphemeralEmailEnvelope, ...]:
            self.calls.append(("thread", connection_id, thread_id))
            return (envelope,)

    async def scenario() -> None:
        async with _running_app() as (app, client):
            await app.state.connection_repository.upsert(
                _connection(
                    OUTLOOK_ID,
                    provider="outlook",
                    email="owner@outlook.com",
                )
            )
            router = RecordingMailboxRouter()
            app.state.mailbox = router
            preview = await client.get(
                f"/v1/mail-todo/connections/{OUTLOOK_ID}/unread-preview"
            )
            disconnected = await client.delete(f"/v1/mail-todo/connections/{OUTLOOK_ID}")
            stored = await app.state.connection_repository.get(OUTLOOK_ID)

        assert preview.status_code == 200
        assert preview.json() == {
            "emailsMatched": 1,
            "messages": [
                {
                    "messageId": "outlook:message-1",
                    "threadId": "outlook:conversation-1",
                    "subject": "Outlook preview",
                    "sender": "sender@example.com",
                    "receivedAt": received_at.isoformat(),
                    "attachmentsPresent": True,
                    "deepLink": "https://outlook.office.com/mail/deeplink/read/message-1",
                }
            ],
            "nextCursor": None,
        }
        assert "must stay out" not in preview.text
        assert [call[0] for call in router.calls] == ["search", "thread"]
        assert disconnected.status_code == 200
        assert disconnected.json() == {"disconnected": True}
        assert stored is None

    asyncio.run(scenario())
