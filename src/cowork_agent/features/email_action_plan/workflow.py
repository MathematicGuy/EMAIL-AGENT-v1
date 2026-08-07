"""Run creation, execution and result use cases."""

import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import NamedTuple
from uuid import uuid4

from cowork_agent.domain import (
    ActionFreshness,
    ActionItem,
    ActionPlanStep,
    Confidence,
    DeadlineSource,
    DigestRun,
    EvidenceRef,
    ProcessedEmail,
    RunStatus,
    RunTrigger,
)
from cowork_agent.domain.target_contracts import (
    TASK_PIPELINE_VERSION,
    ActionPlanOutput,
    EphemeralEmailEnvelope,
    RetrievalFilters,
    RetrievalLimits,
    RetrievalStatus,
    Route,
    SemanticRetrievalRequest,
    SemanticRetrievalResponse,
    Task,
)
from cowork_agent.features.email_action_plan.policies import (
    action_fingerprint,
    calculate_priority,
    normalize_query,
    validate_max_emails,
)
from cowork_agent.features.email_action_plan.short_term import ShortTermStore
from cowork_agent.identity import LOCAL_TENANT_ID

from .correlation import TaskCandidate, correlate_candidates
from .ports import (
    TERMINAL_STATUSES,
    ActionPlanGeneratorPort,
    AttachmentExtractorPort,
    MailboxPort,
    ResultRepository,
    RouteClassifierPort,
    RunRepository,
    SemanticMemoryPort,
    TaskRepository,
)
from .routing import RouteResolution, resolve_candidate_route
from .schemas import ExtractionLimits, GenerationContext
from .validation import validate_action_plan

logger = logging.getLogger(__name__)


class _GeneratedCandidate(NamedTuple):
    """One resolved candidate's generation context, consumed by validation and mapping."""

    resolution: RouteResolution
    retrieval: SemanticRetrievalResponse | None
    envelopes: tuple[EphemeralEmailEnvelope, ...]
    output: ActionPlanOutput


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
        semantic_memory: SemanticMemoryPort | None = None,
        task_repository: TaskRepository | None = None,
        extraction_limits: ExtractionLimits | None = None,
    ) -> None:
        # ADR-003 retains this injection surface temporarily for callers/tests, but the
        # production baseline must not download or extract attachment content.
        del attachments, extraction_limits
        self._runs, self._results, self._mailbox = runs, results, mailbox
        self._classifier, self._generator = classifier, generator
        self._semantic_memory = semantic_memory
        self._task_repository = task_repository
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
            run_context = GenerationContext(
                run_id=run.id, tenant_id=LOCAL_TENANT_ID, user_id=run.user_id
            )
            outputs: list[_GeneratedCandidate] = []
            for task_candidate in candidates:
                resolution = resolve_candidate_route(task_candidate)
                if resolution.route is Route.NO_ACTION:
                    continue
                candidate_envelopes = tuple(
                    messages[message_id] for message_id in task_candidate.source_message_ids
                )
                retrieval: SemanticRetrievalResponse | None = None
                if resolution.route is Route.RETRIEVE_RAG:
                    retrieval = await self._retrieve_for_candidate(run, task_candidate)
                # Cardinality (frozen contract rule 6, PRD-v1 FR-09): exactly
                # one Generator call per resolved non-NO_ACTION Task
                # Candidate; RETRIEVE_RAG adds zero-or-one retrieval call.
                outputs.append(
                    _GeneratedCandidate(
                        resolution,
                        retrieval,
                        candidate_envelopes,
                        await self._generator.generate(
                            user_timezone=user_timezone,
                            current_time=clock,
                            run_context=run_context,
                            candidate=task_candidate,
                            envelopes=candidate_envelopes,
                            resolution=resolution,
                            retrieval=retrieval,
                        ),
                    )
                )
            logger.debug(
                "Run %s routed %d candidate(s): %d classifier batch(es), %d generation call(s)",
                run.id,
                len(candidates),
                classification.batch_count,
                len(outputs),
            )
            # Routing owns selection (master-comparison §3.8): the legacy
            # "actionable" classification filter and the empty-evidence /
            # low-confidence skips disappear with the combined batch — every
            # generated Task that passes the FR-10 output validators becomes
            # an Action Item unless it duplicates a fingerprint already
            # produced in this run.
            items: list[ActionItem] = []
            validated_tasks: list[Task] = []
            actionable: set[str] = set()
            fingerprints: set[str] = set()
            for generated in outputs:
                validation = validate_action_plan(
                    generated.output,
                    resolution=generated.resolution,
                    retrieval=generated.retrieval,
                    envelopes=generated.envelopes,
                )
                if validation.task is None:
                    logger.warning(
                        "Run %s dropped generated task: %s",
                        run.id,
                        [v.code for v in validation.violations],
                    )
                    continue
                task = validation.task
                source = messages.get(task.gmail_message_id)
                if source is None:
                    continue
                actionable.update(
                    message_id
                    for message_id in task.source_message_ids
                    if message_id in messages
                )
                fingerprint = action_fingerprint(
                    run.mailbox_connection_id,
                    task.incident_key or source.gmail_thread_id,
                    task.title,
                    task.deadline,
                )
                if fingerprint in fingerprints:
                    continue
                fingerprints.add(fingerprint)
                validated_tasks.append(task)
                seen = await self._results.fingerprint_seen(
                    run.mailbox_connection_id, fingerprint
                )
                items.append(
                    _action_item_from_task(
                        task,
                        source,
                        connection_id=run.mailbox_connection_id,
                        run_id=run.id,
                        fingerprint=fingerprint,
                        clock=clock,
                        seen=seen,
                    )
                )
            items.sort(key=lambda item: _action_item_sort_key(item, clock))
            await self._results.save_items(run.id, items)
            if self._task_repository is not None:
                # FR-12: persist validated, deduplicated Tasks only once the
                # whole run loop succeeded — idempotent on
                # tenant:user:gmail_message_id:pipeline_version, so replays
                # update rather than duplicate.
                for task in validated_tasks:
                    await self._task_repository.save_task(
                        tenant_id=LOCAL_TENANT_ID,
                        user_id=run.user_id,
                        pipeline_version=TASK_PIPELINE_VERSION,
                        task=task,
                    )
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

    async def _retrieve_for_candidate(
        self, run: DigestRun, candidate: TaskCandidate
    ) -> SemanticRetrievalResponse:
        """Zero-or-one semantic retrieval per RETRIEVE_RAG candidate (§12.3).

        Builds the request from the member Route Decisions; on any port
        failure retries exactly once, then degrades to a structured empty
        ``no_results`` response so the Generator continues in partial mode
        instead of inventing knowledge.
        """
        gaps = tuple(
            dict.fromkeys(
                gap
                for _, decision in candidate.decisions
                for gap in decision.knowledge_gaps
            )
        )
        query = next(
            (
                decision.retrieval_query
                for _, decision in candidate.decisions
                if decision.retrieval_query
            ),
            None,
        )
        if self._semantic_memory is None or (query is None and not gaps):
            return _empty_retrieval()
        request = SemanticRetrievalRequest(
            run_id=run.id,
            tenant_id=LOCAL_TENANT_ID,
            user_id=run.user_id,
            query=query or "; ".join(gaps),
            knowledge_gaps=gaps,
            filters=RetrievalFilters(tenant_scope=LOCAL_TENANT_ID, document_status=()),
            limits=RetrievalLimits(top_k=5, min_score=-1.0, timeout_ms=8_000),
        )
        for attempt in (1, 2):
            try:
                return await self._semantic_memory.retrieve(request)
            except Exception as exc:
                # §12.3: metadata only, never email content.
                logger.warning(
                    "Run %s retrieval attempt %d failed: %s",
                    run.id,
                    attempt,
                    type(exc).__name__,
                )
        return _empty_retrieval()


def _empty_retrieval() -> SemanticRetrievalResponse:
    """Structured empty retrieval result (§12.3 degraded path)."""
    return SemanticRetrievalResponse(
        query_id=f"q_{uuid4().hex}",
        tenant_id=LOCAL_TENANT_ID,
        chunks=(),
        retrieval_status=RetrievalStatus.NO_RESULTS,
        latency_ms=0,
    )


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


def _action_item_from_task(
    task: Task,
    first_envelope: EphemeralEmailEnvelope,
    *,
    connection_id: str,
    run_id: str,
    fingerprint: str,
    clock: datetime,
    seen: bool,
) -> ActionItem:
    """Map one generated §6.6 Task onto the legacy ActionItem surface.

    The Task owns content and priority; the first envelope supplies the
    Gmail pointers. ``fingerprint``/``seen`` come from the unchanged
    freshness + dedupe machinery surrounding this mapping.
    """
    if task.priority is not None:
        priority, priority_reason = task.priority, "generated"
    else:
        priority, priority_reason = calculate_priority(task.deadline, clock)
    generation_confidence = task.generation_confidence
    if generation_confidence is None:
        confidence = Confidence.MEDIUM
    elif generation_confidence >= 0.8:
        confidence = Confidence.HIGH
    elif generation_confidence >= 0.5:
        confidence = Confidence.MEDIUM
    else:
        confidence = Confidence.LOW
    return ActionItem(
        id=f"act_{uuid4().hex}",
        run_id=run_id,
        mailbox_connection_id=connection_id,
        provider_message_id=first_envelope.gmail_message_id,
        provider_thread_id=first_envelope.gmail_thread_id,
        fingerprint=fingerprint,
        freshness=ActionFreshness.SEEN if seen else ActionFreshness.NEW,
        title=task.title.strip(),
        summary=task.request_summary.strip(),
        sender_name=first_envelope.sender_name or None,
        sender_address=first_envelope.sender_email,
        email_subject=first_envelope.subject,
        email_received_at=first_envelope.received_at,
        email_deep_link=first_envelope.gmail_url,
        deadline_at=task.deadline,
        deadline_source=DeadlineSource.EXPLICIT if task.deadline else DeadlineSource.NONE,
        deadline_text=None,
        priority=priority,
        priority_reason=priority_reason,
        action_plan=tuple(
            ActionPlanStep(
                step.step,
                step.instruction,
                "suggestion" if step.supporting_citation_ids else "inference",
            )
            for step in task.action_plan
        ),
        evidence=tuple(
            EvidenceRef(
                source_kind="rag",
                filename=document.title,
                location=document.section,
                excerpt="",
                source_message_id=None,
            )
            for document in task.supporting_documents
        ),
        confidence=confidence,
        created_at=clock,
        impact="none",
        incident_key=task.incident_key,
        related_message_ids=task.source_message_ids[1:],
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
