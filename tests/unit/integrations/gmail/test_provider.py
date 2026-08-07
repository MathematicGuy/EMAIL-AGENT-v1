import asyncio
import base64
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from cowork_agent.config import GMAIL_READONLY_SCOPE, GmailSettings
from cowork_agent.integrations.gmail.auth import OAuthStateManager, TokenCipher
from cowork_agent.integrations.gmail.provider import (
    GmailConnectionService,
    GmailOAuthGrant,
    GoogleOAuthDriver,
    _parse_message,
)
from cowork_agent.persistence.repositories.mailbox_connections import (
    SQLiteMailboxConnectionRepository,
)


def gmail_environment(tmp_path: Path) -> dict[str, str]:
    return {
        "GMAIL_CLIENT_ID": "client-id.apps.googleusercontent.com",
        "GMAIL_CLIENT_SECRET": "client-secret",
        "GMAIL_REDIRECT_URI": "http://localhost:8000/v1/mail-todo/oauth/gmail/callback",
        "GMAIL_SCOPES": GMAIL_READONLY_SCOPE,
        "GMAIL_CONNECTION_DB_PATH": str(tmp_path / "connections.db"),
        "TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "OAUTH_STATE_SECRET": "state-secret-that-is-at-least-32-characters",
    }


def test_gmail_settings_allow_readonly_scope_and_redact_secrets(tmp_path: Path) -> None:
    settings = GmailSettings.from_env(gmail_environment(tmp_path), load_env_file=False)
    assert settings.scopes == (GMAIL_READONLY_SCOPE,)
    assert "client-secret" not in repr(settings)
    assert settings.redirect_uri.endswith("/oauth/gmail/callback")


def test_gmail_settings_reject_write_scope(tmp_path: Path) -> None:
    values = gmail_environment(tmp_path)
    values["GMAIL_SCOPES"] = "https://www.googleapis.com/auth/gmail.modify"
    with pytest.raises(ValueError, match="gmail.readonly"):
        GmailSettings.from_env(values, load_env_file=False)


def test_google_oauth_reuses_same_pkce_verifier_for_callback(tmp_path: Path) -> None:
    settings = GmailSettings.from_env(gmail_environment(tmp_path), load_env_file=False)
    driver = GoogleOAuthDriver(settings)
    verifier = "v" * 64
    authorization_url = driver.authorization_url("signed-state", verifier)
    callback_flow = driver._flow("signed-state", verifier)
    assert "code_challenge=" in authorization_url
    assert callback_flow.code_verifier == verifier
    assert callback_flow.autogenerate_code_verifier is False


def test_oauth_state_is_signed_expiring_and_single_use() -> None:
    async def scenario() -> None:
        now = [1_000.0]
        manager = OAuthStateManager(
            "state-secret-that-is-at-least-32-characters",
            60,
            clock=lambda: now[0],
        )
        state = manager.issue(context="verifier-1")
        assert await manager.consume(state) == "verifier-1"
        with pytest.raises(ValueError, match="already been used"):
            await manager.consume(state)
        expired = manager.issue(context="verifier-2")
        now[0] += 61
        with pytest.raises(ValueError, match="expired"):
            await manager.consume(expired)

    asyncio.run(scenario())


class FakeOAuthDriver:
    def __init__(self) -> None:
        self.state = ""
        self.code_verifier = ""

    def authorization_url(self, state: str, code_verifier: str) -> str:
        self.state = state
        self.code_verifier = code_verifier
        return f"https://accounts.google.test/auth?state={state}"

    async def exchange(
        self, state: str, authorization_response: str, code_verifier: str
    ) -> GmailOAuthGrant:
        assert state == self.state
        assert code_verifier == self.code_verifier
        assert 43 <= len(code_verifier) <= 128
        assert "code=test-code" in authorization_response
        return GmailOAuthGrant("owner@example.com", "refresh-token", (GMAIL_READONLY_SCOPE,))


def test_oauth_completion_encrypts_and_persists_refresh_token(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = GmailSettings.from_env(gmail_environment(tmp_path), load_env_file=False)
        repository = SQLiteMailboxConnectionRepository(settings.connection_db_path)
        await repository.initialize()
        cipher = TokenCipher(settings.token_encryption_key)
        driver = FakeOAuthDriver()
        service = GmailConnectionService(
            settings,
            repository,
            cipher,
            OAuthStateManager(settings.oauth_state_secret, 600),
            driver,
        )
        url = service.begin()
        assert url.startswith("https://accounts.google.test/")
        connection = await service.complete(
            driver.state,
            f"{settings.redirect_uri}?state={driver.state}&code=test-code",
        )
        assert connection.user_id == "owner@example.com"
        assert connection.email_address == "owner@example.com"
        assert connection.encrypted_refresh_token != "refresh-token"
        assert cipher.decrypt(connection.encrypted_refresh_token) == "refresh-token"
        stored = await repository.get(connection.id)
        assert stored == connection

    asyncio.run(scenario())


def test_gmail_message_parser_reads_text_headers_and_attachment() -> None:
    encoded_body = base64.urlsafe_b64encode("Vui lòng gửi báo cáo".encode()).decode()
    message = _parse_message(
        {
            "id": "msg-1",
            "threadId": "thread-1",
            "internalDate": "1785729600000",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Báo cáo tuần"},
                    {"name": "From", "value": "Nguyễn An <an@example.com>"},
                ],
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": encoded_body}},
                    {
                        "mimeType": "application/pdf",
                        "filename": "brief.pdf",
                        "body": {"attachmentId": "att-1", "size": 1234},
                    },
                ],
            },
        }
    )
    assert message.subject == "Báo cáo tuần"
    assert message.sender_address == "an@example.com"
    assert message.text_body == "Vui lòng gửi báo cáo"
    assert message.attachments[0].attachment_id == "att-1"


def test_gmail_message_parser_preserves_html_action_links() -> None:
    plain = base64.urlsafe_b64encode(b"Build failed for HR-Chatbot").decode()
    rich = base64.urlsafe_b64encode(
        b'<p>Build failed</p><a href="https://railway.app/project/build-123">View build logs</a>'
    ).decode()
    message = _parse_message(
        {
            "id": "msg-build",
            "threadId": "thread-build",
            "internalDate": "1785729600000",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Build failed"},
                    {"name": "From", "value": "Railway <hello@notify.railway.app>"},
                ],
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": plain}},
                    {"mimeType": "text/html", "body": {"data": rich}},
                ],
            },
        }
    )

    assert "Build failed for HR-Chatbot" in message.text_body
    assert "View build logs [https://railway.app/project/build-123]" in message.text_body
