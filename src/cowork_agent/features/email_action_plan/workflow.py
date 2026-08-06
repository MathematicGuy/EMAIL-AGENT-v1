"""Run creation, execution and result use cases."""

import logging
from datetime import UTC, datetime
from uuid import uuid4

from cowork_agent.domain import (
    ActionFreshness,
    ActionItem,
    DigestCompletedEvent,
    DigestRun,
    EmailEnvelope,
    ProcessedEmail,
    RunStatus,
    RunTrigger,
)
from cowork_agent.features.email_action_plan.policies import (
    action_fingerprint,
    calculate_priority,
    normalize_query,
    validate_max_emails,
)

from .ports import (
    TERMINAL_STATUSES,
    ActionExtractorPort,
    AttachmentExtractorPort,
    CompletionOutboxPort,
    MailboxPort,
    QueuePort,
    ResultRepository,
    RunRepository,
)
from .schemas import ExtractionLimits, ThreadContext

logger = logging.getLogger(__name__)


class RunNotFoundError(LookupError):
    pass


class RunNotCompleteError(RuntimeError):
    pass


class CreateDigestRun:
    def __init__(self, runs: RunRepository, queue: QueuePort) -> None:
        self._runs = runs
        self._queue = queue

    async def execute(
        self,
        *,
        user_id: str,
        mailbox_connection_id: str,
        idempotency_key: str,
        query: str | None = None,
        max_emails: int = 200,
        trigger: RunTrigger = RunTrigger.ON_DEMAND,
        schedule_id: str | None = None,
        now: datetime | None = None,
    ) -> DigestRun:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        run = DigestRun(
            id=f"run_{uuid4().hex}",
            user_id=user_id,
            mailbox_connection_id=mailbox_connection_id,
            schedule_id=schedule_id,
            trigger=trigger,
            status=RunStatus.QUEUED,
            query=normalize_query(query),
            idempotency_key=idempotency_key,
            max_emails=validate_max_emails(max_emails),
            created_at=now or datetime.now(UTC),
        )
        stored, created = await self._runs.create(run)
        if created:
            await self._queue.enqueue_digest_run(stored.id)
        return stored


class DigestWorker:
    def __init__(
        self,
        runs: RunRepository,
        results: ResultRepository,
        mailbox: MailboxPort,
        attachments: AttachmentExtractorPort,
        actions: ActionExtractorPort,
        outbox: CompletionOutboxPort,
        *,
        extraction_limits: ExtractionLimits | None = None,
    ) -> None:
        # ADR-003 retains this injection surface temporarily for callers/tests, but the
        # production baseline must not download or extract attachment content.
        del attachments, extraction_limits
        self._runs, self._results, self._mailbox = runs, results, mailbox
        self._actions, self._outbox = actions, outbox

    async def execute(
        self, run_id: str, *, user_timezone: str = "UTC", now: datetime | None = None
    ) -> DigestRun | None:
        clock = now or datetime.now(UTC)
        run = await self._runs.claim(run_id, clock)
        if run is None:
            return await self._runs.get(run_id)
        try:
            threads = await self._fetch_threads(run)
            await self._results.save_processed_emails(
                run.id,
                tuple(
                    ProcessedEmail(
                        provider_message_id=message.provider_message_id,
                        provider_thread_id=message.provider_thread_id,
                        subject=message.subject,
                        sender_address=message.sender_address,
                        received_at=message.received_at,
                    )
                    for thread in threads
                    for message in thread
                ),
            )
            contexts: list[ThreadContext] = []
            messages: dict[str, EmailEnvelope] = {}
            for thread in threads:
                for message in thread:
                    messages[message.provider_message_id] = message
                    run.attachments_found += len(message.attachments)
                contexts.append(ThreadContext(thread, ()))

            batch = await self._actions.extract(user_timezone, clock, contexts)
            items: list[ActionItem] = []
            actionable: set[str] = set()
            fingerprints: set[str] = set()
            for email_result in batch.emails:
                source = messages.get(email_result.provider_message_id)
                if source is None or email_result.classification != "actionable":
                    continue
                actionable.add(source.provider_message_id)
                for candidate in email_result.action_items:
                    if not candidate.evidence or candidate.confidence.value == "low":
                        continue
                    actionable.update(
                        message_id
                        for message_id in candidate.related_message_ids
                        if message_id in messages
                    )
                    fingerprint = action_fingerprint(
                        run.mailbox_connection_id,
                        candidate.incident_key or source.provider_thread_id,
                        candidate.title,
                        candidate.deadline_at,
                    )
                    if fingerprint in fingerprints:
                        continue
                    fingerprints.add(fingerprint)
                    priority, reason = calculate_priority(
                        candidate.deadline_at,
                        clock,
                        required=candidate.required,
                        explicit_blocker=candidate.explicit_blocker,
                        impact=candidate.impact,
                    )
                    seen = await self._results.fingerprint_seen(
                        run.mailbox_connection_id, fingerprint
                    )
                    items.append(
                        ActionItem(
                            id=f"act_{uuid4().hex}",
                            run_id=run.id,
                            mailbox_connection_id=run.mailbox_connection_id,
                            provider_message_id=source.provider_message_id,
                            provider_thread_id=source.provider_thread_id,
                            fingerprint=fingerprint,
                            freshness=ActionFreshness.SEEN if seen else ActionFreshness.NEW,
                            title=candidate.title.strip(),
                            summary=candidate.summary.strip(),
                            sender_name=source.sender_name,
                            sender_address=source.sender_address,
                            email_subject=source.subject,
                            email_received_at=source.received_at,
                            email_deep_link=source.deep_link,
                            deadline_at=candidate.deadline_at,
                            deadline_source=candidate.deadline_source,
                            deadline_text=candidate.deadline_text,
                            priority=priority,
                            priority_reason=reason,
                            action_plan=candidate.action_plan,
                            evidence=candidate.evidence,
                            confidence=candidate.confidence,
                            created_at=clock,
                            impact=candidate.impact,
                            incident_key=candidate.incident_key,
                            related_message_ids=tuple(
                                message_id
                                for message_id in candidate.related_message_ids
                                if message_id in messages
                            ),
                        )
                    )
            items.sort(key=lambda item: _action_item_sort_key(item, clock))
            await self._results.save_items(run.id, items)
            run.emails_actionable = len(actionable)
            run.action_items_count = len(items)
            run.ignored_emails_count = max(0, run.emails_processed - len(actionable))
            run.status = RunStatus.SUCCEEDED
        except Exception as exc:
            logger.exception("Digest run %s failed", run.id)
            run.status = RunStatus.FAILED
            run.error_code, run.error_message_safe = _safe_run_error(exc)
        run.completed_at = clock
        await self._runs.save(run)
        await self._outbox.add(DigestCompletedEvent(run.id, run.user_id, run.status, clock))
        return run

    async def _fetch_threads(self, run: DigestRun) -> list[tuple[EmailEnvelope, ...]]:
        threads: list[tuple[EmailEnvelope, ...]] = []
        message_ids: set[str] = set()
        messages_by_thread: dict[str, list[str]] = {}
        cursor: str | None = None
        while len(message_ids) < run.max_emails:
            page = await self._mailbox.search_unread(
                run.mailbox_connection_id,
                run.query,
                min(100, run.max_emails - len(message_ids)),
                cursor,
            )
            run.emails_matched = max(run.emails_matched, page.estimated_total or 0)
            for ref in page.messages:
                if ref.message_id in message_ids or len(message_ids) >= run.max_emails:
                    continue
                message_ids.add(ref.message_id)
                messages_by_thread.setdefault(ref.thread_id, []).append(ref.message_id)
            cursor = page.next_cursor
            if cursor is None or len(message_ids) >= run.max_emails:
                break

        for thread_id, selected_ids in messages_by_thread.items():
            thread = await self._mailbox.get_thread(run.mailbox_connection_id, thread_id)
            selected_id_set = set(selected_ids)
            selected = tuple(
                message for message in thread if message.provider_message_id in selected_id_set
            )
            run.emails_processed += len(selected)
            if selected:
                threads.append(selected)

        run.next_cursor = cursor
        run.truncated = cursor is not None or run.emails_matched > run.emails_processed
        if run.emails_matched == 0:
            run.emails_matched = run.emails_processed
        return threads

def _safe_run_error(exc: Exception) -> tuple[str, str]:
    """Return explicitly public error details without exposing secrets or email content."""
    error_code = getattr(exc, "error_code", None)
    safe_message = getattr(exc, "safe_message", None)
    if (
        isinstance(error_code, str)
        and error_code
        and isinstance(safe_message, str)
        and safe_message
    ):
        return error_code[:80], safe_message[:500]
    return "RUN_PROCESSING_FAILED", (
        "Xử lý email thất bại do lỗi nội bộ. Chi tiết kỹ thuật đã được ghi vào log backend."
    )


def _action_item_sort_key(item: ActionItem, clock: datetime) -> tuple[int, bool, datetime]:
    priority_order = {
        "urgent": 0,
        "high": 1,
        "medium": 2,
        "low": 3,
    }
    return (
        priority_order[item.priority.value],
        item.deadline_at is None,
        item.deadline_at or clock,
    )


class GetDigestResult:
    def __init__(self, runs: RunRepository, results: ResultRepository) -> None:
        self._runs, self._results = runs, results

    async def execute(self, run_id: str) -> dict[str, object]:
        run = await self._runs.get(run_id)
        if run is None:
            raise RunNotFoundError(run_id)
        if run.status not in TERMINAL_STATUSES:
            raise RunNotCompleteError("RUN_NOT_COMPLETE")
        items = list(await self._results.list_items(run_id))
        warnings = list(await self._results.list_warnings(run_id))
        processed_emails = list(await self._results.list_processed_emails(run_id))
        return {
            "run": run,
            "actionItems": items,
            "nextActions": items[:3],
            "attachmentWarnings": warnings,
            "processedEmails": processed_emails,
            "message": "Không có công việc cần xử lý" if not items else None,
        }
