import asyncio
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.fernet import Fernet

from cowork_agent.domain import ActionFreshness, Priority, RunStatus
from cowork_agent.domain.target_contracts import (
    TASK_PIPELINE_VERSION,
    Actionability,
    BodyFormat,
    EmailRouteDecision,
    EphemeralEmailEnvelope,
    ExpectedDocumentType,
    FetchStatus,
    PlanStep,
    ReasonCode,
    RetrievalStatus,
    Route,
    SemanticChunk,
    SemanticRetrievalRequest,
    SemanticRetrievalResponse,
    SupportingDocument,
    Task,
    TraceStatus,
    ValidationStatus,
)
from cowork_agent.features.email_action_plan.observability import (
    DEV_TRACE_MARKER,
    EncryptedDevTraceSink,
    InMemoryTraceSink,
)
from cowork_agent.features.email_action_plan.ports import MailboxTemporaryError
from cowork_agent.features.email_action_plan.schemas import ClassificationResult
from cowork_agent.features.email_action_plan.short_term import ShortTermStore
from cowork_agent.features.email_action_plan.workflow import (
    CreateDigestRun,
    DigestWorker,
    GetDigestResult,
)
from cowork_agent.identity import LOCAL_TENANT_ID
from cowork_agent.integrations.gmail.fakes import FakeMailbox, SafeTextAttachmentExtractor
from cowork_agent.integrations.gmail.provider import MailboxReauthRequiredError
from cowork_agent.integrations.llm.fakes import (
    FailingPlanGenerator,
    FakePlanGenerator,
    FakeRouteClassifier,
)
from cowork_agent.integrations.llm.providers.gemini import GenerationSchemaError
from cowork_agent.orchestration.local import InMemoryOutbox
from cowork_agent.persistence.repositories.local import (
    InMemoryResultRepository,
    InMemoryRunRepository,
    InMemoryTaskRepository,
)
from cowork_agent.persistence.repositories.tasks import SQLiteTaskRepository

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
        task_repository = InMemoryTaskRepository()
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
            task_repository=task_repository,
        )
        completed = await worker.execute(run.id, now=NOW)
        assert completed is not None and completed.status is RunStatus.SUCCEEDED
        assert completed.emails_processed == 2
        assert completed.emails_actionable == 1
        assert completed.ignored_emails_count == 1
        payload = await GetDigestResult(runs, results, task_repository).execute(run.id)
        saved = payload["actionItems"]
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
        task_repository = InMemoryTaskRepository()
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
            task_repository=task_repository,
        )

        completed = await worker.execute(run.id, now=NOW)

        assert completed is not None and completed.status is RunStatus.SUCCEEDED
        assert completed.action_items_count == 0
        assert completed.emails_actionable == 0
        assert completed.ignored_emails_count == 1
        assert await task_repository.list_for_run(run.id) == ()

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
        task_repository = InMemoryTaskRepository()
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
            task_repository=task_repository,
        )

        completed = await worker.execute(run.id, now=NOW)

        assert completed is not None and completed.status is RunStatus.SUCCEEDED
        payload = await GetDigestResult(runs, results, task_repository).execute(run.id)
        saved = payload["actionItems"]
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
        task_repository = InMemoryTaskRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="priority-order"
        )
        generator = FakePlanGenerator(tasks)
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox(messages),
            SafeTextAttachmentExtractor(),
            FakeRouteClassifier(),
            generator,
            ShortTermStore(),
            task_repository=task_repository,
        )

        await worker.execute(run.id, now=NOW)
        payload = await GetDigestResult(runs, results, task_repository).execute(run.id)
        saved = payload["actionItems"]

        assert [item.priority for item in saved] == [
            Priority.URGENT,
            Priority.HIGH,
            Priority.MEDIUM,
        ]
        # §15 criterion 10: exactly one generation call per actionable candidate.
        assert generator.call_count == 3

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
        task_repository = InMemoryTaskRepository()
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
            task_repository=task_repository,
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
        task_repository = InMemoryTaskRepository()
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
            task_repository=task_repository,
        )
        await worker.execute(run.id, now=NOW)
        result = await GetDigestResult(runs, results, task_repository).execute(run.id)
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
        task_repository = InMemoryTaskRepository()
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
            task_repository=task_repository,
        )

        completed = await worker.execute(run.id, now=NOW)

        assert completed is not None and completed.status is RunStatus.FAILED
        assert completed.error_code == "GROQ_API_ERROR"
        assert completed.error_message_safe == "Groq từ chối yêu cầu (HTTP 400)."
        assert "private diagnostic" not in completed.error_message_safe

    asyncio.run(scenario())


def test_worker_surfaces_actionable_gmail_reauth_error_without_private_details() -> None:
    class ReauthRequiredMailbox:
        async def search_unread(self, *args: object) -> None:
            raise MailboxReauthRequiredError("encrypted token is stale: private-token")

    async def scenario() -> None:
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        task_repository = InMemoryTaskRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="gmail-reauth"
        )
        worker = DigestWorker(
            runs,
            results,
            ReauthRequiredMailbox(),  # type: ignore[arg-type]
            SafeTextAttachmentExtractor(),
            FakeRouteClassifier(),
            FakePlanGenerator(),
            ShortTermStore(),
            task_repository=task_repository,
        )

        completed = await worker.execute(run.id, now=NOW)

        assert completed is not None and completed.status is RunStatus.FAILED
        assert completed.error_code == "GMAIL_REAUTH_REQUIRED"
        assert completed.error_message_safe == (
            "Gmail access needs to be reconnected. Reconnect Gmail and retry."
        )
        assert "private-token" not in completed.error_message_safe

    asyncio.run(scenario())


def test_worker_keeps_unrecognized_exception_details_out_of_api_error() -> None:
    class FailingClassifier:
        async def classify(self, *args: object) -> ClassificationResult:
            raise RuntimeError("secret token and private email body")

    async def scenario() -> None:
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        task_repository = InMemoryTaskRepository()
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
            task_repository=task_repository,
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
        task_repository = InMemoryTaskRepository()
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
            task_repository=task_repository,
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
        task_repository = InMemoryTaskRepository()
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
            task_repository=task_repository,
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


class _FlakyThreadMailbox(FakeMailbox):
    """Raises a transient failure for one thread after the adapter's retry
    budget would have been exhausted."""

    def __init__(self, messages: object, flaky_thread_id: str) -> None:
        super().__init__(messages)  # type: ignore[arg-type]
        self._flaky_thread_id = flaky_thread_id

    async def get_thread(self, connection_id: str, thread_id: str):  # type: ignore[override]
        if thread_id == self._flaky_thread_id:
            raise MailboxTemporaryError("transient thread failure")
        return await super().get_thread(connection_id, thread_id)


def test_transient_thread_failure_skips_thread_and_continues() -> None:
    async def scenario() -> None:
        messages = [email("m1", "t1", "Thread một"), email("m2", "t2", "Thread hai")]
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        task_repository = InMemoryTaskRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="flaky", now=NOW
        )
        worker = DigestWorker(
            runs,
            results,
            _FlakyThreadMailbox(messages, "t2"),
            SafeTextAttachmentExtractor(),
            FakeRouteClassifier(),
            FakePlanGenerator((task_for("m1", "Thread một"),)),
            ShortTermStore(),
            task_repository=task_repository,
        )

        completed = await worker.execute(run.id, now=NOW)

        # T5.4: one thread's transient failure skips that thread; the run
        # continues with the healthy one and reports PARTIAL, not SUCCEEDED.
        assert completed is not None and completed.status is RunStatus.PARTIAL
        assert completed.emails_processed == 1
        stored = await task_repository.list_for_run(run.id)
        assert len(stored) == 1
        assert stored[0].task.gmail_message_id == "m1"

    asyncio.run(scenario())


def test_successful_run_finalizer_clears_short_term_memory() -> None:
    async def scenario() -> None:
        messages = [email("m1", "t1", "Gửi báo cáo")]
        tasks = (task_for("m1", "Gửi báo cáo"),)
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        task_repository = InMemoryTaskRepository()
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
            task_repository=task_repository,
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
        task_repository = InMemoryTaskRepository()
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
            task_repository=task_repository,
        )

        completed = await worker.execute(run.id, now=NOW)

        assert completed is not None and completed.status is RunStatus.FAILED
        # The raw body reached classification (via Short-Term Memory) before the failure...
        assert [item.gmail_message_id for item in classifier.received_envelopes] == ["m1"]
        # ...and the finalizer still cleared it.
        assert store.get(run.id) is None

    asyncio.run(scenario())

#: Route Decision resolving to RETRIEVE_RAG with explicit gaps and query.
RAG_DECISION = EmailRouteDecision(
    actionability=Actionability.ACTION_REQUIRED,
    route=Route.RETRIEVE_RAG,
    candidate_action_item=None,
    email_is_sufficient=False,
    knowledge_gaps=("quy trình nghỉ phép",),
    retrieval_query="quy trình nghỉ phép",
    expected_document_types=(),
    reason_codes=(ReasonCode.DOMAIN_KNOWLEDGE_REQUIRED,),
    confidence=0.9,
)

#: Guard-forced RETRIEVE_RAG without any query or gaps (expected document
#: type triggers the Policy Guard): retrieval must be skipped, not guessed.
GUARDED_RAG_DECISION = EmailRouteDecision(
    actionability=Actionability.ACTION_REQUIRED,
    route=Route.RETRIEVE_RAG,
    candidate_action_item=None,
    email_is_sufficient=True,
    knowledge_gaps=(),
    retrieval_query=None,
    expected_document_types=(ExpectedDocumentType.PROCEDURE,),
    reason_codes=(ReasonCode.EMAIL_SELF_CONTAINED,),
    confidence=0.9,
)


class RecordingMemory:
    """SemanticMemoryPort fake: records requests, replays one canned response."""

    def __init__(self, *, fail_times: int = 0) -> None:
        self.requests: list[SemanticRetrievalRequest] = []
        self.fail_times = fail_times
        self.response = SemanticRetrievalResponse(
            query_id="q_test",
            tenant_id=LOCAL_TENANT_ID,
            chunks=(),
            retrieval_status=RetrievalStatus.NO_RESULTS,
            latency_ms=1,
        )

    async def retrieve(self, request: SemanticRetrievalRequest) -> SemanticRetrievalResponse:
        self.requests.append(request)
        if len(self.requests) <= self.fail_times:
            raise TimeoutError("simulated retrieval failure")
        return self.response


def test_retrieve_rag_candidate_retrieves_once_and_feeds_generator() -> None:
    async def scenario() -> None:
        messages = [email("m1", "t1", "Xin nghỉ phép")]
        memory = RecordingMemory()
        memory.response = SemanticRetrievalResponse(
            query_id="q_test",
            tenant_id=LOCAL_TENANT_ID,
            chunks=(
                SemanticChunk(
                    chunk_id="cit_hr_1",
                    document_id="doc_hr_1",
                    document_title="Sổ tay quy trình nội bộ",
                    section=None,
                    text="Nội dung tri thức công ty.",
                    source_url="https://docs.example.com",
                    document_version=None,
                    relevance_score=0.9,
                    rerank_score=None,
                ),
            ),
            retrieval_status=RetrievalStatus.SUCCESS,
            latency_ms=1,
        )
        generator = FakePlanGenerator((task_for("m1", "Xin nghỉ phép"),))
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        task_repository = InMemoryTaskRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="rag-1", now=NOW
        )
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox(messages),
            SafeTextAttachmentExtractor(),
            FakeRouteClassifier({"m1": RAG_DECISION}),
            generator,
            ShortTermStore(),
            task_repository=task_repository,
            semantic_memory=memory,
        )

        completed = await worker.execute(run.id, now=NOW)

        assert completed is not None and completed.status is RunStatus.SUCCEEDED
        assert completed.action_items_count == 1
        assert len(memory.requests) == 1
        request = memory.requests[0]
        assert request.run_id == run.id
        assert request.tenant_id == LOCAL_TENANT_ID
        assert request.user_id == "u1"
        assert request.query == "quy trình nghỉ phép"
        assert request.knowledge_gaps == ("quy trình nghỉ phép",)
        assert request.filters.tenant_scope == LOCAL_TENANT_ID
        assert generator.received_retrievals == (memory.response,)
        # FR-11 boundary: retrieval returned chunks, so no missing-context
        # note may be injected.
        stored = await task_repository.list_for_run(run.id)
        assert len(stored) == 1
        assert stored[0].task.missing_information == ()

    asyncio.run(scenario())


def test_direct_plan_candidate_makes_zero_retrieval_calls() -> None:
    async def scenario() -> None:
        messages = [email("m1", "t1", "Gửi báo cáo")]
        memory = RecordingMemory()
        generator = FakePlanGenerator((task_for("m1", "Gửi báo cáo"),))
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        task_repository = InMemoryTaskRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="rag-2", now=NOW
        )
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox(messages),
            SafeTextAttachmentExtractor(),
            FakeRouteClassifier(),
            generator,
            ShortTermStore(),
            task_repository=task_repository,
            semantic_memory=memory,
        )

        completed = await worker.execute(run.id, now=NOW)

        # V1-M3 exit criterion: DIRECT_PLAN never touches Semantic Memory.
        assert completed is not None and completed.status is RunStatus.SUCCEEDED
        assert memory.requests == []
        assert generator.received_retrievals == (None,)

    asyncio.run(scenario())


def test_retrieval_failure_retries_once_then_degrades_to_structured_empty() -> None:
    async def scenario() -> None:
        messages = [email("m1", "t1", "Xin nghỉ phép")]
        memory = RecordingMemory(fail_times=2)
        generator = FakePlanGenerator((task_for("m1", "Xin nghỉ phép"),))
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        task_repository = InMemoryTaskRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="rag-3", now=NOW
        )
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox(messages),
            SafeTextAttachmentExtractor(),
            FakeRouteClassifier({"m1": RAG_DECISION}),
            generator,
            ShortTermStore(),
            task_repository=task_repository,
            semantic_memory=memory,
        )

        completed = await worker.execute(run.id, now=NOW)

        # §12.3: retry once, then structured empty -> partial generation.
        assert completed is not None and completed.status is RunStatus.SUCCEEDED
        assert len(memory.requests) == 2
        degraded = generator.received_retrievals[0]
        assert degraded is not None
        assert degraded.chunks == ()
        assert degraded.retrieval_status is RetrievalStatus.NO_RESULTS
        # §12.3 "expose missing context": the degraded plan is persisted with
        # a missing-information warning even when nothing was cited. The
        # literal pins the user-facing wording intentionally.
        stored = await task_repository.list_for_run(run.id)
        assert len(stored) == 1
        assert stored[0].task.missing_information == (
            "Kế hoạch được tạo mà không có ngữ cảnh công ty.",
        )

    asyncio.run(scenario())


def test_genuine_empty_retrieval_marks_missing_info_without_degraded_marker() -> None:
    async def scenario() -> None:
        messages = [email("m1", "t1", "Xin nghỉ phép")]
        memory = RecordingMemory()  # healthy port, canned NO_RESULTS response
        sink = InMemoryTraceSink()
        generator = FakePlanGenerator((task_for("m1", "Xin nghỉ phép"),))
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        task_repository = InMemoryTaskRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="rag-empty", now=NOW
        )
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox(messages),
            SafeTextAttachmentExtractor(),
            FakeRouteClassifier({"m1": RAG_DECISION}),
            generator,
            ShortTermStore(),
            task_repository=task_repository,
            semantic_memory=memory,
            trace_sink=sink,
        )

        completed = await worker.execute(run.id, now=NOW)

        assert completed is not None and completed.status is RunStatus.SUCCEEDED
        # FR-11: "no useful result" must expose the missing context. The
        # literal pins the user-facing wording intentionally.
        stored = await task_repository.list_for_run(run.id)
        assert len(stored) == 1
        assert stored[0].task.missing_information == (
            "Kế hoạch được tạo mà không có ngữ cảnh công ty.",
        )
        # FR-16: a genuine empty result is NOT the degraded-fallback marker.
        candidate = [e for e in sink.events if e.event_name == "task_candidate"][0]
        assert candidate.generation_status is None
        assert candidate.retrieval_status == RetrievalStatus.NO_RESULTS.value

    asyncio.run(scenario())


def test_guard_forced_retrieval_without_query_or_gaps_skips_port() -> None:
    async def scenario() -> None:
        messages = [email("m1", "t1", "Xin nghỉ phép")]
        memory = RecordingMemory()
        sink = InMemoryTraceSink()
        generator = FakePlanGenerator((task_for("m1", "Xin nghỉ phép"),))
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        task_repository = InMemoryTaskRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="rag-4", now=NOW
        )
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox(messages),
            SafeTextAttachmentExtractor(),
            FakeRouteClassifier({"m1": GUARDED_RAG_DECISION}),
            generator,
            ShortTermStore(),
            task_repository=task_repository,
            semantic_memory=memory,
            trace_sink=sink,
        )

        completed = await worker.execute(run.id, now=NOW)

        assert completed is not None and completed.status is RunStatus.SUCCEEDED
        assert memory.requests == []
        empty = generator.received_retrievals[0]
        assert empty is not None and empty.chunks == ()
        assert empty.retrieval_status is RetrievalStatus.NO_RESULTS
        # FR-11: the route demanded company knowledge but none was available,
        # so the missing context must be listed. The literal pins the
        # user-facing wording intentionally.
        stored = await task_repository.list_for_run(run.id)
        assert len(stored) == 1
        assert stored[0].task.missing_information == (
            "Kế hoạch được tạo mà không có ngữ cảnh công ty.",
        )
        # §14: skipped retrievals are reported as "skipped", not no_results,
        # and carry no degraded-fallback marker.
        candidate = [e for e in sink.events if e.event_name == "task_candidate"][0]
        assert candidate.retrieval_status == "skipped"
        assert candidate.generation_status is None

    asyncio.run(scenario())


def test_generation_failure_fails_run_with_safe_error() -> None:
    async def scenario() -> None:
        messages = [email("m1", "t1", "Gửi báo cáo", body="Nội dung email tuyệt mật.")]
        generator = FailingPlanGenerator(
            GenerationSchemaError("secret internal detail: stack trace here")
        )
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        task_repository = InMemoryTaskRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="fail-gen", now=NOW
        )
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox(messages),
            SafeTextAttachmentExtractor(),
            FakeRouteClassifier(),
            generator,
            ShortTermStore(),
            task_repository=task_repository,
        )

        completed = await worker.execute(run.id, now=NOW)

        # §12.4: generation failure after repair -> run FAILED with the
        # adapter's safe error, never internal detail or email content.
        assert completed is not None and completed.status is RunStatus.FAILED
        assert completed.error_code == "GENERATION_SCHEMA_ERROR"
        assert completed.error_message_safe is not None
        assert "secret internal detail" not in completed.error_message_safe
        assert "Nội dung email tuyệt mật." not in completed.error_message_safe
        # §15 criterion 10: generation was attempted exactly once for the
        # single candidate — a terminal adapter error aborts the run without
        # any worker-level re-invocation (§12.4 repair lives in the adapter).
        assert generator.call_count == 1
        assert await task_repository.list_for_run(run.id) == ()

    asyncio.run(scenario())


def test_terminal_runs_append_completion_events_to_outbox() -> None:
    async def scenario() -> None:
        messages = [email("m1", "t1", "Gửi báo cáo")]
        outbox = InMemoryOutbox()
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        task_repository = InMemoryTaskRepository()
        creator = CreateDigestRun(runs)
        ok_run = await creator.execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="ok", now=NOW
        )
        failed_run = await creator.execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="bad", now=NOW
        )

        def worker(generator: FakePlanGenerator | FailingPlanGenerator) -> DigestWorker:
            return DigestWorker(
                runs,
                results,
                FakeMailbox(messages),
                SafeTextAttachmentExtractor(),
                FakeRouteClassifier(),
                generator,
                ShortTermStore(),
                task_repository=task_repository,
                completion_outbox=outbox,
            )

        await worker(FakePlanGenerator((task_for("m1", "Gửi báo cáo"),))).execute(
            ok_run.id, now=NOW
        )
        await worker(
            FailingPlanGenerator(GenerationSchemaError("boom"))
        ).execute(failed_run.id, now=NOW)

        # T5.3: every terminal run yields exactly one metadata-only
        # lifecycle event, on success and on failure alike.
        pending = await outbox.pending()
        assert [(event.run_id, event.status) for event in pending] == [
            (ok_run.id, RunStatus.SUCCEEDED),
            (failed_run.id, RunStatus.FAILED),
        ]
        assert all(event.user_id == "u1" for event in pending)
        assert all(event.occurred_at == NOW for event in pending)

    asyncio.run(scenario())


def test_outbox_outage_never_masks_the_run_result() -> None:
    class BrokenOutbox:
        async def add(self, event: object) -> None:
            raise RuntimeError("event store down")

    async def scenario() -> None:
        messages = [email("m1", "t1", "Gửi báo cáo")]
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="outage", now=NOW
        )
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox(messages),
            SafeTextAttachmentExtractor(),
            FakeRouteClassifier(),
            FakePlanGenerator((task_for("m1", "Gửi báo cáo"),)),
            ShortTermStore(),
            task_repository=InMemoryTaskRepository(),
            completion_outbox=BrokenOutbox(),  # type: ignore[arg-type]
        )

        # T5.3 availability property: lifecycle-event persistence is
        # best-effort and must never mask the terminal run result.
        completed = await worker.execute(run.id, now=NOW)
        assert completed is not None and completed.status is RunStatus.SUCCEEDED

    asyncio.run(scenario())


def test_validated_tasks_are_persisted_with_identity_and_pipeline_version() -> None:
    async def scenario() -> None:
        messages = [email("m1", "t1", "Gửi báo cáo"), email("m2", "t2", "Newsletter")]
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        task_repository = InMemoryTaskRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="persist-1", now=NOW
        )
        tasks = (task_for("m1", "Gửi báo cáo"),)
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox(messages),
            SafeTextAttachmentExtractor(),
            FakeRouteClassifier({"m2": INFORMATIONAL_DECISION}),
            FakePlanGenerator(tasks),
            ShortTermStore(),
            task_repository=task_repository,
        )

        completed = await worker.execute(run.id, now=NOW)

        assert completed is not None and completed.status is RunStatus.SUCCEEDED
        stored = await task_repository.list_for_run(run.id)
        assert len(stored) == 1
        assert stored[0].task.task_id == "task_m1"
        assert stored[0].task.run_id == run.id
        assert stored[0].pointer.mailbox_connection_id == "mbx1"
        assert stored[0].pointer.provider_thread_id == "t1"
        assert stored[0].freshness is ActionFreshness.NEW
        (tenant_id, user_id, message_id, pipeline_version), = task_repository.tasks
        assert tenant_id == LOCAL_TENANT_ID
        assert user_id == "u1"
        assert message_id == "m1"
        assert pipeline_version == TASK_PIPELINE_VERSION

    asyncio.run(scenario())


def test_persisted_tasks_are_idempotent_across_replayed_runs() -> None:
    async def scenario() -> None:
        messages = [email("m1", "t1", "Gửi báo cáo")]
        task_repository = InMemoryTaskRepository()
        for key in ("persist-2a", "persist-2b"):
            runs, results = InMemoryRunRepository(), InMemoryResultRepository()
            run = await CreateDigestRun(runs).execute(
                user_id="u1", mailbox_connection_id="mbx1", idempotency_key=key, now=NOW
            )
            worker = DigestWorker(
                runs,
                results,
                FakeMailbox(messages),
                SafeTextAttachmentExtractor(),
                FakeRouteClassifier(),
                FakePlanGenerator((task_for("m1", "Gửi báo cáo"),)),
                ShortTermStore(),
                task_repository=task_repository,
            )
            completed = await worker.execute(run.id, now=NOW)
            assert completed is not None and completed.status is RunStatus.SUCCEEDED

        # Same tenant:user:gmail_message_id:pipeline_version — one durable row.
        assert len(task_repository.tasks) == 1

    asyncio.run(scenario())


def test_validation_dropped_task_is_never_persisted() -> None:
    leaked_body = "Vui lòng gửi báo cáo tài chính quý ba cho ban giám đốc trước thứ Sáu."

    async def scenario() -> None:
        messages = [email("m1", "t1", "Báo cáo quý ba", body=leaked_body)]
        leaked_task = replace(task_for("m1", "Gửi báo cáo"), request_summary=leaked_body)
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        task_repository = InMemoryTaskRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="persist-3", now=NOW
        )
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox(messages),
            SafeTextAttachmentExtractor(),
            FakeRouteClassifier(),
            FakePlanGenerator((leaked_task,)),
            ShortTermStore(),
            task_repository=task_repository,
        )

        completed = await worker.execute(run.id, now=NOW)

        assert completed is not None and completed.status is RunStatus.SUCCEEDED
        assert task_repository.tasks == {}
        assert await task_repository.list_for_run(run.id) == ()

    asyncio.run(scenario())


def test_sqlite_persisted_tasks_are_body_free(tmp_path: Path) -> None:
    raw_body = "MẬT-KHẨU-NỘI-DUNG: kế hoạch ngân sách quý bốn chưa công bố."

    async def scenario() -> None:
        messages = [email("m1", "t1", "Gửi báo cáo", body=raw_body)]
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        task_repository = SQLiteTaskRepository(tmp_path / "tasks.db")
        await task_repository.initialize()
        run = await CreateDigestRun(runs).execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="persist-4", now=NOW
        )
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox(messages),
            SafeTextAttachmentExtractor(),
            FakeRouteClassifier(),
            FakePlanGenerator((task_for("m1", "Gửi báo cáo"),)),
            ShortTermStore(),
            task_repository=task_repository,
        )

        completed = await worker.execute(run.id, now=NOW)

        assert completed is not None and completed.status is RunStatus.SUCCEEDED
        assert len(await task_repository.list_for_run(run.id)) == 1

    asyncio.run(scenario())
    database = sqlite3.connect(tmp_path / "tasks.db")
    dump = "\n".join(database.iterdump())
    database.close()
    assert raw_body not in dump


def test_telemetry_emits_metadata_only_candidate_and_run_events() -> None:
    secret_body = "Nội dung email tuyệt đối không được xuất hiện trong telemetry."

    async def scenario() -> None:
        messages = [email("m1", "t1", "Gửi báo cáo", body=secret_body)]
        sink = InMemoryTraceSink()
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="telemetry-1", now=NOW
        )
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox(messages),
            SafeTextAttachmentExtractor(),
            FakeRouteClassifier(),
            FakePlanGenerator((task_for("m1", "Gửi báo cáo"),)),
            ShortTermStore(),
            InMemoryTaskRepository(),
            trace_sink=sink,
        )

        completed = await worker.execute(run.id, now=NOW)

        assert completed is not None and completed.status is RunStatus.SUCCEEDED
        candidate_events = [e for e in sink.events if e.event_name == "task_candidate"]
        run_events = [e for e in sink.events if e.event_name == "digest_run"]
        assert len(candidate_events) == 1 and len(run_events) == 1
        candidate = candidate_events[0]
        assert candidate.status is TraceStatus.SUCCESS
        assert candidate.route is Route.DIRECT_PLAN
        assert candidate.gmail_message_id == "m1"
        assert candidate.classifier_confidence == 0.9
        assert candidate.retrieval_status is None  # DIRECT_PLAN: zero retrieval
        assert candidate.validation_status == ValidationStatus.SYSTEM_GENERATED.value
        assert candidate.latency_ms.generation is not None
        outcome = run_events[0]
        assert outcome.status is TraceStatus.SUCCESS
        assert outcome.generation_status is None  # no error/fallback marker
        assert outcome.latency_ms.email is not None
        assert outcome.latency_ms.classifier is not None
        assert outcome.latency_ms.persistence is not None
        for event in sink.events:
            assert secret_body not in json.dumps(event.to_dict(), ensure_ascii=False)

    asyncio.run(scenario())


def test_telemetry_marks_failed_run_with_error_code_only() -> None:
    async def scenario() -> None:
        messages = [email("m1", "t1", "Gửi báo cáo", body="Nội dung email riêng tư.")]
        sink = InMemoryTraceSink()
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="telemetry-2", now=NOW
        )
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox(messages),
            SafeTextAttachmentExtractor(),
            FakeRouteClassifier(),
            FailingPlanGenerator(GenerationSchemaError("secret detail")),
            ShortTermStore(),
            InMemoryTaskRepository(),
            trace_sink=sink,
        )

        completed = await worker.execute(run.id, now=NOW)

        assert completed is not None and completed.status is RunStatus.FAILED
        outcome = [e for e in sink.events if e.event_name == "digest_run"][0]
        assert outcome.status is TraceStatus.FAILED
        assert outcome.generation_status == "GENERATION_SCHEMA_ERROR"
        for event in sink.events:
            assert "secret detail" not in json.dumps(event.to_dict(), ensure_ascii=False)
            assert "Nội dung email riêng tư." not in json.dumps(
                event.to_dict(), ensure_ascii=False
            )

    asyncio.run(scenario())


def test_dev_trace_writes_encrypted_full_content_with_marker(tmp_path: Path) -> None:
    full_body = "Toàn văn email cần truy vết trong giai đoạn phát triển."

    async def scenario() -> None:
        messages = [email("m1", "t1", "Gửi báo cáo", body=full_body)]
        dev_trace = EncryptedDevTraceSink(
            tmp_path / "dev_trace.jsonl.enc",
            Fernet.generate_key().decode(),
            enabled=True,
            ttl_seconds=3600,
            environ={"APP_ENV": "development"},
        )
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="devtrace-1", now=NOW
        )
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox(messages),
            SafeTextAttachmentExtractor(),
            FakeRouteClassifier(),
            FakePlanGenerator((task_for("m1", "Gửi báo cáo"),)),
            ShortTermStore(),
            InMemoryTaskRepository(),
            dev_trace=dev_trace,
        )

        completed = await worker.execute(run.id, now=NOW)

        assert completed is not None and completed.status is RunStatus.SUCCEEDED
        raw = (tmp_path / "dev_trace.jsonl.enc").read_text(encoding="utf-8")
        assert full_body not in raw  # encrypted at rest
        records = dev_trace.read()
        assert {str(record["kind"]) for record in records} == {
            "classifier_input",
            "generated_output",
        }
        for record in records:
            assert record["marker"] == DEV_TRACE_MARKER
            assert record["run_id"] == run.id
        classifier_input = [r for r in records if r["kind"] == "classifier_input"][0]
        assert full_body in json.dumps(classifier_input["payload"], ensure_ascii=False)

    asyncio.run(scenario())


def test_telemetry_marks_degraded_retrieval_fallback() -> None:
    async def scenario() -> None:
        messages = [email("m1", "t1", "Xin nghỉ phép")]
        sink = InMemoryTraceSink()
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="telemetry-3", now=NOW
        )
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox(messages),
            SafeTextAttachmentExtractor(),
            FakeRouteClassifier({"m1": RAG_DECISION}),
            FakePlanGenerator((task_for("m1", "Xin nghỉ phép"),)),
            ShortTermStore(),
            InMemoryTaskRepository(),
            semantic_memory=RecordingMemory(fail_times=2),
            trace_sink=sink,
        )

        completed = await worker.execute(run.id, now=NOW)

        # §12.3: both attempts failed → structured empty retrieval; telemetry
        # must expose the fallback, not just a silent empty result.
        assert completed is not None and completed.status is RunStatus.SUCCEEDED
        candidate = [e for e in sink.events if e.event_name == "task_candidate"][0]
        assert candidate.generation_status == "RETRIEVAL_DEGRADED"
        assert candidate.rag_result_count == 0
        assert candidate.retrieval_status == RetrievalStatus.NO_RESULTS.value

    asyncio.run(scenario())


def test_completion_timestamp_is_taken_after_the_work_not_at_claim_time() -> None:
    """Regression: run.completed_at reused the claim clock, so every durable
    record showed a zero-second run and hid the latency the SLO measures."""

    class SlowMailbox(FakeMailbox):
        """Spends real time, so a claim-time stamp is measurably too early."""

        async def get_thread(
            self, connection_id: str, thread_id: str
        ) -> Sequence[EphemeralEmailEnvelope]:
            await asyncio.sleep(0.05)
            return await super().get_thread(connection_id, thread_id)

    async def scenario() -> None:
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="request-1"
        )
        worker = DigestWorker(
            runs,
            results,
            SlowMailbox([email("m1", "t1", "Newsletter")]),
            SafeTextAttachmentExtractor(),
            FakeRouteClassifier({"m1": INFORMATIONAL_DECISION}),
            FakePlanGenerator(()),
            ShortTermStore(),
            task_repository=InMemoryTaskRepository(),
        )
        completed = await worker.execute(run.id)
        assert completed is not None and completed.completed_at is not None
        assert completed.started_at is not None
        elapsed = (completed.completed_at - completed.started_at).total_seconds()
        assert elapsed >= 0.05, f"completed_at stamped at claim time (elapsed {elapsed}s)"
        # The injected-clock path stays deterministic for every other test.
        pinned = await CreateDigestRun(runs).execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="request-2", now=NOW
        )
        assert (await worker.execute(pinned.id, now=NOW)).completed_at == NOW

    asyncio.run(scenario())
