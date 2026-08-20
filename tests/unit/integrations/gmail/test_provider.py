import asyncio
import base64
import random
import ssl
from datetime import UTC, datetime
from pathlib import Path

import httplib2  # type: ignore[import-untyped]
import pytest
from cryptography.fernet import Fernet
from google.auth.exceptions import TransportError
from googleapiclient.errors import HttpError  # type: ignore[import-untyped]

from cowork_agent.config import GMAIL_READONLY_SCOPE, GmailSettings
from cowork_agent.domain import MailboxConnection
from cowork_agent.domain.target_contracts import BodyFormat, FetchStatus
from cowork_agent.identity import VerifiedPrincipal
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
    assert settings.fetch_concurrency == 6
    assert "client-secret" not in repr(settings)
    assert settings.redirect_uri.endswith("/oauth/gmail/callback")
    assert settings.frontend_url is None


@pytest.mark.parametrize("value", ["0", "9"])
def test_gmail_settings_reject_out_of_range_fetch_concurrency(
    tmp_path: Path, value: str
) -> None:
    values = gmail_environment(tmp_path)
    values["GMAIL_FETCH_CONCURRENCY"] = value

    with pytest.raises(ValueError, match="GMAIL_FETCH_CONCURRENCY"):
        GmailSettings.from_env(values, load_env_file=False)


def test_gmail_settings_accept_safe_frontend_url(tmp_path: Path) -> None:
    values = gmail_environment(tmp_path)
    values["FRONTEND_URL"] = "http://localhost:5173/"
    settings = GmailSettings.from_env(values, load_env_file=False)
    assert settings.frontend_url == "http://localhost:5173"


def test_gmail_settings_reject_insecure_remote_frontend_url(tmp_path: Path) -> None:
    values = gmail_environment(tmp_path)
    values["FRONTEND_URL"] = "http://example.com"
    with pytest.raises(ValueError, match="FRONTEND_URL"):
        GmailSettings.from_env(values, load_env_file=False)


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


def test_oauth_completion_persists_the_resolved_internal_principal(tmp_path: Path) -> None:
    class RecordingRepository:
        def __init__(self) -> None:
            self.connection: MailboxConnection | None = None

        async def upsert(self, connection: MailboxConnection) -> MailboxConnection:
            self.connection = connection
            return connection

    async def resolve_principal(email_address: str) -> VerifiedPrincipal:
        assert email_address == "owner@example.com"
        return VerifiedPrincipal(user_id="internal-user-1")

    async def scenario() -> None:
        settings = GmailSettings.from_env(gmail_environment(tmp_path), load_env_file=False)
        repository = RecordingRepository()
        driver = FakeOAuthDriver()
        service = GmailConnectionService(
            settings,
            repository,  # type: ignore[arg-type]
            TokenCipher(settings.token_encryption_key),
            OAuthStateManager(settings.oauth_state_secret, 600),
            driver,
            principal_resolver=resolve_principal,
        )
        service.begin()
        connection = await service.complete(
            driver.state,
            f"{settings.redirect_uri}?state={driver.state}&code=test-code",
        )

        # The resolver, not the Google grant, decides who owns the connection:
        # email_address stays the mailbox, user_id becomes the internal principal.
        assert connection.user_id == "internal-user-1"
        assert connection.email_address == "owner@example.com"
        assert repository.connection == connection

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
    assert message.run_id == "" and message.user_id == ""


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
    assert "View build logs [link1]" in message.normalized_body
    assert message.source_links[0].ref == "link1"
    assert message.source_links[0].label == "View build logs"
    assert message.source_links[0].url == "https://railway.app/project/build-123"
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


def test_gmail_message_parser_preserves_html_block_boundaries() -> None:
    html_body = (
        "<p>Overview</p>"
        "<ul><li>First action</li><li>Second action</li></ul>"
        '<p><a href="https://example.test/item">Open item</a></p>'
    )
    rich = base64.urlsafe_b64encode(html_body.encode()).decode()
    message = _parse_message(
        {
            "id": "msg-blocks",
            "threadId": "thread-blocks",
            "internalDate": "1785729600000",
            "payload": {
                "headers": [],
                "parts": [
                    {"mimeType": "text/html", "body": {"data": rich}},
                ],
            },
        }
    )

    assert message.normalized_body.splitlines() == [
        "Overview",
        "First action",
        "Second action",
        "Open item [link1]",
    ]
    assert message.body_format is BodyFormat.HTML_CONVERTED


def test_gmail_message_parser_deduplicates_urls_and_keeps_bare_link_label_null() -> None:
    html_body = (
        '<p><a href="https://example.test/item">Open item</a></p>'
        '<p><a href="https://example.test/item">Open again</a></p>'
        '<p>Fallback https://example.test/bare</p>'
    )
    rich = base64.urlsafe_b64encode(html_body.encode()).decode()

    message = _parse_message(
        {
            "id": "msg-links",
            "threadId": "thread-links",
            "internalDate": "1785729600000",
            "payload": {
                "headers": [],
                "parts": [{"mimeType": "text/html", "body": {"data": rich}}],
            },
        }
    )

    assert message.normalized_body.splitlines() == [
        "Open item [link1]",
        "Open again [link1]",
        "Fallback [link2]",
    ]
    assert [(link.ref, link.label, link.url) for link in message.source_links] == [
        ("link1", "Open item", "https://example.test/item"),
        ("link2", None, "https://example.test/bare"),
    ]


def test_gmail_message_parser_normalizes_wrapped_plain_urls_and_appends_each_url_once() -> None:
    plain = base64.urlsafe_b64encode(b"Reference [https://example.test/plain]").decode()
    rich = base64.urlsafe_b64encode(
        b'<a href="https://example.test/extra">First label</a>'
        b'<a href="https://example.test/extra">Second label</a>'
    ).decode()

    message = _parse_message(
        {
            "id": "msg-wrapped-links",
            "threadId": "thread-wrapped-links",
            "internalDate": "1785729600000",
            "payload": {
                "headers": [],
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": plain}},
                    {"mimeType": "text/html", "body": {"data": rich}},
                ],
            },
        }
    )

    assert message.normalized_body.count("[link1]") == 1
    assert message.normalized_body.count("[link2]") == 1
    assert [link.url for link in message.source_links] == [
        "https://example.test/plain",
        "https://example.test/extra",
    ]


def test_gmail_message_parser_keeps_one_primary_link_per_digest_card() -> None:
    first_title = "A Practical Guide to Reliable Agent Workflows"
    second_title = "Understanding Retrieval Quality in Production Systems"
    plain = base64.urlsafe_b64encode(
        f"Today's highlights\n{first_title}\n{second_title}".encode()
    ).decode()
    rich = base64.urlsafe_b64encode(
        (
            '<section class="card">'
            f'<a href="https://example.test/posts/reliable-agent-workflows">'
            f'<img alt="{first_title}"></a>'
            f'<a href="https://example.test/posts/reliable-agent-workflows">'
            f"{first_title}</a>"
            '<a href="https://example.test/@author-one">'
            '<img alt="Author profile"></a>'
            "</section>"
            '<section class="card">'
            f'<a href="https://example.test/posts/retrieval-quality-production">'
            f'<img alt="{second_title}"></a>'
            "</section>"
            '<footer><a href="https://example.test/unsubscribe">Unsubscribe</a>'
            '<a href="https://example.test/privacy">Privacy Policy</a>'
            '<a href="https://example.test/tracking"><img></a></footer>'
        ).encode()
    ).decode()

    message = _parse_message(
        {
            "id": "msg-digest",
            "threadId": "thread-digest",
            "internalDate": "1785729600000",
            "payload": {
                "headers": [],
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": plain}},
                    {"mimeType": "text/html", "body": {"data": rich}},
                ],
            },
        }
    )

    assert message.normalized_body.count("[link1]") == 1
    assert message.normalized_body.count("[link3]") == 1
    assert first_title + " [link1]" in message.normalized_body
    assert second_title + " [link3]" in message.normalized_body
    assert "Open link" not in message.normalized_body
    assert "Author profile [link2]" not in message.normalized_body
    assert "Unsubscribe [link4]" not in message.normalized_body
    assert "Privacy Policy [link5]" not in message.normalized_body
    assert "[link6]" not in message.normalized_body
    assert [(link.ref, link.label) for link in message.source_links] == [
        ("link1", first_title),
        ("link2", "Author profile"),
        ("link3", second_title),
        ("link4", "Unsubscribe"),
        ("link5", "Privacy Policy"),
        ("link6", None),
    ]


def test_gmail_message_parser_hides_url_used_as_anchor_label() -> None:
    url = "https://example.test/long-tracking-link"
    rich = base64.urlsafe_b64encode(
        f'<a href="{url}">https://display.example.test/item</a>'.encode()
    ).decode()

    message = _parse_message(
        {
            "id": "msg-url-label",
            "threadId": "thread-url-label",
            "internalDate": "1785729600000",
            "payload": {
                "headers": [],
                "parts": [{"mimeType": "text/html", "body": {"data": rich}}],
            },
        }
    )

    assert message.normalized_body == ""
    assert message.source_links[0].label is None


def test_gmail_message_parser_strips_artifact_controls_but_preserves_emoji_zwj() -> None:
    plain = base64.urlsafe_b64encode(
        "A\u200c\u034f\u00adB 👩\u200d💻 می\u200cروم".encode()
    ).decode()

    message = _parse_message(
        {
            "id": "msg-controls",
            "threadId": "thread-controls",
            "internalDate": "1785729600000",
            "payload": {
                "headers": [],
                "parts": [{"mimeType": "text/plain", "body": {"data": plain}}],
            },
        }
    )

    assert message.normalized_body == "AB 👩‍💻 می‌روم"


def test_gmail_message_parser_normalizes_html_embedded_in_plain_mime() -> None:
    plain_body = (
        "<!-- rendering metadata -->"
        "<v:rect><strong>Important</strong></v:rect>"
        '<br><a href="https://example.test/item">Open item</a>&amp; continue'
    )
    plain = base64.urlsafe_b64encode(plain_body.encode()).decode()

    message = _parse_message(
        {
            "id": "msg-plain-html",
            "threadId": "thread-plain-html",
            "internalDate": "1785729600000",
            "payload": {
                "headers": [],
                "parts": [{"mimeType": "text/plain", "body": {"data": plain}}],
            },
        }
    )

    assert message.normalized_body.splitlines() == [
        "Important",
        "Open item [link1] & continue",
    ]


def test_gmail_message_parser_normalizes_markdown_links() -> None:
    plain = base64.urlsafe_b64encode(
        b"Review [the document](https://example.test/document)."
    ).decode()

    message = _parse_message(
        {
            "id": "msg-markdown-link",
            "threadId": "thread-markdown-link",
            "internalDate": "1785729600000",
            "payload": {
                "headers": [],
                "parts": [{"mimeType": "text/plain", "body": {"data": plain}}],
            },
        }
    )

    assert message.normalized_body == "Review the document [link1]."
    assert message.source_links[0].label == "the document"


def test_gmail_message_parser_removes_only_whole_separator_lines() -> None:
    plain = base64.urlsafe_b64encode(
        b"Keep this\n----------------\n|-----|\n--\n000\n...\nKeep-this-inline"
    ).decode()

    message = _parse_message(
        {
            "id": "msg-separators",
            "threadId": "thread-separators",
            "internalDate": "1785729600000",
            "payload": {
                "headers": [],
                "parts": [{"mimeType": "text/plain", "body": {"data": plain}}],
            },
        }
    )

    assert message.normalized_body.splitlines() == [
        "Keep this",
        "000",
        "...",
        "Keep-this-inline",
    ]


def test_gmail_message_parser_unescapes_plain_entities() -> None:
    plain = base64.urlsafe_b64encode(b"Terms &amp; conditions &ndash; review").decode()

    message = _parse_message(
        {
            "id": "msg-entities",
            "threadId": "thread-entities",
            "internalDate": "1785729600000",
            "payload": {
                "headers": [],
                "parts": [{"mimeType": "text/plain", "body": {"data": plain}}],
            },
        }
    )

    assert message.normalized_body == "Terms & conditions – review"


@pytest.mark.parametrize(
    ("plain_body", "expected"),
    [
        (
            "Review [item](https://example.test/one https://example.test/two",
            "Review item [link1] [link2]",
        ),
        (
            "Review [item](https://example.test/item\ntracking metadata)",
            "Review item [link1]",
        ),
    ],
)
def test_gmail_message_parser_collapses_only_canonical_ref_wrappers(
    plain_body: str,
    expected: str,
) -> None:
    plain = base64.urlsafe_b64encode(plain_body.encode()).decode()

    message = _parse_message(
        {
            "id": "msg-ref-wrapper",
            "threadId": "thread-ref-wrapper",
            "internalDate": "1785729600000",
            "payload": {
                "headers": [],
                "parts": [{"mimeType": "text/plain", "body": {"data": plain}}],
            },
        }
    )

    assert message.normalized_body == expected


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


def test_service_cache_builds_once_and_requests_receive_distinct_transports(
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

    class AuthorizedTransport:
        def __init__(self, credentials: object, http: object) -> None:
            self.credentials = credentials
            self.http = http

    captured: dict[str, object] = {}
    transports: list[object] = []

    def fake_build(*args: object, **kwargs: object) -> object:
        captured["calls"] = int(captured.get("calls", 0)) + 1
        captured["credentials"] = kwargs["credentials"]
        captured["request_builder"] = kwargs["requestBuilder"]
        return object()

    def fake_http() -> object:
        transport = object()
        transports.append(transport)
        return transport

    def fake_authorized_http(credentials: object, *, http: object) -> AuthorizedTransport:
        return AuthorizedTransport(credentials, http)

    def fake_request(http: object, *args: object, **kwargs: object) -> object:
        return http

    monkeypatch.setattr(gmail_provider, "build", fake_build)
    monkeypatch.setattr(gmail_provider.httplib2, "Http", fake_http)
    monkeypatch.setattr(gmail_provider, "AuthorizedHttp", fake_authorized_http)
    monkeypatch.setattr(gmail_provider, "HttpRequest", fake_request)
    settings = GmailSettings.from_env(gmail_environment(tmp_path), load_env_file=False)
    adapter = GmailMailboxAdapter(settings, Repository(), DecryptingCipher())  # type: ignore[arg-type]

    async def scenario() -> None:
        first, second = await asyncio.gather(adapter._service("mbx-1"), adapter._service("mbx-1"))
        assert first is second

    asyncio.run(scenario())
    assert captured["calls"] == 1

    request_builder = captured["request_builder"]
    assert callable(request_builder)
    first_request = request_builder(object(), "postproc", "https://example.test/one")
    second_request = request_builder(object(), "postproc", "https://example.test/two")

    assert isinstance(first_request, AuthorizedTransport)
    assert isinstance(second_request, AuthorizedTransport)
    assert first_request.credentials is captured["credentials"]
    assert second_request.credentials is captured["credentials"]
    assert first_request.http is not second_request.http
    assert transports == [first_request.http, second_request.http]


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


@pytest.mark.parametrize(
    "transient_error",
    [
        ssl.SSLEOFError("EOF occurred in violation of protocol"),
        ssl.SSLError("SSL handshake failed"),
        ConnectionResetError("Connection reset by peer"),
        TimeoutError("Connection timed out"),
        httplib2.error.HttpLib2Error("HttpLib2 transport failed"),
    ],
)
def test_call_retries_network_and_ssl_errors_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, transient_error: Exception
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    calls = {"count": 0}

    def flaky_network() -> dict[str, object]:
        calls["count"] += 1
        if calls["count"] < 3:
            raise transient_error
        return {"ok": True}

    result = asyncio.run(_adapter()._call(flaky_network))
    assert result == {"ok": True}
    assert calls["count"] == 3
    assert len(sleeps) == 2


def test_call_exhausts_network_error_into_temporary_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    calls = {"count": 0}

    def persistent_ssl_error() -> dict[str, object]:
        calls["count"] += 1
        raise ssl.SSLEOFError("EOF occurred in violation of protocol")

    with pytest.raises(MailboxTemporaryError):
        asyncio.run(_adapter()._call(persistent_ssl_error))
    assert calls["count"] == 3
