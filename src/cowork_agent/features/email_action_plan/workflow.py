"""Run creation, execution and result use cases."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import NamedTuple, TypeVar, cast
from uuid import uuid4

from langfuse import observe

from cowork_agent.domain import (
    DigestCompletedEvent,
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
    Task,
    TraceEvent,
    TraceLatency,
    TraceStatus,
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
from .observability import EncryptedDevTraceSink, TraceSink
from .ports import (
    TERMINAL_STATUSES,
    ActionPlanGeneratorPort,
    AttachmentExtractorPort,
    CompletionOutboxPort,
    MailboxPort,
    MailboxTemporaryError,
    PersistedTask,
    ResultRepository,
    RouteClassifierPort,
    RunRepository,
    SemanticMemoryPort,
    TaskPointer,
    TaskRepository,
)
from .routing import RouteResolution, resolve_candidate_route
from .schemas import ExtractionLimits, GenerationContext, MessageRef
from .validation import validate_action_plan

logger = logging.getLogger(__name__)

_T = TypeVar("_T")
_R = TypeVar("_R")
_UNSET = object()


async def _bounded_gather(
    items: Sequence[_T],
    *,
    limit: int,
    operation: Callable[[_T], Awaitable[_R]],
) -> list[_R]:
    """Run independent I/O with a bounded fan-out and input-order results."""
    semaphore = asyncio.Semaphore(limit)

    async def run_item(item: _T) -> _R:
        async with semaphore:
            return await operation(item)

    return list(await asyncio.gather(*(run_item(item) for item in items)))


async def _bounded_fail_fast(
    items: Sequence[_T],
    *,
    limit: int,
    operation: Callable[[_T], Awaitable[_R]],
) -> list[_R]:
    """Run at most ``limit`` operations, stopping queued work after a failure.

    Results remain aligned to the input sequence. When simultaneous work
    fails, raise the exception belonging to the earliest input item so a run's
    safe error is deterministic.
    """
    results: list[_R | object] = [_UNSET] * len(items)
    active: dict[asyncio.Task[_R], int] = {}
    next_index = 0

    async def run_item(item: _T) -> _R:
        return await operation(item)

    def start_next() -> None:
        nonlocal next_index
        task: asyncio.Task[_R] = asyncio.create_task(run_item(items[next_index]))
        active[task] = next_index
        next_index += 1

    while next_index < len(items) and len(active) < limit:
        start_next()
    while active:
        done, _pending = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
        failures: list[tuple[int, BaseException]] = []
        for task in done:
            index = active.pop(task)
            try:
                results[index] = task.result()
            except BaseException as exc:
                failures.append((index, exc))
        if failures:
            for task in active:
                task.cancel()
            await asyncio.gather(*active, return_exceptions=True)
            _index, error = min(failures, key=lambda failure: failure[0])
            raise error
        while next_index < len(items) and len(active) < limit:
            start_next()
    return [cast(_R, result) for result in results]


class _RetrievalOutcome(NamedTuple):
    """§12.3 outcome of the zero-or-one retrieval for one candidate."""

    response: SemanticRetrievalResponse
    skipped: bool  # nothing queryable (no query/knowledge gaps)
    degraded: bool  # structured empty after the bounded retry ladder failed


class _GeneratedCandidate(NamedTuple):
    """One resolved candidate's generation context, consumed by validation,
    telemetry, and mapping."""

    candidate: TaskCandidate
    resolution: RouteResolution
    retrieval: _RetrievalOutcome | None
    envelopes: tuple[EphemeralEmailEnvelope, ...]
    output: ActionPlanOutput
    retrieval_ms: int | None
    generation_ms: int


#: FR-11 partial-plan fallback: a RETRIEVE_RAG plan generated without any
#: company chunks must list the missing context explicitly.
_NO_COMPANY_CONTEXT_NOTE = "Kế hoạch được tạo mà không có ngữ cảnh công ty."


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
        max_emails: int = 10,
        trigger: RunTrigger = RunTrigger.ON_DEMAND,
        now: datetime | None = None,
    ) -> DigestRun:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        run = DigestRun(
            id=f"run_{uuid4().hex}",
            user_id=user_id,
            mailbox_connection_id=mailbox_connection_id,
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
        trace_sink: TraceSink | None = None,
        dev_trace: EncryptedDevTraceSink | None = None,
        extraction_limits: ExtractionLimits | None = None,
        completion_outbox: CompletionOutboxPort | None = None,
        mailbox_fetch_concurrency: int = 6,
        generation_concurrency: int = 1,
    ) -> None:
        # ADR-003 retains this injection surface temporarily for callers/tests, but the
        # production baseline must not download or extract attachment content.
        del attachments, extraction_limits
        self._runs, self._results, self._mailbox = runs, results, mailbox
        self._classifier, self._generator = classifier, generator
        self._semantic_memory = semantic_memory
        self._task_repository = task_repository
        self._short_term = short_term
        self._trace_sink = trace_sink
        self._dev_trace = dev_trace
        self._completion_outbox = completion_outbox
        if mailbox_fetch_concurrency < 1:
            raise ValueError("mailbox_fetch_concurrency must be positive")
        if generation_concurrency < 1:
            raise ValueError("generation_concurrency must be positive")
        self._mailbox_fetch_concurrency = mailbox_fetch_concurrency
        self._generation_concurrency = generation_concurrency

    @observe(name="execute_digest_run")
    async def execute(
        self, run_id: str, *, user_timezone: str = "UTC", now: datetime | None = None
    ) -> DigestRun | None:
        clock = now or datetime.now(UTC)
        run = await self._runs.claim(run_id, clock)
        if run is None:
            return await self._runs.get(run_id)
        email_ms: int | None = None
        classifier_ms: int | None = None
        persistence_ms: int | None = None
        try:
            logger.info(
                "🚀 [RUN %s] Starting worker scan (User: %s, Max Emails: %d)",
                run.id,
                run.user_id,
                run.max_emails,
            )
            fetch_started = time.monotonic()
            threads, skipped_threads = await self._fetch_threads(run)
            email_ms = int((time.monotonic() - fetch_started) * 1000)
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
                    for message in sorted(
                        (message for thread in threads for message in thread),
                        key=lambda item: item.received_at,
                        reverse=True,
                    )
                ),
            )
            envelopes: list[EphemeralEmailEnvelope] = []
            messages: dict[str, EphemeralEmailEnvelope] = {}
            for thread in threads:
                for message in thread:
                    messages[message.gmail_message_id] = message
                    run.attachments_found += int(message.attachments_present)
                envelopes.extend(thread)

            logger.info(
                "📥 [RUN %s] Fetched %d thread(s) / %d email envelope(s) in %d ms",
                run.id,
                len(threads),
                len(envelopes),
                email_ms,
            )

            self._short_term.put(run.id, envelopes)
            stored_envelopes = self._short_term.get(run.id)
            if stored_envelopes is None:  # defensive only: put() above guarantees presence
                stored_envelopes = ()
            self._write_dev_trace(
                run.id,
                "classifier_input",
                lambda: [envelope.to_dict() for envelope in stored_envelopes],
            )
            classify_started = time.monotonic()
            logger.info(
                "🤖 [RUN %s] Classifying route for %d email(s) via Gemini LLM...",
                run.id,
                len(stored_envelopes),
            )
            classification = await self._classifier.classify(user_timezone, clock, stored_envelopes)
            classifier_ms = int((time.monotonic() - classify_started) * 1000)
            run.filtered_summary = classification.filtered_summary
            decisions = {
                classified.gmail_message_id: classified.decision
                for classified in classification.decisions
            }
            candidates = correlate_candidates(decisions, messages)
            logger.info(
                "⚡ [RUN %s] Correlated into %d Task Candidate(s) (Classifier took %d ms)",
                run.id,
                len(candidates),
                classifier_ms,
            )
            run_context = GenerationContext(
                run_id=run.id, user_id=run.user_id
            )
            actionable_candidates: list[tuple[TaskCandidate, RouteResolution]] = []
            for task_candidate in candidates:
                resolution = resolve_candidate_route(task_candidate)
                logger.info(
                    "  ├─ Candidate '%s' -> Route: %s",
                    task_candidate.candidate_key,
                    resolution.route.value.upper(),
                )
                if resolution.route is Route.NO_ACTION:
                    continue
                actionable_candidates.append((task_candidate, resolution))
            generation_started = time.monotonic()
            outputs = await _bounded_fail_fast(
                actionable_candidates,
                limit=self._generation_concurrency,
                operation=lambda item: self._generate_candidate(
                    run,
                    item[0],
                    item[1],
                    messages,
                    run_context,
                    user_timezone,
                    clock,
                ),
            )
            logger.info(
                "Run %s generated %d action plan(s) with concurrency %d in %d ms",
                run.id,
                len(outputs),
                self._generation_concurrency,
                int((time.monotonic() - generation_started) * 1000),
            )
            logger.info(
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
                    retrieval=(
                        generated.retrieval.response if generated.retrieval is not None else None
                    ),
                    envelopes=generated.envelopes,
                )
                if validation.task is None:
                    violation_codes = [v.code for v in validation.violations]
                    logger.warning(
                        "Run %s dropped generated task: %s",
                        run.id,
                        violation_codes,
                    )
                    self._emit_candidate_trace(
                        run, generated, violation_codes=tuple(violation_codes)
                    )
                    continue
                task = validation.task
                if generated.retrieval is not None and not generated.retrieval.response.chunks:
                    task = replace(
                        task,
                        missing_information=task.missing_information + (_NO_COMPANY_CONTEXT_NOTE,),
                    )
                self._emit_candidate_trace(run, generated, task=task)
                source = messages.get(task.gmail_message_id)
                if source is None:
                    continue
                actionable.update(
                    message_id for message_id in task.source_message_ids if message_id in messages
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
            self._write_dev_trace(
                run.id,
                "generated_output",
                lambda: [item.output.to_dict() for item in outputs],
            )
            # FR-12: persist validated, deduplicated Tasks only once the
            # whole run loop succeeded — idempotent on
            # tenant:user:gmail_message_id:pipeline_version, so replays
            # update rather than duplicate.
            persistence_started = time.monotonic()
            for record in records:
                await self._task_repository.save_task(
                    record,
                    tenant_id=LOCAL_TENANT_ID,
                    user_id=run.user_id,
                    pipeline_version=TASK_PIPELINE_VERSION,
                    run_id=run.id,
                )
            persistence_ms = int((time.monotonic() - persistence_started) * 1000)
            run.emails_actionable = len(actionable)
            run.action_items_count = len(records)
            run.ignored_emails_count = max(0, run.emails_processed - len(actionable))
            # T5.4: a run that had to skip threads after their retry budget
            # is PARTIAL, not SUCCEEDED — degradation must stay visible.
            run.status = RunStatus.PARTIAL if skipped_threads else RunStatus.SUCCEEDED
            logger.info(
                "🎉 [RUN %s] Execution finished "
                "(Status: %s, Action Items: %d, Actionable Emails: %d)",
                run.id,
                run.status.value.upper(),
                run.action_items_count,
                run.emails_actionable,
            )
        except Exception as exc:
            logger.exception("❌ [RUN %s] FAILED: %s", run.id, exc)
            run.status = RunStatus.FAILED
            run.error_code, run.error_message_safe = _safe_run_error(exc)
        finally:
            # Run finalizer (FR-14): raw bodies never outlive the run, on any outcome.
            self._short_term.clear(run.id)
        self._emit_run_trace(run, email_ms, classifier_ms, persistence_ms)
        # The claim clock is not the completion clock. Reusing it stamped every
        # run as zero-duration, which hides the latency the SLO is measured on.
        completed = now or datetime.now(UTC)
        run.completed_at = completed
        await self._runs.save(run)
        await self._append_completion_event(run, completed)
        return run

    async def _append_completion_event(self, run: DigestRun, clock: datetime) -> None:
        """T5.3: durable, metadata-only lifecycle event for every terminal run."""
        if self._completion_outbox is None:
            return
        try:
            await self._completion_outbox.add(
                DigestCompletedEvent(
                    run_id=run.id,
                    user_id=run.user_id,
                    status=run.status,
                    occurred_at=clock,
                )
            )
        except Exception as exc:
            logger.warning(
                "Completion event for run %s could not be appended to the outbox (%s)",
                run.id,
                type(exc).__name__,
            )

    @observe(name="fetch_threads")
    async def _fetch_threads(
        self, run: DigestRun
    ) -> tuple[list[tuple[EphemeralEmailEnvelope, ...]], int]:
        threads: list[tuple[EphemeralEmailEnvelope, ...]] = []
        skipped_threads = 0
        refs: list[MessageRef] = []
        seen_message_ids: set[str] = set()
        cursor: str | None = None
        while True:
            page = await self._mailbox.search_unread(
                run.mailbox_connection_id,
                run.query,
                min(run.max_emails, 100),
                cursor,
            )
            run.emails_matched = max(run.emails_matched, page.estimated_total or 0)
            for ref in page.messages:
                if ref.message_id in seen_message_ids:
                    continue
                seen_message_ids.add(ref.message_id)
                refs.append(ref)
                if len(refs) >= run.max_emails:
                    break
            cursor = page.next_cursor
            if cursor is None or len(refs) >= run.max_emails:
                break

        selected_refs = refs[: run.max_emails]
        messages_by_thread: dict[str, list[str]] = {}
        for ref in selected_refs:
            messages_by_thread.setdefault(ref.thread_id, []).append(ref.message_id)

        thread_items = tuple(messages_by_thread.items())

        async def fetch_thread(
            item: tuple[str, list[str]],
        ) -> Sequence[EphemeralEmailEnvelope] | None:
            thread_id, _selected_ids = item
            try:
                return await self._mailbox.get_thread(run.mailbox_connection_id, thread_id)
            except MailboxTemporaryError:
                # T5.4 partial-batch continuation: one thread's transient
                # failure must not abort the run; the adapter already
                # exhausted its retry budget, so skip and continue.
                logger.warning(
                    "Run %s skipping thread %s after transient mailbox failure",
                    run.id,
                    thread_id,
                )
                return None

        thread_started = time.monotonic()
        thread_results = await _bounded_gather(
            thread_items,
            limit=self._mailbox_fetch_concurrency,
            operation=fetch_thread,
        )
        logger.info(
            "Run %s fetched %d Gmail threads with concurrency %d in %d ms",
            run.id,
            len(thread_items),
            self._mailbox_fetch_concurrency,
            int((time.monotonic() - thread_started) * 1000),
        )
        for (_thread_id, selected_ids), thread in zip(
            thread_items, thread_results, strict=True
        ):
            if thread is None:
                skipped_threads += 1
                continue
            selected_id_set = set(selected_ids)
            # Mailbox adapters leave run identity empty; the workflow stamps it once, here.
            selected = tuple(
                replace(
                    message,
                    run_id=run.id,
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
        return threads, skipped_threads

    async def _generate_candidate(
        self,
        run: DigestRun,
        task_candidate: TaskCandidate,
        resolution: RouteResolution,
        messages: dict[str, EphemeralEmailEnvelope],
        run_context: GenerationContext,
        user_timezone: str,
        clock: datetime,
    ) -> _GeneratedCandidate:
        """Retrieve (when needed) and generate one independent candidate."""
        candidate_envelopes = tuple(
            messages[message_id] for message_id in task_candidate.source_message_ids
        )
        retrieval: _RetrievalOutcome | None = None
        retrieval_ms: int | None = None
        if resolution.route is Route.RETRIEVE_RAG:
            logger.info("  ├─ 🔍 Querying internal RAG knowledge base...")
            retrieval_started = time.monotonic()
            retrieval = await self._retrieve_for_candidate(run, task_candidate)
            retrieval_ms = int((time.monotonic() - retrieval_started) * 1000)
            logger.info(
                "  ├─ 🔍 RAG returned %d document chunk(s) (%d ms)",
                len(retrieval.response.chunks),
                retrieval_ms,
            )
        generation_started = time.monotonic()
        logger.info("  ├─ ✍️ Generating Action Plan via Gemini LLM...")
        output = await self._generator.generate(
            user_timezone=user_timezone,
            current_time=clock,
            run_context=run_context,
            candidate=task_candidate,
            envelopes=candidate_envelopes,
            resolution=resolution,
            retrieval=retrieval.response if retrieval is not None else None,
        )
        generation_ms = int((time.monotonic() - generation_started) * 1000)
        logger.info("  └─ ✍️ Action Plan generated (%d ms)", generation_ms)
        return _GeneratedCandidate(
            task_candidate,
            resolution,
            retrieval,
            candidate_envelopes,
            output,
            retrieval_ms,
            generation_ms,
        )

    async def _retrieve_for_candidate(
        self, run: DigestRun, candidate: TaskCandidate
    ) -> _RetrievalOutcome:
        """Zero-or-one semantic retrieval per RETRIEVE_RAG candidate (§12.3).

        Builds the request from the member Route Decisions; on any port
        failure retries exactly once, then degrades to a structured empty
        ``no_results`` response so the Generator continues in partial mode
        instead of inventing knowledge.
        """
        gaps = tuple(
            dict.fromkeys(
                gap for _, decision in candidate.decisions for gap in decision.knowledge_gaps
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
        if self._semantic_memory is None:
            # §12.3 module failure: no semantic adapter is wired.
            return _RetrievalOutcome(_empty_retrieval(), skipped=False, degraded=True)
        if query is None and not gaps:
            return _RetrievalOutcome(_empty_retrieval(), skipped=True, degraded=False)
        request = SemanticRetrievalRequest(
            run_id=run.id,
            user_id=run.user_id,
            query=query or "; ".join(gaps),
            knowledge_gaps=gaps,
            filters=RetrievalFilters(document_status=("ready",)),
            limits=RetrievalLimits(top_k=5, min_score=-1.0, timeout_ms=8_000),
        )
        for attempt in (1, 2):
            try:
                response = await self._semantic_memory.retrieve(request)
                return _RetrievalOutcome(response, skipped=False, degraded=False)
            except Exception as exc:
                # §12.3: metadata only, never email content.
                logger.warning(
                    "Run %s retrieval attempt %d failed: %s",
                    run.id,
                    attempt,
                    type(exc).__name__,
                )
        return _RetrievalOutcome(_empty_retrieval(), skipped=False, degraded=True)

    def _emit_candidate_trace(
        self,
        run: DigestRun,
        generated: _GeneratedCandidate,
        *,
        task: Task | None = None,
        violation_codes: tuple[str, ...] = (),
    ) -> None:
        """FR-16: one metadata-only §6.8 event per generated candidate."""
        if self._trace_sink is None:
            return
        retrieval = generated.retrieval.response if generated.retrieval is not None else None
        decisions = tuple(decision for _, decision in generated.candidate.decisions)
        reason_codes = tuple(
            dict.fromkeys(code.value for decision in decisions for code in decision.reason_codes)
        )
        classifier_confidence = (
            task.classifier_confidence
            if task is not None
            else next((decision.confidence for decision in decisions), None)
        )
        # FR-16 fallback marker: §12.3 degraded retrieval (empty result after
        # the retry ladder) must be distinguishable from a genuine no_results.
        degraded = generated.retrieval is not None and generated.retrieval.degraded
        if generated.retrieval is None:
            retrieval_status: str | None = None
        elif generated.retrieval.skipped:
            # §14: keep skipped retrievals out of the no-result rate.
            retrieval_status = "skipped"
        else:
            retrieval_status = retrieval.retrieval_status.value if retrieval else None
        validation_status = (
            task.validation_status.value if task is not None else ";".join(violation_codes)
        )
        self._trace_sink.record(
            TraceEvent(
                run_id=run.id,
                user_id=run.user_id,
                gmail_message_id=generated.output.task.gmail_message_id,
                event_name="task_candidate",
                status=TraceStatus.SUCCESS if task is not None else TraceStatus.FAILED,
                route=generated.resolution.route,
                reason_codes=reason_codes,
                classifier_confidence=classifier_confidence,
                rag_result_count=len(retrieval.chunks) if retrieval is not None else None,
                retrieval_status=retrieval_status,
                generation_status="RETRIEVAL_DEGRADED" if degraded else None,
                validation_status=validation_status,
                latency_ms=TraceLatency(
                    rag=generated.retrieval_ms, generation=generated.generation_ms
                ),
            )
        )

    def _emit_run_trace(
        self,
        run: DigestRun,
        email_ms: int | None,
        classifier_ms: int | None,
        persistence_ms: int | None,
    ) -> None:
        """FR-16: run-level §6.8 event with status, error marker, stage latency."""
        if self._trace_sink is None:
            return
        self._trace_sink.record(
            TraceEvent(
                run_id=run.id,
                user_id=run.user_id,
                gmail_message_id=None,
                event_name="digest_run",
                status=TraceStatus.FAILED
                if run.status is RunStatus.FAILED
                else TraceStatus.SUCCESS,
                route=None,
                reason_codes=(),
                classifier_confidence=None,
                rag_result_count=None,
                retrieval_status=None,
                # Error/fallback marker (FR-16): code only, never content.
                generation_status=run.error_code,
                validation_status=None,
                latency_ms=TraceLatency(
                    email=email_ms, classifier=classifier_ms, persistence=persistence_ms
                ),
            )
        )

    def _write_dev_trace(self, run_id: str, kind: str, payload: Callable[[], object]) -> None:
        """FR-15 full-content development trace; never fails the run.

        The payload is a lazy callable: full-content serialization happens
        only when the development trace is actually enabled.
        """
        if self._dev_trace is None:
            return
        try:
            self._dev_trace.write(run_id=run_id, kind=kind, payload=payload())
        except Exception:
            logger.warning("Development trace write failed for run %s (%s)", run_id, kind)


def _empty_retrieval() -> SemanticRetrievalResponse:
    """Structured empty retrieval result (§12.3 degraded path)."""
    return SemanticRetrievalResponse(
        query_id=f"q_{uuid4().hex}",
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
