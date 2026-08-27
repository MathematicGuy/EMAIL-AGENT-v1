"""Unit tests for ADR-011: Bounded reply chain context aggregation."""

import asyncio
from datetime import UTC, datetime, timedelta

from cowork_agent.domain import Priority, RunStatus
from cowork_agent.domain.target_contracts import (
    Actionability,
    BodyFormat,
    EphemeralEmailEnvelope,
    FetchStatus,
    PlanStep,
    Route,
    Task,
    ValidationStatus,
)
from cowork_agent.features.email_action_plan.ports import MailboxPort
from cowork_agent.features.email_action_plan.schemas import MessageRef, SearchPage
from cowork_agent.features.email_action_plan.short_term import ShortTermStore
from cowork_agent.features.email_action_plan.workflow import (
    CreateDigestRun,
    DigestWorker,
)
from cowork_agent.integrations.gmail.fakes import SafeTextAttachmentExtractor
from cowork_agent.integrations.llm.fakes import FakePlanGenerator, FakeRouteClassifier
from cowork_agent.persistence.repositories.local import (
    InMemoryResultRepository,
    InMemoryRunRepository,
    InMemoryTaskRepository,
)

BASE_TIME = datetime(2026, 8, 18, 10, 0, 0, tzinfo=UTC)


def make_email(
    message_id: str,
    thread_id: str,
    subject: str,
    received_at: datetime,
    body: str = "Test body",
) -> EphemeralEmailEnvelope:
    return EphemeralEmailEnvelope(
        run_id="",
        user_id="",
        gmail_message_id=message_id,
        gmail_thread_id=thread_id,
        gmail_url=f"https://mail.google.com/mail/u/0/#inbox/{thread_id}",
        sender_name="Sender",
        sender_email="sender@example.com",
        recipients=(),
        subject=subject,
        received_at=received_at,
        labels=(),
        normalized_body=body,
        body_format=BodyFormat.TEXT,
        attachments_present=False,
        fetch_status=FetchStatus.COMPLETE,
    )


def task_for(
    message_id: str,
    title: str,
    source_message_ids: tuple[str, ...] = (),
) -> Task:
    return Task(
        task_id=f"task_{message_id}",
        run_id="run-test",
        gmail_message_id=message_id,
        gmail_url=f"https://mail.google.com/mail/u/0/#inbox/{message_id}",
        source_message_ids=source_message_ids or (message_id,),
        incident_key=None,
        title=title,
        request_summary="Yêu cầu cần được xử lý.",
        actionability=Actionability.ACTION_REQUIRED,
        route=Route.DIRECT_PLAN,
        priority=Priority.MEDIUM,
        deadline=None,
        action_plan=(PlanStep(1, "Kiểm tra yêu cầu", ()), PlanStep(2, title, ())),
        supporting_documents=(),
        missing_information=(),
        classifier_confidence=0.9,
        generation_confidence=0.9,
        validation_status=ValidationStatus.SYSTEM_GENERATED,
        created_at=BASE_TIME,
    )


class FakeReplyChainMailbox(MailboxPort):
    """Mailbox fake where only some messages are unread, but thread contains all messages."""

    def __init__(
        self,
        unread_refs: list[MessageRef],
        thread_messages: dict[str, list[EphemeralEmailEnvelope]],
    ) -> None:
        self._unread_refs = unread_refs
        self._thread_messages = thread_messages

    async def search_unread(
        self, connection_id: str, query: str, page_size: int, cursor: str | None = None
    ) -> SearchPage:
        return SearchPage(
            messages=tuple(self._unread_refs),
            next_cursor=None,
            estimated_total=len(self._unread_refs),
        )

    async def get_thread(
        self, connection_id: str, thread_id: str
    ) -> tuple[EphemeralEmailEnvelope, ...]:
        return tuple(self._thread_messages.get(thread_id, []))

    async def get_message_received_at(self, connection_id: str, message_id: str) -> datetime:
        for msgs in self._thread_messages.values():
            for m in msgs:
                if m.gmail_message_id == message_id:
                    return m.received_at
        return BASE_TIME

    async def download_attachment(self, *args, **kwargs):  # type: ignore
        raise NotImplementedError


def test_reply_chain_fetches_both_read_and_unread_up_to_five() -> None:
    async def scenario() -> None:
        # Thread 1 has 7 messages: m1..m7 (m7 is latest and unread, m1..m6 are read)
        t1_messages = [
            make_email(f"m{i}", "t1", f"Subject m{i}", BASE_TIME + timedelta(minutes=i * 10))
            for i in range(1, 8)
        ]
        # Only m7 is unread in search_unread
        unread_refs = [MessageRef(message_id="m7", thread_id="t1")]
        mailbox = FakeReplyChainMailbox(
            unread_refs=unread_refs,
            thread_messages={"t1": t1_messages},
        )

        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        task_repo = InMemoryTaskRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1",
            mailbox_connection_id="mbx1",
            idempotency_key="key-1",
            max_emails=10,
            now=BASE_TIME,
        )

        canned_task = task_for("m7", "Task Thread 1", ("m3", "m4", "m5", "m6", "m7"))
        worker = DigestWorker(
            runs=runs,
            results=results,
            mailbox=mailbox,
            attachments=SafeTextAttachmentExtractor(),
            classifier=FakeRouteClassifier(),
            generator=FakePlanGenerator((canned_task,)),
            short_term=ShortTermStore(),
            task_repository=task_repo,
        )

        completed = await worker.execute(run.id, now=BASE_TIME)
        assert completed is not None
        assert completed.status is RunStatus.SUCCEEDED
        # ADR-011: Out of 7 messages, exactly latest 5 (m3..m7) were processed
        assert completed.emails_processed == 5

        # Verify processed emails saved to results
        processed = await results.list_processed_emails(run.id)
        processed_ids = [p.provider_message_id for p in processed]
        assert set(processed_ids) == {"m3", "m4", "m5", "m6", "m7"}

        # Verify tasks generated contain source_message_ids covering the 5 messages
        tasks = await task_repo.list_for_run(run.id)
        assert len(tasks) == 1
        assert set(tasks[0].task.source_message_ids) == {"m3", "m4", "m5", "m6", "m7"}
        # Primary message id points to the newest message m7
        assert tasks[0].task.gmail_message_id == "m7"

    asyncio.run(scenario())


def test_reply_chain_under_five_messages_fetches_all() -> None:
    async def scenario() -> None:
        # Thread 2 has 3 messages (m1 read, m2 read, m3 unread as latest)
        t2_messages = [
            make_email("m1", "t2", "Sub 1", BASE_TIME),
            make_email("m2", "t2", "Sub 2", BASE_TIME + timedelta(minutes=5)),
            make_email("m3", "t2", "Sub 3", BASE_TIME + timedelta(minutes=10)),
        ]
        unread_refs = [MessageRef(message_id="m3", thread_id="t2")]
        mailbox = FakeReplyChainMailbox(
            unread_refs=unread_refs,
            thread_messages={"t2": t2_messages},
        )

        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        task_repo = InMemoryTaskRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1",
            mailbox_connection_id="mbx1",
            idempotency_key="key-2",
            max_emails=10,
            now=BASE_TIME,
        )

        canned_task = task_for("m3", "Task Thread 2", ("m1", "m2", "m3"))
        worker = DigestWorker(
            runs=runs,
            results=results,
            mailbox=mailbox,
            attachments=SafeTextAttachmentExtractor(),
            classifier=FakeRouteClassifier(),
            generator=FakePlanGenerator((canned_task,)),
            short_term=ShortTermStore(),
            task_repository=task_repo,
        )

        completed = await worker.execute(run.id, now=BASE_TIME)
        assert completed is not None
        assert completed.status is RunStatus.SUCCEEDED
        # All 3 messages processed
        assert completed.emails_processed == 3

        processed = await results.list_processed_emails(run.id)
        assert len(processed) == 3

        tasks = await task_repo.list_for_run(run.id)
        assert len(tasks) == 1
        assert set(tasks[0].task.source_message_ids) == {"m1", "m2", "m3"}
        assert tasks[0].task.gmail_message_id == "m3"

    asyncio.run(scenario())


def test_thread_skipped_if_latest_message_is_already_read() -> None:
    async def scenario() -> None:
        # Thread has 3 messages: m1 (read), m2 (unread), m3 (read - latest)
        t3_messages = [
            make_email("m1", "t3", "Sub 1", BASE_TIME),
            make_email("m2", "t3", "Sub 2", BASE_TIME + timedelta(minutes=5)),
            make_email("m3", "t3", "Sub 3", BASE_TIME + timedelta(minutes=10)),
        ]
        # Only m2 is unread, but m3 is newer and already read
        unread_refs = [MessageRef(message_id="m2", thread_id="t3")]
        mailbox = FakeReplyChainMailbox(
            unread_refs=unread_refs,
            thread_messages={"t3": t3_messages},
        )

        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        task_repo = InMemoryTaskRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1",
            mailbox_connection_id="mbx1",
            idempotency_key="key-3",
            max_emails=10,
            now=BASE_TIME,
        )

        worker = DigestWorker(
            runs=runs,
            results=results,
            mailbox=mailbox,
            attachments=SafeTextAttachmentExtractor(),
            classifier=FakeRouteClassifier(),
            generator=FakePlanGenerator(),
            short_term=ShortTermStore(),
            task_repository=task_repo,
        )

        completed = await worker.execute(run.id, now=BASE_TIME)
        assert completed is not None
        assert completed.status is RunStatus.SUCCEEDED
        # Thread was skipped because latest message m3 is read
        assert completed.emails_processed == 0
        tasks = await task_repo.list_for_run(run.id)
        assert len(tasks) == 0

    asyncio.run(scenario())


def test_reply_chain_single_action_marks_entire_chain_as_action() -> None:
    async def scenario() -> None:
        # Thread has 3 messages: m1 (informational), m2 (informational), m3 (action_required)
        t4_messages = [
            make_email("m1", "t4", "Sub 1", BASE_TIME),
            make_email("m2", "t4", "Sub 2", BASE_TIME + timedelta(minutes=5)),
            make_email("m3", "t4", "Sub 3", BASE_TIME + timedelta(minutes=10)),
        ]
        unread_refs = [MessageRef(message_id="m3", thread_id="t4")]
        mailbox = FakeReplyChainMailbox(
            unread_refs=unread_refs,
            thread_messages={"t4": t4_messages},
        )

        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        task_repo = InMemoryTaskRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1",
            mailbox_connection_id="mbx1",
            idempotency_key="key-4",
            max_emails=10,
            now=BASE_TIME,
        )

        from cowork_agent.domain.target_contracts import EmailRouteDecision, ReasonCode, Route

        # Classifier gives NO_ACTION for m1 and m2, but ACTION_REQUIRED for m3
        custom_classifier = FakeRouteClassifier(
            decisions={
                "m1": EmailRouteDecision(
                    route=Route.NO_ACTION,
                    actionability=Actionability.INFORMATIONAL,
                    candidate_action_item=None,
                    email_is_sufficient=True,
                    knowledge_gaps=(),
                    retrieval_query=None,
                    expected_document_types=(),
                    reason_codes=(ReasonCode.NO_ACTION,),
                    confidence=0.9,
                ),
                "m2": EmailRouteDecision(
                    route=Route.NO_ACTION,
                    actionability=Actionability.INFORMATIONAL,
                    candidate_action_item=None,
                    email_is_sufficient=True,
                    knowledge_gaps=(),
                    retrieval_query=None,
                    expected_document_types=(),
                    reason_codes=(ReasonCode.NO_ACTION,),
                    confidence=0.9,
                ),
                "m3": EmailRouteDecision(
                    route=Route.DIRECT_PLAN,
                    actionability=Actionability.ACTION_REQUIRED,
                    candidate_action_item="Do something",
                    email_is_sufficient=True,
                    knowledge_gaps=(),
                    retrieval_query=None,
                    expected_document_types=(),
                    reason_codes=(ReasonCode.EMAIL_SELF_CONTAINED,),
                    confidence=0.9,
                ),
            }
        )

        canned_task = task_for("m3", "Unified Plan for Thread 4", ("m1", "m2", "m3"))
        worker = DigestWorker(
            runs=runs,
            results=results,
            mailbox=mailbox,
            attachments=SafeTextAttachmentExtractor(),
            classifier=custom_classifier,
            generator=FakePlanGenerator((canned_task,)),
            short_term=ShortTermStore(),
            task_repository=task_repo,
        )

        completed = await worker.execute(run.id, now=BASE_TIME)
        assert completed is not None
        assert completed.status is RunStatus.SUCCEEDED
        # All 3 emails processed
        assert completed.emails_processed == 3

        # Exactly 1 Action Plan produced combining all 3 messages
        tasks = await task_repo.list_for_run(run.id)
        assert len(tasks) == 1
        assert set(tasks[0].task.source_message_ids) == {"m1", "m2", "m3"}

    asyncio.run(scenario())
