import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
from cryptography.fernet import Fernet

from cowork_agent.app import create_app
from cowork_agent.config import GMAIL_READONLY_SCOPE
from cowork_agent.domain import MailboxConnection
from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatMessageRequest,
    ChatTurnStatus,
)
from cowork_agent.persistence.repositories.local import InMemoryChatHistoryRepository


def test_server_starts_and_redirects_to_google_oauth(tmp_path: Path, monkeypatch: object) -> None:
    values = {
        "DATABASE_URL": "",
        "GMAIL_CLIENT_ID": "test.apps.googleusercontent.com",
        "GMAIL_CLIENT_SECRET": "test-secret",
        "GMAIL_REDIRECT_URI": "http://localhost:8000/v1/mail-todo/oauth/gmail/callback",
        "GMAIL_SCOPES": GMAIL_READONLY_SCOPE,
        "GMAIL_CONNECTION_DB_PATH": str(tmp_path / "connections.db"),
        "TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "OAUTH_STATE_SECRET": "state-secret-that-is-at-least-32-characters",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)  # type: ignore[attr-defined]

    async def scenario() -> None:
        app = create_app()
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                assert (await client.get("/health")).json() == {"status": "ok"}
                response = await client.get(
                    "/v1/mail-todo/oauth/gmail/connect",
                    follow_redirects=False,
                )
                assert response.status_code == 302
                assert response.headers["location"].startswith("https://accounts.google.com/")
                assert "gmail.readonly" in response.headers["location"]
                connections = await client.get("/v1/mail-todo/connections")
                assert connections.json() == {"connections": []}

    asyncio.run(scenario())


def test_local_fallback_wires_chat_history_into_scoped_controllers(
    tmp_path: Path, monkeypatch: object
) -> None:
    values = {
        "DATABASE_URL": "",
        "GMAIL_CLIENT_ID": "test.apps.googleusercontent.com",
        "GMAIL_CLIENT_SECRET": "test-secret",
        "GMAIL_REDIRECT_URI": "http://localhost:8000/v1/mail-todo/oauth/gmail/callback",
        "GMAIL_SCOPES": GMAIL_READONLY_SCOPE,
        "GMAIL_CONNECTION_DB_PATH": str(tmp_path / "connections.db"),
        "TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "OAUTH_STATE_SECRET": "state-secret-that-is-at-least-32-characters",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)  # type: ignore[attr-defined]

    async def scenario() -> None:
        app = create_app()
        async with app.router.lifespan_context(app):
            assert isinstance(
                app.state.chat_history_repository, InMemoryChatHistoryRepository
            )
            scope = ChatMemoryScope(
                tenant_id="local", user_id="owner", session_id="session-1"
            )

            class AssertPendingBeforeReply:
                observed_pending = False

                async def stream_reply(self, _request: object, _context: object):
                    turns = await app.state.chat_history_repository.list_turns(scope)
                    assert len(turns) == 1
                    assert turns[0].status is ChatTurnStatus.GENERATING
                    assert turns[0].assistant_message is None
                    self.observed_pending = True
                    yield "Reply"

            reply = AssertPendingBeforeReply()
            app.state.chat_reply = reply
            controller = app.state.chat_controller_factory(scope)
            assert controller._history is app.state.chat_history_repository
            events = [
                event
                async for event in controller.stream_message(
                    ChatMessageRequest(
                        session_id=scope.session_id,
                        user_message="Persist me first",
                        idempotency_key="submission-1",
                    )
                )
            ]
            stored = await app.state.chat_history_repository.list_turns(scope)
            assert reply.observed_pending is True
            assert events[-1].event_type.value == "completed"
            assert stored[0].status is ChatTurnStatus.COMPLETED
            assert stored[0].assistant_message == "Reply"

    asyncio.run(scenario())


def test_server_starts_with_faucet_provider(tmp_path: Path, monkeypatch: object) -> None:
    values = {
        "DATABASE_URL": "",
        "LLM_PROVIDER": "faucet",
        "FAUCET_API_KEY": "test-key",
        "FAUCET_MODEL": "test-model",
        "GMAIL_CLIENT_ID": "test.apps.googleusercontent.com",
        "GMAIL_CLIENT_SECRET": "test-secret",
        "GMAIL_REDIRECT_URI": "http://localhost:8000/v1/mail-todo/oauth/gmail/callback",
        "GMAIL_SCOPES": GMAIL_READONLY_SCOPE,
        "GMAIL_CONNECTION_DB_PATH": str(tmp_path / "connections.db"),
        "TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "OAUTH_STATE_SECRET": "state-secret-that-is-at-least-32-characters",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)  # type: ignore[attr-defined]

    async def scenario() -> None:
        app = create_app()
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                assert (await client.get("/health")).json() == {"status": "ok"}

    asyncio.run(scenario())


def test_invalid_faucet_configuration_returns_provider_accurate_safe_503(
    tmp_path: Path, monkeypatch: object
) -> None:
    values = {
        "DATABASE_URL": "",
        "LLM_PROVIDER": "faucet",
        "FAUCET_API_KEY": "must-not-appear-in-error",
        "FAUCET_MODEL": "replace-with-faucet-model",
        "GMAIL_CLIENT_ID": "test.apps.googleusercontent.com",
        "GMAIL_CLIENT_SECRET": "test-secret",
        "GMAIL_REDIRECT_URI": "http://localhost:8000/v1/mail-todo/oauth/gmail/callback",
        "GMAIL_SCOPES": GMAIL_READONLY_SCOPE,
        "GMAIL_CONNECTION_DB_PATH": str(tmp_path / "connections.db"),
        "TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "OAUTH_STATE_SECRET": "state-secret-that-is-at-least-32-characters",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)  # type: ignore[attr-defined]

    async def scenario() -> None:
        app = create_app()
        async with app.router.lifespan_context(app):
            now = datetime.now(UTC)
            await app.state.connection_repository.upsert(
                MailboxConnection(
                    id="mbx-faucet-invalid",
                    user_id="owner@example.com",
                    provider="gmail",
                    external_account_id="owner@example.com",
                    email_address="owner@example.com",
                    encrypted_refresh_token="not-used",
                    scopes=(GMAIL_READONLY_SCOPE,),
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
            )
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/v1/mail-todo/runs",
                    json={"mailboxConnectionId": "mbx-faucet-invalid"},
                    headers={"Idempotency-Key": "faucet-invalid"},
                )
                assert response.status_code == 503
                assert response.json()["detail"] == (
                    "Faucet is not configured: FAUCET_MODEL must be a real Faucet model name"
                )
                assert "must-not-appear-in-error" not in response.text

    asyncio.run(scenario())
