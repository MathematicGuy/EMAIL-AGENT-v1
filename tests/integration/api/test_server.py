import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

from cowork_agent.app import create_app
from cowork_agent.config import GMAIL_READONLY_SCOPE
from cowork_agent.domain import MailboxConnection
from cowork_agent.domain.chat_contracts import ChatMessageRequest, ChatTurnStatus
from cowork_agent.integrations.report_pdf import Fpdf2ReportPdfRenderer
from cowork_agent.persistence.repositories.sqlite_chat import SQLiteChatRepository


@pytest.fixture(autouse=True)
def disable_optional_outlook(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep Gmail-only API tests independent of a developer's local .env."""
    for name in (
        "MICROSOFT_CLIENT_ID",
        "MICROSOFT_CLIENT_SECRET",
        "MICROSOFT_TENANT",
        "MICROSOFT_REDIRECT_URI",
        "MICROSOFT_SCOPES",
    ):
        monkeypatch.setenv(name, "")


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
            assert isinstance(
                app.state.runtime.report_pdf_renderer,
                Fpdf2ReportPdfRenderer,
            )
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
                assert connections.json() == {
                    "connections": [],
                    "providerAvailability": {
                        "gmail": {"enabled": True, "reason": None},
                        "outlook": {"enabled": False, "reason": "not_configured"},
                    },
                }

    asyncio.run(scenario())


def test_sqlite_fallback_wires_chat_history_into_scoped_controllers(
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
            history_repository = app.state.runtime.control_plane.chat_history_repository
            assert isinstance(history_repository, SQLiteChatRepository)
            scope = await app.state.runtime.chat.chat_sessions.create(
                tenant_id="local", user_id="owner"
            )

            class AssertPendingBeforeReply:
                observed_pending = False

                async def stream_reply(self, _request: object, _context: object):
                    turns = await history_repository.list_turns(scope)
                    assert len(turns) == 1
                    assert turns[0].status is ChatTurnStatus.GENERATING
                    assert turns[0].assistant_message is None
                    self.observed_pending = True
                    yield "Reply"

            reply = AssertPendingBeforeReply()
            # The controller factory reads the composed runtime at creation
            # time (ADR-013), so the test reply must land on the chat group.
            app.state.runtime = replace(
                app.state.runtime,
                chat=replace(app.state.runtime.chat, chat_reply=reply),
            )
            controller = app.state.chat_controller_factory(scope)
            assert controller._history is history_repository
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
            stored = await history_repository.list_turns(scope)
            assert reply.observed_pending is True
            assert events[-1].event_type.value == "completed"
            assert stored[0].status is ChatTurnStatus.COMPLETED
            assert stored[0].assistant_message == "Reply"

    asyncio.run(scenario())


def test_sqlite_fallback_bootstraps_a_guest_chat_session(
    tmp_path: Path, monkeypatch: object
) -> None:
    values = {
        "POSTGRES_MODE": "off",
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
                guest = await client.post("/v1/cowork/chat/guest-session")
                session = await client.post("/v1/cowork/chat/sessions")

        assert guest.status_code == 204
        assert "cowork_session=" in guest.headers["set-cookie"]
        assert session.status_code == 201
        assert session.json()["feature"] == "ai_chat"

    asyncio.run(scenario())


def test_server_starts_with_mistral_provider(tmp_path: Path, monkeypatch: object) -> None:
    values = {
        "DATABASE_URL": "",
        "LLM_PROVIDER": "mistral",
        "MISTRAL_API_KEY": "test-key",
        "MISTRAL_MODEL": "mistral-small-2603",
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


def test_invalid_mistral_configuration_returns_provider_accurate_safe_503(
    tmp_path: Path, monkeypatch: object
) -> None:
    values = {
        "DATABASE_URL": "",
        "LLM_PROVIDER": "mistral",
        "MISTRAL_API_KEY": "must-not-appear-in-error",
        "MISTRAL_MODEL": "replace-with-mistral-model",
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
            await app.state.runtime.control_plane.connection_repository.upsert(
                MailboxConnection(
                    id="mbx-mistral-invalid",
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
                    json={"mailboxConnectionId": "mbx-mistral-invalid"},
                    headers={"Idempotency-Key": "mistral-invalid"},
                )
                assert response.status_code == 503
                assert response.json()["detail"] == (
                    "Mistral is not configured: MISTRAL_MODEL must be a real Mistral model name"
                )
                assert "must-not-appear-in-error" not in response.text

    asyncio.run(scenario())
