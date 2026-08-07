"""Run creation, execution and result use cases."""

import logging
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from cowork_agent.domain import (
    ActionFreshness,
    ActionItem,
    DigestRun,
    ProcessedEmail,
    RunStatus,
    RunTrigger,
)
from cowork_agent.domain.target_contracts import EphemeralEmailEnvelope, Route
from cowork_agent.features.email_action_plan.policies import (
    action_fingerprint,
    calculate_priority,
    normalize_query,
    validate_max_emails,
)
from cowork_agent.features.email_action_plan.short_term import ShortTermStore
from cowork_agent.identity import LOCAL_TENANT_ID

from .correlation import correlate_candidates
from .ports import (
    TERMINAL_STATUSES,
    ActionPlanGeneratorPort,
    AttachmentExtractorPort,
    MailboxPort,
    ResultRepository,
    RouteClassifierPort,
    RunRepository,
)
from .routing import resolve_candidate_route
from .schemas import EmailExtraction, ExtractionBatch, ExtractionLimits

logger = logging.getLogger(__name__)


class RunNotFoundError(LookupError):
    pass


class RunNotCompleteError(RuntimeError):
    pass


class CreateDigestRun:
    def __init__(self, runs: RunRepository) -> None:
        self._runs = runs

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
        stored, _created = await self._runs.create(run)
        return stored


class DigestWorker:
    def __init__(
        self,
        runs: RunRepository,
        results: ResultRepository,
        mailbox: MailboxPort,
        attachments: AttachmentExtractorPort,
        classifier: RouteClassifierPort,
        generator: ActionPlanGeneratorPort,
        short_term: ShortTermStore,
        *,
        extraction_limits: ExtractionLimits | None = None,
    ) -> None:
        # ADR-003 retains this injection surface temporarily for callers/tests, but the
        # production baseline must not download or extract attachment content.
        del attachments, extraction_limits
        self._runs, self._results, self._mailbox = runs, results, mailbox
        self._classifier, self._generator = classifier, generator
        self._short_term = short_term

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
                        provider_message_id=message.gmail_message_id,
                        provider_thread_id=message.gmail_thread_id,
                        subject=message.subject,
                        sender_address=message.sender_email,
                        received_at=message.received_at,
                    )
                    for thread in threads
                    for message in thread
                ),
            )
            envelopes: list[EphemeralEmailEnvelope] = []
            messages: dict[str, EphemeralEmailEnvelope] = {}
            for thread in threads:
                for message in thread:
                    messages[message.gmail_message_id] = message
                    run.attachments_found += int(message.attachments_present)
                envelopes.extend(thread)

            self._short_term.put(run.id, envelopes)
            stored_envelopes = self._short_term.get(run.id)
            if stored_envelopes is None:  # defensive only: put() above guarantees presence
                stored_envelopes = ()
            classification = await self._classifier.classify(
                user_timezone, clock, stored_envelopes
            )
            decisions = {
                classified.gmail_message_id: classified.decision
                for classified in classification.decisions
            }
            candidates = correlate_candidates(decisions, messages)
            extracted_emails: list[EmailExtraction] = []
            generated_count = 0
            for task_candidate in candidates:
                resolution = resolve_candidate_route(task_candidate)
                if resolution.route is Route.NO_ACTION:
                    continue
                candidate_envelopes = tuple(
                    messages[message_id] for message_id in task_candidate.source_message_ids
                )
                # Cardinality (frozen contract): exactly one Generator call per
                # resolved non-NO_ACTION Task Candidate.
                candidate_batch = await self._generator.generate(
                    user_timezone, clock, candidate_envelopes
                )
                generated_count += 1
                extracted_emails.extend(candidate_batch.emails)
            logger.debug(
                "Run %s routed %d candidate(s): %d classifier batch(es), %d generation call(s)",
                run.id,
                len(candidates),
                classification.batch_count,
                generated_count,
            )
            batch = ExtractionBatch(emails=tuple(extracted_emails))
            items: list[ActionItem] = []
            actionable: set[str] = set()
            fingerprints: set[str] = set()
            for email_result in batch.emails:
                source = messages.get(email_result.provider_message_id)
                if source is None or email_result.classification != "actionable":
                    continue
                actionable.add(source.gmail_message_id)
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
                        candidate.incident_key or source.gmail_thread_id,
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
                            provider_message_id=source.gmail_message_id,
                            provider_thread_id=source.gmail_thread_id,
                            fingerprint=fingerprint,
                            freshness=ActionFreshness.SEEN if seen else ActionFreshness.NEW,
                            title=candidate.title.strip(),
                            summary=candidate.summary.strip(),
                            sender_name=source.sender_name or None,
                            sender_address=source.sender_email,
                            email_subject=source.subject,
                            email_received_at=source.received_at,
                            email_deep_link=source.gmail_url,
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
        finally:
            # Run finalizer (FR-14): raw bodies never outlive the run, on any outcome.
            self._short_term.clear(run.id)
        run.completed_at = clock
        await self._runs.save(run)
        return run

    async def _fetch_threads(
        self, run: DigestRun
    ) -> list[tuple[EphemeralEmailEnvelope, ...]]:
        threads: list[tuple[EphemeralEmailEnvelope, ...]] = []
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
            # Mailbox adapters leave run identity empty; the workflow stamps it once, here.
            selected = tuple(
                replace(
                    message,
                    run_id=run.id,
                    tenant_id=LOCAL_TENANT_ID,
                    user_id=run.user_id,
                )
                for message in thread
                if message.gmail_message_id in selected_id_set
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
