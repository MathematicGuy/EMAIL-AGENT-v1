"""Shared harness freezing the legacy HTTP contract against deterministic fakes.

Compatibility suite (P0-A): pins current API/result behavior so V1-M*
refactors cannot silently change what master-comparison §7 "Compatibility
contract" promises.
"""

import asyncio
import tempfile
from collections.abc import Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

from cowork_agent.app import create_app
from cowork_agent.config import GMAIL_READONLY_SCOPE
from cowork_agent.domain import EmailEnvelope, MailboxConnection
from cowork_agent.features.email_action_plan.schemas import ExtractionBatch
from cowork_agent.features.email_action_plan.workflow import DigestWorker
from cowork_agent.integrations.gmail.fakes import FakeMailbox, SafeTextAttachmentExtractor
from cowork_agent.integrations.llm.fakes import FakeActionExtractor
from cowork_agent.orchestration.local import InMemoryOutbox

OWNER_EMAIL = "compat@example.com"
CONNECTION_ID = "mbx-compat"


@pytest.fixture()
def compat_env(monkeypatch: pytest.MonkeyPatch) -> None:
    work_dir = Path(tempfile.mkdtemp(prefix="compat-"))
    monkeypatch.setenv("GMAIL_CLIENT_ID", "compat.apps.googleusercontent.com")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "compat-secret")
    monkeypatch.setenv(
        "GMAIL_REDIRECT_URI", "http://localhost:8000/v1/mail-todo/oauth/gmail/callback"
    )
    monkeypatch.setenv("GMAIL_SCOPES", GMAIL_READONLY_SCOPE)
    monkeypatch.setenv("GMAIL_CONNECTION_DB_PATH", str(work_dir / "compat-connections.db"))
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("OAUTH_STATE_SECRET", "compat-state-secret-at-least-32-characters")
    monkeypatch.setenv("APP_ENV", "development")


def make_email(
    message_id: str,
    thread_id: str,
    subject: str,
    body: str = "Nội dung thân email.",
    received_at: datetime | None = None,
) -> EmailEnvelope:
    stamp = received_at or datetime(2026, 8, 3, 8, tzinfo=UTC)
    return EmailEnvelope(
        provider_message_id=message_id,
        provider_thread_id=thread_id,
        deep_link=f"https://mail.google.com/mail/u/0/#inbox/{message_id}",
        subject=subject,
        sender_name="Người Gửi",
        sender_address=f"{message_id}@example.com",
        sent_at=stamp,
        received_at=stamp,
        text_body=body,
        attachments=(),
    )


@dataclass
class CompatSession:
    """App + client wired to deterministic fakes for compatibility assertions."""

    messages: Sequence[EmailEnvelope] = ()
    batch: ExtractionBatch | None = None
    mailbox: object | None = None

    async def __aenter__(self) -> "CompatSession":
        self.app = create_app()
        self._lifespan = self.app.router.lifespan_context(self.app)
        await self._lifespan.__aenter__()
        now = datetime.now(UTC)
        await self.app.state.connection_repository.upsert(
            MailboxConnection(
                id=CONNECTION_ID,
                user_id=OWNER_EMAIL,
                provider="gmail",
                external_account_id="compat-account",
                email_address=OWNER_EMAIL,
                encrypted_refresh_token="compat-token",
                scopes=(GMAIL_READONLY_SCOPE,),
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        self.app.state.digest_worker = DigestWorker(
            self.app.state.run_repository,
            self.app.state.result_repository,
            self.mailbox or FakeMailbox(list(self.messages)),
            SafeTextAttachmentExtractor(),
            FakeActionExtractor(self.batch or ExtractionBatch(())),
            InMemoryOutbox(),
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://compat.test"
        )
        await self.client.__aenter__()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.client.__aexit__(*exc_info)
        await self._lifespan.__aexit__(*exc_info)

    async def post_run(
        self,
        idempotency_key: str,
        *,
        query: str | None = None,
        max_emails: int | None = None,
    ) -> httpx.Response:
        payload: dict[str, object] = {"mailboxConnectionId": CONNECTION_ID}
        if query is not None:
            payload["query"] = query
        if max_emails is not None:
            payload["maxEmails"] = max_emails
        return await self.client.post(
            "/v1/mail-todo/runs",
            json=payload,
            headers={"Idempotency-Key": idempotency_key},
        )

    async def get_run(self, run_id: str) -> httpx.Response:
        return await self.client.get(f"/v1/mail-todo/runs/{run_id}")

    async def get_result(self, run_id: str) -> httpx.Response:
        return await self.client.get(f"/v1/mail-todo/runs/{run_id}/result")


@pytest.fixture()
def compat_session(compat_env: None):
    """Factory returning an async context manager for a compatibility session."""

    @asynccontextmanager
    async def factory(
        messages: Sequence[EmailEnvelope] = (),
        batch: ExtractionBatch | None = None,
        mailbox: object | None = None,
    ):
        async with CompatSession(messages, batch, mailbox) as session:
            yield session

    return factory


def run_scenario(scenario) -> None:
    asyncio.run(scenario())
