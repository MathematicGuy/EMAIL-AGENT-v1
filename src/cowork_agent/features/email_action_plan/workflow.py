"""Run creation, execution and result use cases."""

import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import NamedTuple
from uuid import uuid4

from cowork_agent.domain import (
    DigestRun,
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
)
from cowork_agent.features.email_action_plan.policies import (
    action_fingerprint,
    normalize_query,
    validate_max_emails,
)
from cowork_agent.features.email_action_plan.short_term import ShortTermStore
from cowork_agent.identity import LOCAL_TENANT_ID

from .compat_mapper import legacy_result_shape
from .correlation import TaskCandidate, correlate_candidates
from .ports import (
    TERMINAL_STATUSES,
    ActionPlanGeneratorPort,
    AttachmentExtractorPort,
    MailboxPort,
    PersistedTask,
    ResultRepository,
    RouteClassifierPort,
    RunRepository,
    SemanticMemoryPort,
    TaskPointer,
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
        task_repository: TaskRepository,
        *,
        semantic_memory: SemanticMemoryPort | None = None,
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
            # Routing owns selection (master-comparison §3.8): every generated
            # Task that passes the FR-10 output validators is persisted unless
            # it duplicates a fingerprint already produced in this run. The
            # legacy Action Item surface is derived from the persisted Tasks
            # by the compatibility mapper at read time (T4.2).
            records: list[PersistedTask] = []
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
                records.append(
                    PersistedTask(
                        # The persisting run owns the durable row: normalize
                        # the generator-stamped run identity onto the Task.
                        task=replace(task, run_id=run.id),
                        pointer=TaskPointer(
                            mailbox_connection_id=run.mailbox_connection_id,
                            provider_thread_id=source.gmail_thread_id,
                            sender_name=source.sender_name,
                            sender_address=source.sender_email,
                            email_subject=source.subject,
                            email_received_at=source.received_at,
                        ),
                        fingerprint=fingerprint,
                    )
                )
            # FR-12: persist validated, deduplicated Tasks only once the
            # whole run loop succeeded — idempotent on
            # tenant:user:gmail_message_id:pipeline_version, so replays
            # update rather than duplicate.
            for record in records:
                await self._task_repository.save_task(
                    record,
                    tenant_id=LOCAL_TENANT_ID,
                    user_id=run.user_id,
                    pipeline_version=TASK_PIPELINE_VERSION,
                    run_id=run.id,
                )
            run.emails_actionable = len(actionable)
            run.action_items_count = len(records)
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


class GetDigestResult:
    def __init__(
        self, runs: RunRepository, results: ResultRepository, tasks: TaskRepository
    ) -> None:
        self._runs, self._results, self._tasks = runs, results, tasks

    async def execute(self, run_id: str) -> dict[str, object]:
        run = await self._runs.get(run_id)
        if run is None:
            raise RunNotFoundError(run_id)
        if run.status not in TERMINAL_STATUSES:
            raise RunNotCompleteError("RUN_NOT_COMPLETE")
        return legacy_result_shape(
            run=run,
            persisted=await self._tasks.list_for_run(run_id),
            warnings=await self._results.list_warnings(run_id),
            processed_emails=await self._results.list_processed_emails(run_id),
            clock=run.completed_at or datetime.now(UTC),
        )
