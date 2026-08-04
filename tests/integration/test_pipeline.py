import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from mail_todo.application.contracts import EmailExtraction, ExtractedAction, ExtractionBatch
from mail_todo.application.services import CreateDigestRun, DigestWorker, GetDigestResult
from mail_todo.domain import (
    ActionPlanStep,
    AttachmentRef,
    Confidence,
    DeadlineSource,
    EmailEnvelope,
    EvidenceRef,
    Priority,
    RunStatus,
)
from mail_todo.infrastructure import (
    FakeActionExtractor,
    FakeMailbox,
    InMemoryOutbox,
    InMemoryQueue,
    InMemoryResultRepository,
    InMemoryRunRepository,
    SafeTextAttachmentExtractor,
)

NOW = datetime(2026, 8, 3, 8, tzinfo=UTC)


def email(
    message_id: str, thread_id: str, subject: str, attachments: tuple[AttachmentRef, ...] = ()
) -> EmailEnvelope:
    return EmailEnvelope(
        message_id,
        thread_id,
        f"https://mail.google.com/mail/u/0/#inbox/{message_id}",
        subject,
        "Nguyễn An",
        "an@example.com",
        NOW,
        NOW,
        "Nội dung",
        attachments,
    )


def action(message_id: str, title: str, deadline: datetime | None = None) -> ExtractedAction:
    return ExtractedAction(
        message_id,
        title,
        "Yêu cầu cần được xử lý.",
        deadline,
        deadline.isoformat() if deadline else None,
        DeadlineSource.EXPLICIT if deadline else DeadlineSource.NONE,
        (ActionPlanStep(1, "Kiểm tra yêu cầu", "email"), ActionPlanStep(2, title, "email")),
        (EvidenceRef("email_body", None, None, "Vui lòng thực hiện yêu cầu"),),
        Confidence.HIGH,
    )


def test_pipeline_filters_non_action_email_and_normalizes_priority() -> None:
    async def scenario() -> None:
        messages = [email("m1", "t1", "Gửi báo cáo"), email("m2", "t2", "Newsletter")]
        batch = ExtractionBatch(
            (
                EmailExtraction(
                    "m1",
                    "actionable",
                    "Có yêu cầu",
                    (action("m1", "Gửi báo cáo", NOW + timedelta(hours=20)),),
                ),
                EmailExtraction("m2", "newsletter", "Nội dung marketing", ()),
            )
        )
        runs, results, queue, outbox = (
            InMemoryRunRepository(),
            InMemoryResultRepository(),
            InMemoryQueue(),
            InMemoryOutbox(),
        )
        creator = CreateDigestRun(runs, queue)
        run = await creator.execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="request-1", now=NOW
        )
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox(messages),
            SafeTextAttachmentExtractor(),
            FakeActionExtractor(batch),
            outbox,
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
        assert len(await outbox.pending()) == 1

    asyncio.run(scenario())


def test_pipeline_orders_items_by_priority_before_deadline() -> None:
    async def scenario() -> None:
        messages = [
            email("medium", "tm", "Việc thường"),
            email("urgent", "tu", "Nguy cơ mất dữ liệu"),
            email("high", "th", "Build production bị chặn"),
        ]
        batch = ExtractionBatch(
            (
                EmailExtraction(
                    "medium", "actionable", "Cần xử lý", (action("medium", "Việc thường"),)
                ),
                EmailExtraction(
                    "urgent",
                    "actionable",
                    "Nguy cơ mất dữ liệu",
                    (replace(action("urgent", "Giữ dữ liệu"), impact="data_loss_risk"),),
                ),
                EmailExtraction(
                    "high",
                    "actionable",
                    "Production bị chặn",
                    (replace(action("high", "Sửa build"), impact="production_blocked"),),
                ),
            )
        )
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        run = await CreateDigestRun(runs, InMemoryQueue()).execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="priority-order"
        )
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox(messages),
            SafeTextAttachmentExtractor(),
            FakeActionExtractor(batch),
            InMemoryOutbox(),
        )

        await worker.execute(run.id, now=NOW)
        saved = await results.list_items(run.id)

        assert [item.priority for item in saved] == [
            Priority.URGENT,
            Priority.HIGH,
            Priority.MEDIUM,
        ]

    asyncio.run(scenario())


def test_same_idempotency_key_creates_and_enqueues_only_one_run() -> None:
    async def scenario() -> None:
        runs, queue = InMemoryRunRepository(), InMemoryQueue()
        creator = CreateDigestRun(runs, queue)
        first = await creator.execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="same"
        )
        second = await creator.execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="same"
        )
        assert first.id == second.id
        assert queue.run_ids == [first.id]

    asyncio.run(scenario())


def test_attachment_failure_makes_partial_run_but_preserves_actions() -> None:
    async def scenario() -> None:
        ref = AttachmentRef("a1", "spec.pdf", "application/pdf", 100)
        message = email("m1", "t1", "Duyệt tài liệu", (ref,))
        batch = ExtractionBatch(
            (EmailExtraction("m1", "actionable", "Có yêu cầu", (action("m1", "Duyệt tài liệu"),)),)
        )
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        creator = CreateDigestRun(runs, InMemoryQueue())
        run = await creator.execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="partial"
        )
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox([message], {"a1": b"%PDF-test"}),
            SafeTextAttachmentExtractor(),
            FakeActionExtractor(batch),
            InMemoryOutbox(),
        )
        completed = await worker.execute(run.id, now=NOW)
        assert completed is not None and completed.status is RunStatus.PARTIAL
        assert completed.action_items_count == 1
        warnings = await results.list_warnings(run.id)
        assert warnings[0].code == "ATTACHMENT_UNSUPPORTED"

    asyncio.run(scenario())


def test_result_has_explicit_empty_state_message() -> None:
    async def scenario() -> None:
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        creator = CreateDigestRun(runs, InMemoryQueue())
        run = await creator.execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="empty"
        )
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox(),
            SafeTextAttachmentExtractor(),
            FakeActionExtractor(ExtractionBatch(())),
            InMemoryOutbox(),
        )
        await worker.execute(run.id, now=NOW)
        result = await GetDigestResult(runs, results).execute(run.id)
        assert result["message"] == "Không có công việc cần xử lý"
        assert result["actionItems"] == []

    asyncio.run(scenario())


def test_worker_exposes_only_explicitly_safe_failure_details() -> None:
    class PublicExtractorError(RuntimeError):
        error_code = "GROQ_API_ERROR"
        safe_message = "Groq từ chối yêu cầu (HTTP 400)."

    class FailingExtractor:
        async def extract(self, *args: object) -> ExtractionBatch:
            raise PublicExtractorError("private diagnostic containing request data")

    async def scenario() -> None:
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        run = await CreateDigestRun(runs, InMemoryQueue()).execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="safe-failure"
        )
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox([email("m1", "t1", "Test")]),
            SafeTextAttachmentExtractor(),
            FailingExtractor(),
            InMemoryOutbox(),
        )

        completed = await worker.execute(run.id, now=NOW)

        assert completed is not None and completed.status is RunStatus.FAILED
        assert completed.error_code == "GROQ_API_ERROR"
        assert completed.error_message_safe == "Groq từ chối yêu cầu (HTTP 400)."
        assert "private diagnostic" not in completed.error_message_safe

    asyncio.run(scenario())


def test_worker_keeps_unrecognized_exception_details_out_of_api_error() -> None:
    class FailingExtractor:
        async def extract(self, *args: object) -> ExtractionBatch:
            raise RuntimeError("secret token and private email body")

    async def scenario() -> None:
        runs, results = InMemoryRunRepository(), InMemoryResultRepository()
        run = await CreateDigestRun(runs, InMemoryQueue()).execute(
            user_id="u1", mailbox_connection_id="mbx1", idempotency_key="private-failure"
        )
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox(),
            SafeTextAttachmentExtractor(),
            FailingExtractor(),
            InMemoryOutbox(),
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
        creator = CreateDigestRun(runs, InMemoryQueue())
        run = await creator.execute(
            user_id="u1",
            mailbox_connection_id="mbx1",
            idempotency_key="limit-two",
            max_emails=2,
        )
        extractor = FakeActionExtractor(ExtractionBatch(()))
        worker = DigestWorker(
            runs,
            results,
            FakeMailbox(messages),
            SafeTextAttachmentExtractor(),
            extractor,
            InMemoryOutbox(),
        )
        completed = await worker.execute(run.id, now=NOW)
        assert completed is not None
        assert completed.emails_matched == 3
        assert completed.emails_processed == 2
        assert completed.truncated is True
        assert [item.provider_message_id for item in extractor.received_threads[0].messages] == [
            "m1",
            "m2",
        ]

    asyncio.run(scenario())
