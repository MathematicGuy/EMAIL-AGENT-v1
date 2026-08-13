"""Shared harness freezing the legacy HTTP contract against deterministic fakes.

Compatibility suite (P0-A): pins current API/result behavior so V1-M*
refactors cannot silently change what master-comparison §7 "Compatibility
contract" promises.
"""

import asyncio
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

from cowork_agent.app import create_app
from cowork_agent.config import GMAIL_READONLY_SCOPE
from cowork_agent.domain import MailboxConnection, Priority
from cowork_agent.domain.target_contracts import (
    Actionability,
    BodyFormat,
    EmailRouteDecision,
    EphemeralEmailEnvelope,
    FetchStatus,
    PlanStep,
    ReasonCode,
    Route,
    Task,
    ValidationStatus,
)
from cowork_agent.features.email_action_plan.short_term import ShortTermStore
from cowork_agent.features.email_action_plan.workflow import DigestWorker
from cowork_agent.integrations.gmail.fakes import FakeMailbox, SafeTextAttachmentExtractor
from cowork_agent.integrations.llm.fakes import FakePlanGenerator, FakeRouteClassifier

OWNER_EMAIL = "compat@example.com"
CONNECTION_ID = "mbx-compat"


@pytest.fixture()
def compat_env(monkeypatch: pytest.MonkeyPatch) -> None:
    work_dir = Path(tempfile.mkdtemp(prefix="compat-"))
    monkeypatch.setenv("DATABASE_URL", "")
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
) -> EphemeralEmailEnvelope:
    stamp = received_at or datetime(2026, 8, 3, 8, tzinfo=UTC)
    return EphemeralEmailEnvelope(
        run_id="",
        user_id="",
        gmail_message_id=message_id,
        gmail_thread_id=thread_id,
        gmail_url=f"https://mail.google.com/mail/u/0/#inbox/{message_id}",
        sender_name="Người Gửi",
        sender_email=f"{message_id}@example.com",
        recipients=(),
        subject=subject,
        received_at=stamp,
        labels=(),
        normalized_body=body,
        body_format=BodyFormat.TEXT,
        attachments_present=False,
        fetch_status=FetchStatus.COMPLETE,
    )


def make_task(
    message_id: str,
    title: str,
    *,
    summary: str = "Yêu cầu cần xử lý.",
    deadline: datetime | None = None,
    priority: Priority | None = None,
    incident_key: str | None = None,
) -> Task:
    """Canned §6.6 Task standing in for one Generator call's output."""
    return Task(
        task_id=f"task_{message_id}",
        run_id="run-compat",
        gmail_message_id=message_id,
        gmail_url=f"https://mail.google.com/mail/u/0/#inbox/{message_id}",
        source_message_ids=(message_id,),
        incident_key=incident_key,
        title=title,
        request_summary=summary,
        actionability=Actionability.ACTION_REQUIRED,
        route=Route.DIRECT_PLAN,
        priority=priority,
        deadline=deadline,
        action_plan=(PlanStep(1, title, ()),),
        supporting_documents=(),
        missing_information=(),
        classifier_confidence=0.9,
        generation_confidence=0.9,
        validation_status=ValidationStatus.SYSTEM_GENERATED,
        created_at=datetime(2026, 8, 3, 8, tzinfo=UTC),
    )


#: Classifier override resolving informational emails to NO_ACTION, so the
#: Route Resolver skips generation for them (the legacy "newsletter" case).
INFORMATIONAL_DECISION = EmailRouteDecision(
    actionability=Actionability.INFORMATIONAL,
    route=Route.NO_ACTION,
    candidate_action_item=None,
    email_is_sufficient=True,
    knowledge_gaps=(),
    retrieval_query=None,
    expected_document_types=(),
    reason_codes=(ReasonCode.NO_ACTION,),
    confidence=0.9,
)


@dataclass
class CompatSession:
    """App + client wired to deterministic fakes for compatibility assertions."""

    messages: Sequence[EphemeralEmailEnvelope] = ()
    tasks: tuple[Task, ...] = ()
    decisions: Mapping[str, EmailRouteDecision] | None = None
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
            FakeRouteClassifier(self.decisions),
            FakePlanGenerator(self.tasks),
            ShortTermStore(),
            self.app.state.task_repository,
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
        messages: Sequence[EphemeralEmailEnvelope] = (),
        tasks: tuple[Task, ...] = (),
        decisions: Mapping[str, EmailRouteDecision] | None = None,
        mailbox: object | None = None,
    ):
        async with CompatSession(messages, tasks, decisions, mailbox) as session:
            yield session

    return factory


def run_scenario(scenario) -> None:
    asyncio.run(scenario())
