import asyncio
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from cowork_agent.domain import Priority, RunStatus
from cowork_agent.domain.target_contracts import (
    Actionability,
    BodyFormat,
    EmailRouteDecision,
    EphemeralEmailEnvelope,
    FetchStatus,
    PlanStep,
    ReasonCode,
    Route,
    SupportingDocument,
    Task,
    ValidationStatus,
)
from cowork_agent.features.email_action_plan.schemas import ClassificationResult
from cowork_agent.features.email_action_plan.short_term import ShortTermStore
from cowork_agent.features.email_action_plan.workflow import (
    CreateDigestRun,
    DigestWorker,
    GetDigestResult,
)
from cowork_agent.identity import LOCAL_TENANT_ID
from cowork_agent.integrations.gmail.fakes import FakeMailbox, SafeTextAttachmentExtractor
from cowork_agent.integrations.llm.fakes import FakePlanGenerator, FakeRouteClassifier
from cowork_agent.persistence.repositories.local import (
    InMemoryResultRepository,
    InMemoryRunRepository,
)

NOW = datetime(2026, 8, 3, 8, tzinfo=UTC)


def email(
    message_id: str,
    thread_id: str,
    subject: str,
    *,
    attachments_present: bool = False,
    body: str = "Nội dung",
) -> EphemeralEmailEnvelope:
    return EphemeralEmailEnvelope(
        run_id="",
        tenant_id="",
        user_id="",
        gmail_message_id=message_id,
        gmail_thread_id=thread_id,
        gmail_url=f"https://mail.google.com/mail/u/0/#inbox/{message_id}",
        sender_name="Nguyễn An",
        sender_email="an@example.com",
        recipients=(),
        subject=subject,
        received_at=NOW,
        labels=(),
        normalized_body=body,
        body_format=BodyFormat.TEXT,
        attachments_present=attachments_present,
        fetch_status=FetchStatus.COMPLETE,
    )


def task_for(
    message_id: str,
    title: str,
    deadline: datetime | None = None,
    *,
    priority: Priority | None = None,
) -> Task:
    """Canned §6.6 Task standing in for one Generator call's output."""
    return Task(
        task_id=f"task_{message_id}",
        run_id="run-test",
        gmail_message_id=message_id,
        gmail_url=f"https://mail.google.com/mail/u/0/#inbox/{message_id}",
        source_message_ids=(message_id,),
        incident_key=None,
        title=title,
        request_summary="Yêu cầu cần được xử lý.",
        actionability=Actionability.ACTION_REQUIRED,
        route=Route.DIRECT_PLAN,
        priority=priority,
        deadline=deadline,
        action_plan=(PlanStep(1, "Kiểm tra yêu cầu", ()), PlanStep(2, title, ())),
        supporting_documents=(),
        missing_information=(),
        classifier_confidence=0.9,
        generation_confidence=0.9,
        validation_status=ValidationStatus.SYSTEM_GENERATED,
        created_at=NOW,
    )


#: Route Decision resolving informational emails to NO_ACTION (the legacy
#: "newsletter" classification): routing skips generation for these.
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


def test_pipeline_filters_non_action_email_and_normalizes_priority() -> None:
    async def scenario() -> None:
        messages = [email("m1", "t1", "Gửi báo cáo"), email("m2", "t2", "Newsletter")]
        tasks = (task_for("m1", "Gửi báo cáo", NOW + timedelta(hours=20)),)
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        creator = CreateDigestRun(runs)
        run = await creator.execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="request-1", now=NOW
        )
        generator = FakePlanGenerator(tasks)
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox(messages),
            SafeTextAttachmentExtractor(),
            FakeRouteClassifier({"m2": INFORMATIONAL_DECISION}),
            generator,
            ShortTermStore(),
        )
        completed = await worker.execute(run.id, now=NOW)
        assert completed is not None and completed.status is RunStatus.SUCCEEDED
        assert completed.emails_processed == 2
        assert completed.emails_actionable == 1
        assert completed.ignored_emails_count == 1
        saved = await results.list_items(run.id)
        assert len(saved) == 1 and saved[0].priority is Priority.URGENT
        processed = await results.list_processed_emails(run.id)
        assert [item.subject for item in processed] == ["Gửi báo cáo", "Newsletter"]
        # Cardinality (frozen contract rule 6): exactly one Generator call per
        # resolved non-NO_ACTION candidate — the informational email is skipped.
        assert generator.call_count == 1
        assert [
            candidate.source_message_ids for candidate in generator.received_candidates
        ] == [("m1",)]

    asyncio.run(scenario())


def test_validation_drops_generated_task_leaking_raw_email_body() -> None:
    leaked_body = "Vui lòng gửi báo cáo tài chính quý ba cho ban giám đốc trước thứ Sáu."

    async def scenario() -> None:
        messages = [email("m1", "t1", "Báo cáo quý ba", body=leaked_body)]
        leaked_task = replace(task_for("m1", "Gửi báo cáo"), request_summary=leaked_body)
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1",
            mailbox_connection_id="mbx1",
            idempotency_key="validation-leak",
            now=NOW,
        )
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox(messages),
            SafeTextAttachmentExtractor(),
            FakeRouteClassifier(),
            FakePlanGenerator((leaked_task,)),
            ShortTermStore(),
        )

        completed = await worker.execute(run.id, now=NOW)

        assert completed is not None and completed.status is RunStatus.SUCCEEDED
        assert completed.action_items_count == 0
        assert completed.emails_actionable == 0
        assert completed.ignored_emails_count == 1
        assert await results.list_items(run.id) == ()

    asyncio.run(scenario())


def test_validation_strips_bogus_citations_from_direct_plan_task() -> None:
    async def scenario() -> None:
        messages = [email("m1", "t1", "Gửi báo cáo")]
        bogus_task = replace(
            task_for("m1", "Gửi báo cáo"),
            supporting_documents=(
                SupportingDocument(
                    citation_id="cit_bogus",
                    document_id="doc_1",
                    title="Sổ tay quy trình",
                    section=None,
                    url="https://docs.example.com/doc_1",
                    relevance_score=0.9,
                ),
            ),
            action_plan=(PlanStep(1, "Kiểm tra yêu cầu", ("cit_bogus",)),),
        )
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1",
            mailbox_connection_id="mbx1",
            idempotency_key="validation-citations",
            now=NOW,
        )
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox(messages),
            SafeTextAttachmentExtractor(),
            FakeRouteClassifier(),
            FakePlanGenerator((bogus_task,)),
            ShortTermStore(),
        )

        completed = await worker.execute(run.id, now=NOW)

        assert completed is not None and completed.status is RunStatus.SUCCEEDED
        saved = await results.list_items(run.id)
        assert len(saved) == 1
        assert saved[0].title == "Gửi báo cáo"
        assert saved[0].evidence == ()

    asyncio.run(scenario())


def test_pipeline_orders_items_by_priority_before_deadline() -> None:
    async def scenario() -> None:
        messages = [
            email("medium", "tm", "Việc thường"),
            email("urgent", "tu", "Nguy cơ mất dữ liệu"),
            email("high", "th", "Build production bị chặn"),
        ]
        tasks = (
            task_for("medium", "Việc thường"),
            task_for("urgent", "Giữ dữ liệu", priority=Priority.URGENT),
            task_for("high", "Sửa build", priority=Priority.HIGH),
        )
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="priority-order"
        )
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox(messages),
            SafeTextAttachmentExtractor(),
            FakeRouteClassifier(),
            FakePlanGenerator(tasks),
            ShortTermStore(),
        )

        await worker.execute(run.id, now=NOW)
        saved = await results.list_items(run.id)

        assert [item.priority for item in saved] == [
            Priority.URGENT,
            Priority.HIGH,
            Priority.MEDIUM,
        ]

    asyncio.run(scenario())


def test_same_idempotency_key_creates_only_one_run() -> None:
    async def scenario() -> None:
        runs = InMemoryRunRepository()
        creator = CreateDigestRun(runs)
        first = await creator.execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="same"
        )
        second = await creator.execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="same"
        )
        assert second.id == first.id
        assert len(runs.runs) == 1

    asyncio.run(scenario())


def test_attachment_is_recorded_without_download_or_extraction() -> None:
    class AttachmentDownloadMustNotRunMailbox(FakeMailbox):
        def download_attachment(self, *args: object) -> object:
            raise AssertionError("ADR-003 forbids attachment download")

    class AttachmentExtractionMustNotRun:
        async def extract(self, *args: object) -> object:
            raise AssertionError("ADR-003 forbids attachment extraction")

    async def scenario() -> None:
        message = email("m1", "t1", "Duyệt tài liệu", attachments_present=True)
        tasks = (task_for("m1", "Duyệt tài liệu"),)
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        creator = CreateDigestRun(runs)
        run = await creator.execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="partial"
        )
        worker = DigestWorker(
            runs,
            results,
            AttachmentDownloadMustNotRunMailbox([message]),
            AttachmentExtractionMustNotRun(),
            FakeRouteClassifier(),
            FakePlanGenerator(tasks),
            ShortTermStore(),
        )
        completed = await worker.execute(run.id, now=NOW)
        assert completed is not None and completed.status is RunStatus.SUCCEEDED
        assert completed.action_items_count == 1
        assert completed.attachments_found == 1
        assert completed.attachments_extracted == 0
        assert completed.attachment_warnings_count == 0
        assert await results.list_warnings(run.id) == ()

    asyncio.run(scenario())


def test_result_has_explicit_empty_state_message() -> None:
    async def scenario() -> None:
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        creator = CreateDigestRun(runs)
        run = await creator.execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="empty"
        )
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox(),
            SafeTextAttachmentExtractor(),
            FakeRouteClassifier(),
            FakePlanGenerator(),
            ShortTermStore(),
        )
        await worker.execute(run.id, now=NOW)
        result = await GetDigestResult(runs, results).execute(run.id)
        assert result["message"] == "Không có công việc cần xử lý"
        assert result["actionItems"] == []

    asyncio.run(scenario())


def test_worker_exposes_only_explicitly_safe_failure_details() -> None:
    class PublicClassifierError(RuntimeError):
        error_code = "GROQ_API_ERROR"
        safe_message = "Groq từ chối yêu cầu (HTTP 400)."

    class FailingClassifier:
        async def classify(self, *args: object) -> ClassificationResult:
            raise PublicClassifierError("private diagnostic containing request data")

    async def scenario() -> None:
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="safe-failure"
        )
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox([email("m1", "t1", "Test")]),
            SafeTextAttachmentExtractor(),
            FailingClassifier(),
            FakePlanGenerator(),
            ShortTermStore(),
        )

        completed = await worker.execute(run.id, now=NOW)

        assert completed is not None and completed.status is RunStatus.FAILED
        assert completed.error_code == "GROQ_API_ERROR"
        assert completed.error_message_safe == "Groq từ chối yêu cầu (HTTP 400)."
        assert "private diagnostic" not in completed.error_message_safe

    asyncio.run(scenario())


def test_worker_keeps_unrecognized_exception_details_out_of_api_error() -> None:
    class FailingClassifier:
        async def classify(self, *args: object) -> ClassificationResult:
            raise RuntimeError("secret token and private email body")

    async def scenario() -> None:
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="private-failure"
        )
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox(),
            SafeTextAttachmentExtractor(),
            FailingClassifier(),
            FakePlanGenerator(),
            ShortTermStore(),
        )

        completed = await worker.execute(run.id, now=NOW)

        assert completed is not None and completed.status is RunStatus.FAILED
        assert completed.error_code == "RUN_PROCESSING_FAILED"
        assert "secret token" not in (completed.error_message_safe or "")
        assert "log backend" in (completed.error_message_safe or "")

    asyncio.run(scenario())


def test_max_emails_counts_only_matched_unread_messages_not_thread_history() -> None:
    async def scenario() -> None:
        messages = [
            email("m1", "shared-thread", "First unread"),
            email("m2", "shared-thread", "Second unread"),
            email("m3", "shared-thread", "Third unread"),
        ]
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        creator = CreateDigestRun(runs)
        run = await creator.execute(
            user_id="u1",
            mailbox_connection_id="mbx1",
            idempotency_key="limit-two",
            max_emails=2,
        )
        classifier = FakeRouteClassifier()
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox(messages),
            SafeTextAttachmentExtractor(),
            classifier,
            FakePlanGenerator(),
            ShortTermStore(),
        )
        completed = await worker.execute(run.id, now=NOW)
        assert completed is not None
        assert completed.emails_matched == 3
        assert completed.emails_processed == 2
        assert completed.truncated is True
        assert [item.gmail_message_id for item in classifier.received_envelopes] == [
            "m1",
            "m2",
        ]

    asyncio.run(scenario())


def test_envelopes_reaching_extraction_carry_stamped_run_identity() -> None:
    async def scenario() -> None:
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        creator = CreateDigestRun(runs)
        run = await creator.execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="stamp"
        )
        classifier = FakeRouteClassifier()
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox([email("m1", "t1", "Việc một"), email("m2", "t2", "Việc hai")]),
            SafeTextAttachmentExtractor(),
            classifier,
            FakePlanGenerator(),
            ShortTermStore(),
        )
        completed = await worker.execute(run.id, now=NOW)
        assert completed is not None and completed.status is RunStatus.SUCCEEDED
        assert classifier.received_envelopes
        for envelope in classifier.received_envelopes:
            assert envelope.run_id == run.id
            assert envelope.run_id != ""
            assert envelope.tenant_id == LOCAL_TENANT_ID
            assert envelope.user_id == "u1"
            assert envelope.attachments_processed is False

    asyncio.run(scenario())


def test_successful_run_finalizer_clears_short_term_memory() -> None:
    async def scenario() -> None:
        messages = [email("m1", "t1", "Gửi báo cáo")]
        tasks = (task_for("m1", "Gửi báo cáo"),)
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="cleanup-success"
        )
        store = ShortTermStore()
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox(messages),
            SafeTextAttachmentExtractor(),
            FakeRouteClassifier(),
            FakePlanGenerator(tasks),
            store,
        )

        completed = await worker.execute(run.id, now=NOW)

        assert completed is not None and completed.status is RunStatus.SUCCEEDED
        assert store.get(run.id) is None  # no raw body survives run completion

    asyncio.run(scenario())


def test_failed_run_finalizer_clears_short_term_memory() -> None:
    class FailingClassifier:
        def __init__(self) -> None:
            self.received_envelopes: list[EphemeralEmailEnvelope] = []

        async def classify(
            self,
            user_timezone: str,
            current_time: datetime,
            messages: Sequence[EphemeralEmailEnvelope],
        ) -> ClassificationResult:
            del user_timezone, current_time
            self.received_envelopes.extend(messages)
            raise RuntimeError("classifier backend failure")

    async def scenario() -> None:
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="cleanup-failure"
        )
        store = ShortTermStore()
        classifier = FailingClassifier()
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox([email("m1", "t1", "Việc cần làm")]),
            SafeTextAttachmentExtractor(),
            classifier,
            FakePlanGenerator(),
            store,
        )

        completed = await worker.execute(run.id, now=NOW)

        assert completed is not None and completed.status is RunStatus.FAILED
        # The raw body reached classification (via Short-Term Memory) before the failure...
        assert [item.gmail_message_id for item in classifier.received_envelopes] == ["m1"]
        # ...and the finalizer still cleared it.
        assert store.get(run.id) is None

    asyncio.run(scenario())
