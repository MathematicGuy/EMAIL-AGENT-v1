import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
from cryptography.fernet import Fernet

from cowork_agent.app import create_app
from cowork_agent.config import GMAIL_READONLY_SCOPE
from cowork_agent.domain import MailboxConnection


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
