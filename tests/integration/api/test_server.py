import asyncio
from pathlib import Path

import httpx
from cryptography.fernet import Fernet

from cowork_agent.app import create_app
from cowork_agent.config import GMAIL_READONLY_SCOPE


def test_server_starts_and_redirects_to_google_oauth(tmp_path: Path, monkeypatch: object) -> None:
    values = {
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
