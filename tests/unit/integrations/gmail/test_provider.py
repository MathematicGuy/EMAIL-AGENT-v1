import asyncio
import base64
import random
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from google.auth.exceptions import TransportError
from googleapiclient.errors import HttpError  # type: ignore[import-untyped]

from cowork_agent.config import GMAIL_READONLY_SCOPE, GmailSettings
from cowork_agent.domain import MailboxConnection
from cowork_agent.domain.target_contracts import BodyFormat, FetchStatus
from cowork_agent.integrations.gmail import provider as gmail_provider
from cowork_agent.integrations.gmail.auth import OAuthStateManager, TokenCipher
from cowork_agent.integrations.gmail.provider import (
    GmailConnectionService,
    GmailMailboxAdapter,
    GmailOAuthGrant,
    GoogleOAuthDriver,
    MailboxReauthRequiredError,
    MailboxTemporaryError,
    _parse_message,
    _retry_delay,
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
            "labelIds": ["INBOX", "UNREAD"],
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Báo cáo tuần"},
                    {"name": "From", "value": "Nguyễn An <an@example.com>"},
                    {"name": "To", "value": "Bình <binh@example.com>"},
                    {"name": "Cc", "value": "chi@example.com, Trưởng Phòng <truong@example.com>"},
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
    assert message.sender_name == "Nguyễn An"
    assert message.sender_email == "an@example.com"
    assert message.recipients == ("binh@example.com", "chi@example.com", "truong@example.com")
    assert message.labels == ("INBOX", "UNREAD")
    assert message.normalized_body == "Vui lòng gửi báo cáo"
    assert message.body_format is BodyFormat.TEXT
    assert message.fetch_status is FetchStatus.COMPLETE
    assert message.attachments_present is True
    assert message.attachments_processed is False
    assert message.run_id == "" and message.tenant_id == "" and message.user_id == ""


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

    assert "Build failed for HR-Chatbot" in message.normalized_body
    assert "View build logs [https://railway.app/project/build-123]" in message.normalized_body
    assert message.body_format is BodyFormat.TEXT
    assert message.fetch_status is FetchStatus.COMPLETE


def test_gmail_message_parser_marks_html_only_bodies_as_html_converted() -> None:
    html_body = '<p>Thông báo bảo trì hệ thống</p><a href="https://example.com">Chi tiết</a>'
    rich = base64.urlsafe_b64encode(html_body.encode()).decode()
    message = _parse_message(
        {
            "id": "msg-html",
            "threadId": "thread-html",
            "internalDate": "1785729600000",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Bảo trì"},
                    {"name": "From", "value": "noreply@example.com"},
                ],
                "parts": [
                    {"mimeType": "text/html", "body": {"data": rich}},
                ],
            },
        }
    )

    assert "Thông báo bảo trì hệ thống" in message.normalized_body
    assert message.body_format is BodyFormat.HTML_CONVERTED
    assert message.fetch_status is FetchStatus.COMPLETE


def test_gmail_message_parser_marks_partial_fetch_without_usable_body() -> None:
    bodyless = _parse_message(
        {
            "id": "msg-empty",
            "threadId": "thread-empty",
            "internalDate": "1785729600000",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Không có nội dung"},
                    {"name": "From", "value": "an@example.com"},
                ],
                "parts": [],
            },
        }
    )
    assert bodyless.normalized_body == ""
    assert bodyless.fetch_status is FetchStatus.PARTIAL

    missing_payload = _parse_message(
        {
            "id": "msg-no-payload",
            "threadId": "thread-no-payload",
            "internalDate": "1785729600000",
        }
    )
    assert missing_payload.normalized_body == ""
    assert missing_payload.fetch_status is FetchStatus.PARTIAL
    assert missing_payload.attachments_present is False


class _Resp:
    def __init__(self, status: int) -> None:
        self.status = status
        self.reason = "simulated failure"


def _http_error(status: int) -> "HttpError":
    return HttpError(_Resp(status), b"unavailable")  # type: ignore[arg-type]


def _adapter() -> GmailMailboxAdapter:
    # _call touches none of the dependencies, so placeholders suffice.
    return GmailMailboxAdapter(object(), object(), object())  # type: ignore[arg-type]


def _active_connection() -> MailboxConnection:
    now = datetime.now(UTC)
    return MailboxConnection(
        id="mbx-1",
        user_id="owner@example.com",
        provider="gmail",
        external_account_id="owner@example.com",
        email_address="owner@example.com",
        encrypted_refresh_token="encrypted-token",
        scopes=(GMAIL_READONLY_SCOPE,),
        status="active",
        created_at=now,
        updated_at=now,
    )


def test_service_translates_only_stored_token_decryption_errors(tmp_path: Path) -> None:
    class Repository:
        async def get(self, connection_id: str) -> MailboxConnection:
            assert connection_id == "mbx-1"
            return _active_connection()

    class FailingCipher:
        def decrypt(self, encrypted_token: str) -> str:
            assert encrypted_token == "encrypted-token"
            raise ValueError("Stored Gmail token cannot be decrypted")

    settings = GmailSettings.from_env(gmail_environment(tmp_path), load_env_file=False)
    adapter = GmailMailboxAdapter(settings, Repository(), FailingCipher())  # type: ignore[arg-type]

    with pytest.raises(MailboxReauthRequiredError) as raised:
        asyncio.run(adapter._service("mbx-1"))

    assert raised.value.error_code == "GMAIL_REAUTH_REQUIRED"
    assert raised.value.safe_message == (
        "Gmail access needs to be reconnected. Reconnect Gmail and retry."
    )


def test_service_does_not_translate_unrelated_build_value_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class Repository:
        async def get(self, connection_id: str) -> MailboxConnection:
            assert connection_id == "mbx-1"
            return _active_connection()

    class DecryptingCipher:
        def decrypt(self, encrypted_token: str) -> str:
            assert encrypted_token == "encrypted-token"
            return "refresh-token"

    def invalid_build(*args: object, **kwargs: object) -> None:
        raise ValueError("Gmail discovery document is invalid")

    monkeypatch.setattr(gmail_provider, "build", invalid_build)
    settings = GmailSettings.from_env(gmail_environment(tmp_path), load_env_file=False)
    adapter = GmailMailboxAdapter(settings, Repository(), DecryptingCipher())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="discovery document"):
        asyncio.run(adapter._service("mbx-1"))


def test_call_retries_transient_errors_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    calls = {"count": 0}

    def flaky() -> dict[str, object]:
        calls["count"] += 1
        if calls["count"] < 3:
            raise _http_error(503)
        return {"ok": True}

    result = asyncio.run(_adapter()._call(flaky))
    assert result == {"ok": True}
    assert calls["count"] == 3
    assert len(sleeps) == 2
    assert all(delay >= 0.0 for delay in sleeps)


def test_call_exhausts_budget_into_temporary_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    calls = {"count": 0}

    def always_down() -> dict[str, object]:
        calls["count"] += 1
        raise _http_error(503)

    with pytest.raises(MailboxTemporaryError):
        asyncio.run(_adapter()._call(always_down))
    assert calls["count"] == 3


def test_call_never_retries_authorization_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_sleep(delay: float) -> None:
        raise AssertionError("authorization errors must not sleep/retry")

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    calls = {"count": 0}

    def forbidden() -> dict[str, object]:
        calls["count"] += 1
        raise _http_error(403)

    with pytest.raises(MailboxReauthRequiredError):
        asyncio.run(_adapter()._call(forbidden))
    assert calls["count"] == 1


def test_transport_errors_are_retried_like_api_throttling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    calls = {"count": 0}

    def flaky_refresh() -> dict[str, object]:
        calls["count"] += 1
        if calls["count"] == 1:
            raise TransportError("token refresh hiccup")
        return {"ok": True}

    result = asyncio.run(_adapter()._call(flaky_refresh))
    assert result == {"ok": True}
    assert calls["count"] == 2 and len(sleeps) == 1


def test_retry_delay_is_bounded_and_jittered() -> None:
    random.seed(5441)
    delays = [_retry_delay(attempt) for attempt in (1, 2, 3) for _ in range(50)]
    assert all(delay >= 0.0 for delay in delays)
    assert all(delay <= 4.0 for delay in delays)
    # Jitter: 100 samples of attempt 1 cannot all be identical.
    assert len({_retry_delay(1) for _ in range(100)}) > 1
