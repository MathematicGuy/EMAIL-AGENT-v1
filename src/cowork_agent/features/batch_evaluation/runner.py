"""Budget-aware lane execution for durable Level 1 evaluation jobs."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar

from cowork_agent.domain.chat_contracts import ChatMessageRequest
from cowork_agent.features.ai_chat.generation_context import GenerationContext
from cowork_agent.features.ai_chat.ports import ChatReplyChunk, ChatReplyPort
from cowork_agent.features.batch_evaluation.artifacts import (
    FilesystemEvaluationArtifactStore,
    UnsafeArtifact,
)
from cowork_agent.features.batch_evaluation.contracts import (
    ArtifactBundle,
    AttemptState,
    CleanupOutcome,
    CredentialState,
    EvaluationBudget,
    EvaluationPlugin,
    EvaluationRequest,
    EvaluationWarning,
    FailureClass,
    FailureClassification,
    JobState,
    PluginPlan,
    ProviderAttemptEvent,
    StepState,
    UnitState,
    WorkContext,
    WorkUnit,
    WorkUnitOutcome,
)
from cowork_agent.features.batch_evaluation.credentials import (
    CredentialLease,
    CredentialLeasingPool,
)
from cowork_agent.features.batch_evaluation.planning import DataSharder
from cowork_agent.features.batch_evaluation.registry import PluginRegistry
from cowork_agent.integrations.llm.evaluation_mistral import MistralEvaluationReplyFactory
from cowork_agent.persistence.repositories.evaluation_jobs import (
    EvaluationJob,
    InvalidStateTransition,
)

AttemptSink = Callable[[ProviderAttemptEvent], Awaitable[None] | None]
_TERMINAL_STATES = frozenset(
    {
        JobState.SUCCEEDED,
        JobState.PARTIALLY_SUCCEEDED,
        JobState.FAILED,
        JobState.CANCELLED,
    }
)
_AMBIGUOUS_PROVIDER_OUTCOMES = frozenset({"timed_out", "timeout", "unknown_timeout"})
_OperationResult = TypeVar("_OperationResult")
_UnitKey = tuple[str, str]


class StoredEvaluationUnit(Protocol):
    job_id: str
    unit_id: str
    ordinal: int
    state: UnitState
    claimed_by: str | None
    payload: Mapping[str, object]
    provider_requests: int
    total_tokens: int
    outcome_ref: str | None


class StoredEvaluationAttempt(Protocol):
    attempt_id: str


class RunnerRepository(Protocol):
    """Durable runner seam, including outcome fields supplied by persistence."""

    async def get_job(self, job_id: str) -> EvaluationJob | None: ...

    async def transition_job(
        self,
        job_id: str,
        state: JobState,
        *,
        effective_workers: int | None = None,
        warnings: Sequence[EvaluationWarning] | None = None,
    ) -> EvaluationJob: ...

    async def list_units(self, job_id: str) -> tuple[StoredEvaluationUnit, ...]: ...

    async def append_warnings(
        self, job_id: str, warnings: Sequence[EvaluationWarning]
    ) -> EvaluationJob: ...

    async def claim_ready_unit(self, job_id: str, worker_id: str) -> WorkUnit | None: ...

    async def claim_ready_unit_by_id(
        self, job_id: str, unit_id: str, worker_id: str
    ) -> WorkUnit | None: ...

    async def complete_unit(
        self,
        job_id: str,
        outcome: WorkUnitOutcome,
        *,
        outcome_ref: str | None,
    ) -> None: ...

    async def start_attempt(
        self, job_id: str, unit_id: str, worker_id: str, credential_alias: str
    ) -> StoredEvaluationAttempt: ...

    async def finish_attempt(
        self,
        attempt_id: str,
        state: AttemptState,
        failure_class: FailureClass | None = None,
        *,
        worker_id: str,
    ) -> object: ...

    async def write_step(
        self,
        job_id: str,
        unit_id: str,
        worker_id: str,
        attempt_id: str,
        *,
        step_id: str,
        ordinal: int,
        state: StepState,
        safe_metadata: Mapping[str, object],
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class _ExecutedUnit:
    outcome: WorkUnitOutcome
    outcome_ref: str | None
    warnings: tuple[EvaluationWarning, ...] = ()


@dataclass(frozen=True, slots=True)
class _CleanupResult:
    warnings: tuple[EvaluationWarning, ...]
    error: BaseException | None


async def _resolve_durable_operation(
    operation: Awaitable[_OperationResult],
) -> tuple[_OperationResult | None, BaseException | None, bool]:
    task = asyncio.ensure_future(operation)
    cancellation_requested = False
    while True:
        try:
            return await asyncio.shield(task), None, cancellation_requested
        except asyncio.CancelledError:
            cancellation_requested = True
            if task.done():
                try:
                    return task.result(), None, cancellation_requested
                except BaseException as error:
                    return None, error, cancellation_requested
        except BaseException as error:
            return None, error, cancellation_requested


class _DurableOutcomeQueue:
    """Bound claims while completing each unit with its already-written artifact reference."""

    def __init__(self, repository: RunnerRepository, capacity: int) -> None:
        self._repository = repository
        self._slots = asyncio.Semaphore(capacity)
        self._owned: set[_UnitKey] = set()

    async def claim_next(self, job_id: str, worker_id: str) -> WorkUnit | None:
        await self._slots.acquire()
        unit, error, cancelled = await _resolve_durable_operation(
            self._repository.claim_ready_unit(job_id, worker_id)
        )
        if error is not None:
            self._slots.release()
            if cancelled:
                raise asyncio.CancelledError from error
            raise error
        if unit is None:
            self._slots.release()
        else:
            key = (job_id, unit.unit_id)
            if key in self._owned:
                self._slots.release()
                raise RuntimeError("repository returned an already-owned work unit")
            self._owned.add(key)
        if cancelled:
            raise asyncio.CancelledError
        return unit

    async def complete(self, job_id: str, executed: _ExecutedUnit) -> None:
        key = (job_id, executed.outcome.unit_id)
        if key not in self._owned:
            raise ValueError("work unit is not outstanding for this job")
        _, error, cancelled = await _resolve_durable_operation(
            self._repository.complete_unit(
                job_id,
                executed.outcome,
                outcome_ref=executed.outcome_ref,
            )
        )
        if error is not None:
            if cancelled:
                raise asyncio.CancelledError from error
            raise error
        self._owned.remove(key)
        self._slots.release()
        if cancelled:
            raise asyncio.CancelledError


class BudgetExhausted(RuntimeError):
    """No further provider attempt fits within the durable job's conservative budget."""


class CleanupFailed(RuntimeError):
    """Evaluation cleanup failed without retaining its private exception details."""


class EvaluationReplyFactory(Protocol):
    """Create a lease-bound reply without exposing the credential value."""

    max_output_tokens: int

    def bind(
        self,
        lease: CredentialLease,
        model: str,
        attempt_sink: AttemptSink,
    ) -> ChatReplyPort: ...


class BudgetLedger:
    """Atomically reserve conservative request and output-token capacity per stream."""

    def __init__(self, budget: EvaluationBudget, token_allowance: int) -> None:
        if isinstance(token_allowance, bool) or not isinstance(token_allowance, int):
            raise TypeError("token_allowance must be an integer")
        if token_allowance < 1:
            raise ValueError("token_allowance must be positive")
        self._budget = budget
        self._token_allowance = token_allowance
        self._provider_requests = 0
        self._reserved_tokens = 0
        self._exhausted = False
        self._lock = asyncio.Lock()

    @property
    def exhausted(self) -> bool:
        return self._exhausted

    async def reserve_attempt(self) -> None:
        """Reserve before the underlying provider stream is created or iterated."""

        async with self._lock:
            next_requests = self._provider_requests + 1
            next_tokens = self._reserved_tokens + self._token_allowance
            if (
                next_requests > self._budget.max_provider_requests
                or next_tokens > self._budget.max_total_tokens
            ):
                self._exhausted = True
                raise BudgetExhausted("evaluation budget is exhausted")
            self._provider_requests = next_requests
            self._reserved_tokens = next_tokens
            self._exhausted = (
                self._provider_requests >= self._budget.max_provider_requests
                or self._reserved_tokens + self._token_allowance > self._budget.max_total_tokens
            )


class BudgetedChatReplyPort:
    """Delay provider stream creation until one conservative budget reservation succeeds."""

    def __init__(self, reply: ChatReplyPort, ledger: BudgetLedger) -> None:
        self._reply = reply
        self._ledger = ledger

    def stream_reply(
        self,
        request: ChatMessageRequest,
        context: GenerationContext,
    ) -> AsyncIterator[str | ChatReplyChunk]:
        return _BudgetedReplyStream(self._reply, self._ledger, request, context)


class _BudgetedReplyStream(AsyncIterator[str | ChatReplyChunk]):
    def __init__(
        self,
        reply: ChatReplyPort,
        ledger: BudgetLedger,
        request: ChatMessageRequest,
        context: GenerationContext,
    ) -> None:
        self._reply = reply
        self._ledger = ledger
        self._request = request
        self._context = context
        self._stream: AsyncIterator[str | ChatReplyChunk] | None = None

    def __aiter__(self) -> _BudgetedReplyStream:
        return self

    async def __anext__(self) -> str | ChatReplyChunk:
        if self._stream is None:
            await self._ledger.reserve_attempt()
            self._stream = self._reply.stream_reply(self._request, self._context)
        return await anext(self._stream)

    async def aclose(self) -> None:
        if self._stream is None:
            return
        close = getattr(self._stream, "aclose", None)
        if close is not None:
            await close()


class EvaluationJobRunner:
    """Run one durable job with either dynamic pull lanes or fixed workflow shards."""

    def __init__(
        self,
        *,
        registry: PluginRegistry,
        repository: RunnerRepository,
        credential_pool: CredentialLeasingPool,
        artifact_store: FilesystemEvaluationArtifactStore,
        scratch_root: Path,
        reply_factory: EvaluationReplyFactory | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        retry_backoff_base_seconds: float = 1.0,
        retry_backoff_max_seconds: float = 30.0,
    ) -> None:
        self._registry = registry
        self._repository = repository
        self._credential_pool = credential_pool
        self._artifact_store = artifact_store
        self._scratch_root = scratch_root
        self._reply_factory = reply_factory or MistralEvaluationReplyFactory()
        self._sleeper = sleeper or asyncio.sleep
        self._retry_backoff_base_seconds = _positive_delay(
            retry_backoff_base_seconds, "retry_backoff_base_seconds"
        )
        self._retry_backoff_max_seconds = _positive_delay(
            retry_backoff_max_seconds, "retry_backoff_max_seconds"
        )
        if self._retry_backoff_base_seconds > self._retry_backoff_max_seconds:
            raise ValueError("retry backoff base cannot exceed its maximum")
        allowance = self._reply_factory.max_output_tokens
        if isinstance(allowance, bool) or not isinstance(allowance, int) or allowance < 1:
            raise ValueError("reply factory max_output_tokens must be a positive integer")

    async def run(self, job_id: str) -> None:
        """Execute one queued job and leave an honest terminal state and manifest."""

        job = await self._require_job(job_id)
        if job.state in _TERMINAL_STATES:
            return
        plugin = self._registry.require(job.request.evaluation_type)
        if await self._is_cancel_requested(job.job_id):
            await self._finish_cancelled(job, plugin, None)
            return
        try:
            plan = await self._preflight(plugin, job.request)
            units = await self._durable_work_units(job, plan)
        except BaseException:
            await self._finish_unplanned_failure(job)
            return
        job = await self._require_job(job_id)
        if job.state is JobState.CANCELLATION_REQUESTED:
            await self._finish_cancelled(job, plugin, plan)
            return
        if job.state is JobState.COLLECTING:
            await self._finish_collected(
                job,
                plugin,
                plan,
                units,
                await self._durable_outcomes(job.job_id),
                (),
            )
            return
        if job.state is JobState.QUEUED:
            try:
                job = await self._repository.transition_job(job.job_id, JobState.RUNNING)
            except InvalidStateTransition:
                cancelled = await self._require_job(job.job_id)
                if cancelled.state is JobState.CANCELLATION_REQUESTED:
                    await self._finish_cancelled(cancelled, plugin, plan)
                    return
                raise
        ledger = BudgetLedger(job.request.budget, self._reply_factory.max_output_tokens)
        if job.request.execution_mode.value == "request_batch":
            cleanup_warnings = await self._run_pull_lanes(job, plugin, plan, ledger)
        else:
            cleanup_warnings = await self._run_fixed_shards(job, plugin, plan, units, ledger)
        additions = tuple(warning for warning in cleanup_warnings if warning not in job.warnings)
        if additions:
            job = await self._repository.append_warnings(job.job_id, additions)
        outcomes = await self._durable_outcomes(job.job_id)
        if await self._is_cancel_requested(job.job_id):
            await self._finish_cancelled(job, plugin, plan)
            return
        try:
            await self._finish_collected(
                job,
                plugin,
                plan,
                units,
                outcomes,
                cleanup_warnings,
            )
        except InvalidStateTransition:
            cancelled = await self._require_job(job.job_id)
            if cancelled.state is JobState.CANCELLATION_REQUESTED:
                await self._finish_cancelled(cancelled, plugin, plan)
                return
            raise

    async def _run_pull_lanes(
        self,
        job: EvaluationJob,
        plugin: EvaluationPlugin,
        plan: PluginPlan,
        ledger: BudgetLedger,
    ) -> tuple[EvaluationWarning, ...]:
        queue = _DurableOutcomeQueue(self._repository, job.effective_workers)
        lane_tasks = tuple(
            asyncio.create_task(
                self._run_pull_lane(job, plugin, plan, queue, ledger, f"lane-{index + 1}")
            )
            for index in range(job.effective_workers)
        )
        completed = await asyncio.gather(*lane_tasks, return_exceptions=True)
        warnings: list[EvaluationWarning] = []
        for result in completed:
            if isinstance(result, BaseException):
                continue
            warnings.extend(result)
        return tuple(warnings)

    async def _run_pull_lane(
        self,
        job: EvaluationJob,
        plugin: EvaluationPlugin,
        plan: PluginPlan,
        queue: _DurableOutcomeQueue,
        ledger: BudgetLedger,
        lane_id: str,
    ) -> tuple[EvaluationWarning, ...]:
        warnings: list[EvaluationWarning] = []
        try:
            lease = await self._credential_pool.lease()
        except RuntimeError:
            return ()
        lane_stopped = asyncio.Event()
        try:
            while not lane_stopped.is_set() and not ledger.exhausted:
                if await self._is_cancel_requested(job.job_id):
                    break
                unit = await queue.claim_next(job.job_id, lane_id)
                if unit is None:
                    break
                try:
                    executed = await self._execute_claimed_unit(
                        job,
                        plugin,
                        plan,
                        unit,
                        lease,
                        lane_id,
                        ledger,
                        lane_stopped,
                    )
                except BaseException:
                    cancellation_requested = await self._is_cancel_requested(job.job_id)
                    executed = _ExecutedUnit(
                        _cancelled_outcome(unit)
                        if cancellation_requested
                        else _failed_outcome(unit),
                        None,
                    )
                await queue.complete(job.job_id, executed)
                warnings.extend(executed.warnings)
        finally:
            await lease.release()
        return tuple(warnings)

    async def _run_fixed_shards(
        self,
        job: EvaluationJob,
        plugin: EvaluationPlugin,
        plan: PluginPlan,
        units: tuple[WorkUnit, ...],
        ledger: BudgetLedger,
    ) -> tuple[EvaluationWarning, ...]:
        shards = DataSharder().partition(units, job.effective_workers)
        lane_tasks = tuple(
            asyncio.create_task(
                self._run_fixed_lane(
                    job,
                    plugin,
                    plan,
                    tuple(item.value for item in shard),
                    ledger,
                    f"lane-{index + 1}",
                )
            )
            for index, shard in enumerate(shards)
        )
        completed = await asyncio.gather(*lane_tasks, return_exceptions=True)
        warnings: list[EvaluationWarning] = []
        for result in completed:
            if isinstance(result, BaseException):
                continue
            warnings.extend(result)
        return tuple(warnings)

    async def _run_fixed_lane(
        self,
        job: EvaluationJob,
        plugin: EvaluationPlugin,
        plan: PluginPlan,
        assigned_units: tuple[WorkUnit, ...],
        ledger: BudgetLedger,
        lane_id: str,
    ) -> tuple[EvaluationWarning, ...]:
        warnings: list[EvaluationWarning] = []
        try:
            lease = await self._credential_pool.lease()
        except RuntimeError:
            return ()
        lane_stopped = asyncio.Event()
        try:
            for assigned in assigned_units:
                if lane_stopped.is_set() or ledger.exhausted:
                    break
                if await self._is_cancel_requested(job.job_id):
                    break
                unit = await self._repository.claim_ready_unit_by_id(
                    job.job_id,
                    assigned.unit_id,
                    lane_id,
                )
                if unit is None:
                    continue
                try:
                    executed = await self._execute_claimed_unit(
                        job,
                        plugin,
                        plan,
                        unit,
                        lease,
                        lane_id,
                        ledger,
                        lane_stopped,
                    )
                except BaseException:
                    cancellation_requested = await self._is_cancel_requested(job.job_id)
                    executed = _ExecutedUnit(
                        _cancelled_outcome(unit)
                        if cancellation_requested
                        else _failed_outcome(unit),
                        None,
                    )
                await self._repository.complete_unit(
                    job.job_id,
                    executed.outcome,
                    outcome_ref=executed.outcome_ref,
                )
                warnings.extend(executed.warnings)
        finally:
            await lease.release()
        return tuple(warnings)

    async def _execute_claimed_unit(
        self,
        job: EvaluationJob,
        plugin: EvaluationPlugin,
        plan: PluginPlan,
        unit: WorkUnit,
        lease: CredentialLease,
        lane_id: str,
        ledger: BudgetLedger,
        lane_stopped: asyncio.Event,
    ) -> _ExecutedUnit:
        unit_warnings: list[EvaluationWarning] = []
        for attempt_number in range(1, job.request.max_attempts_per_unit + 1):
            if await self._is_cancel_requested(job.job_id):
                return _ExecutedUnit(_cancelled_outcome(unit), None)
            if ledger.exhausted:
                return await self._fail_budget_exhausted_unit(job, unit, lane_id, lease.alias)
            attempt = await self._repository.start_attempt(
                job.job_id,
                unit.unit_id,
                lane_id,
                lease.alias,
            )
            scratch_dir = self._create_scratch_dir(job.job_id, lane_id, attempt.attempt_id)
            attempt_id = attempt.attempt_id
            event_ordinal = 0
            ambiguous_provider_outcome = False
            provider_retry_after = 0
            credential_cooling = False

            async def observe(
                event: ProviderAttemptEvent,
                *,
                observed_attempt_id: str = attempt_id,
            ) -> None:
                nonlocal ambiguous_provider_outcome, credential_cooling
                nonlocal event_ordinal, provider_retry_after
                state = _step_state_for(event.outcome)
                await self._repository.write_step(
                    job_id=job.job_id,
                    unit_id=unit.unit_id,
                    worker_id=lane_id,
                    attempt_id=observed_attempt_id,
                    step_id=event.request_attempt_id,
                    ordinal=event_ordinal,
                    state=state,
                    safe_metadata={
                        "outcome": event.outcome,
                        "status_code": 0 if event.status_code is None else event.status_code,
                        "retry_after_seconds": (
                            0 if event.retry_after_seconds is None else event.retry_after_seconds
                        ),
                        "latency_ms": event.latency_ms,
                    },
                )
                event_ordinal += 1
                if event.outcome in _AMBIGUOUS_PROVIDER_OUTCOMES:
                    ambiguous_provider_outcome = True
                if event.outcome == "rate_limited":
                    credential_cooling = True
                    provider_retry_after = max(
                        provider_retry_after,
                        event.retry_after_seconds or 0,
                    )
                    await lease.hold_cooldown(event.retry_after_seconds or 0)
                elif event.outcome == "authentication_failed":
                    await lease.disable()
                    lane_stopped.set()

            context: WorkContext | None = None
            outcome: WorkUnitOutcome | None = None
            error: BaseException | None = None
            try:
                context = WorkContext(
                    job_id=job.job_id,
                    attempt_id=attempt.attempt_id,
                    lane_id=lane_id,
                    credential_alias=lease.alias,
                    plugin_plan=plan,
                    provider_client=BudgetedChatReplyPort(
                        self._reply_factory.bind(lease, job.request.target_model, observe),
                        ledger,
                    ),
                    scratch_dir=scratch_dir,
                )
                outcome = await plugin.execute_work(unit, context)
                _validate_outcome(unit, outcome)
            except BaseException as raised:
                error = raised
            cleanup = await self._cleanup(plugin, context, scratch_dir)
            unit_warnings.extend(cleanup.warnings)
            if cleanup.error is not None and error is None:
                error = (
                    cleanup.error
                    if isinstance(cleanup.error, asyncio.CancelledError)
                    else CleanupFailed()
                )
            if ambiguous_provider_outcome:
                await self._repository.finish_attempt(
                    attempt.attempt_id,
                    AttemptState.UNKNOWN,
                    FailureClass.UNKNOWN,
                    worker_id=lane_id,
                )
                return _ExecutedUnit(_failed_outcome(unit), None, tuple(unit_warnings))
            if error is None:
                assert outcome is not None
                outcome_ref: str | None = None
                if outcome.state is UnitState.SUCCEEDED:
                    try:
                        outcome_ref = self._artifact_store.write_private_details(
                            job.job_id,
                            f"{unit.unit_id}-{attempt.attempt_id}-outcome",
                            outcome.private_result,
                        )
                    except BaseException as raised:
                        error = raised
            if error is None:
                assert outcome is not None
                await self._repository.finish_attempt(
                    attempt.attempt_id,
                    AttemptState.SUCCEEDED,
                    worker_id=lane_id,
                )
                if await self._is_cancel_requested(job.job_id):
                    return _ExecutedUnit(
                        _cancelled_outcome(unit), None, tuple(unit_warnings)
                    )
                if credential_cooling:
                    lane_stopped.set()
                return _ExecutedUnit(outcome, outcome_ref, tuple(unit_warnings))

            classification = _classify(plugin, error)
            cancellation_requested = await self._is_cancel_requested(job.job_id)
            cancelled = isinstance(error, asyncio.CancelledError) or cancellation_requested
            await self._repository.finish_attempt(
                attempt.attempt_id,
                AttemptState.CANCELLED if cancelled else AttemptState.FAILED,
                None if cancelled else classification.failure_class,
                worker_id=lane_id,
            )
            if (
                classification.credential_state is CredentialState.COOLING_DOWN
                and not credential_cooling
            ):
                await lease.hold_cooldown(provider_retry_after)
                credential_cooling = True
            elif classification.credential_state is CredentialState.DISABLED:
                await lease.disable()
                lane_stopped.set()
            if cancelled:
                return _ExecutedUnit(_cancelled_outcome(unit), None, tuple(unit_warnings))
            if (
                classification.retryable
                and attempt_number < job.request.max_attempts_per_unit
                and not lane_stopped.is_set()
                and not ledger.exhausted
            ):
                await self._sleeper(
                    self._retry_delay(attempt_number, provider_retry_after)
                )
                if await self._is_cancel_requested(job.job_id):
                    return _ExecutedUnit(
                        _cancelled_outcome(unit), None, tuple(unit_warnings)
                    )
                continue
            if credential_cooling:
                lane_stopped.set()
            return _ExecutedUnit(_failed_outcome(unit), None, tuple(unit_warnings))
        return _ExecutedUnit(_failed_outcome(unit), None, tuple(unit_warnings))

    async def _fail_budget_exhausted_unit(
        self,
        job: EvaluationJob,
        unit: WorkUnit,
        lane_id: str,
        credential_alias: str,
    ) -> _ExecutedUnit:
        """Persist a generic failure without constructing a provider reply."""

        attempt = await self._repository.start_attempt(
            job.job_id,
            unit.unit_id,
            lane_id,
            credential_alias,
        )
        await self._repository.finish_attempt(
            attempt.attempt_id,
            AttemptState.FAILED,
            FailureClass.EVALUATION,
            worker_id=lane_id,
        )
        return _ExecutedUnit(_failed_outcome(unit), None)

    async def _finish_collected(
        self,
        job: EvaluationJob,
        plugin: EvaluationPlugin,
        plan: PluginPlan,
        units: tuple[WorkUnit, ...],
        outcomes: tuple[WorkUnitOutcome, ...],
        cleanup_warnings: tuple[EvaluationWarning, ...],
    ) -> None:
        if job.state is JobState.COLLECTING:
            collecting = job
        else:
            try:
                collecting = await self._repository.transition_job(
                    job.job_id,
                    JobState.COLLECTING,
                )
            except InvalidStateTransition:
                cancelled = await self._require_job(job.job_id)
                if cancelled.state is JobState.CANCELLATION_REQUESTED:
                    await self._finish_cancelled(cancelled, plugin, plan)
                    return
                raise
        ordered = tuple(sorted(outcomes, key=lambda outcome: (outcome.ordinal, outcome.unit_id)))
        succeeded = sum(outcome.state is UnitState.SUCCEEDED for outcome in ordered)
        if (
            succeeded == len(units)
            and len(ordered) == len(units)
            and not cleanup_warnings
        ):
            terminal = JobState.SUCCEEDED
        elif succeeded:
            terminal = JobState.PARTIALLY_SUCCEEDED
        else:
            terminal = JobState.FAILED
        try:
            bundle = plugin.aggregate(plan, ordered)
            _validate_bundle(bundle)
            self._artifact_store.write_manifest(
                collecting.job_id,
                _plain_public_result(bundle.public_result),
            )
        except BaseException:
            terminal = JobState.FAILED
            self._artifact_store.write_manifest(collecting.job_id, {"state": "failed"})
        latest = await self._require_job(collecting.job_id)
        if latest.state is JobState.CANCELLATION_REQUESTED:
            await self._finish_cancelled(latest, plugin, plan)
            return
        await self._repository.transition_job(collecting.job_id, terminal)

    async def _finish_cancelled(
        self,
        job: EvaluationJob,
        plugin: EvaluationPlugin,
        plan: PluginPlan | None,
    ) -> None:
        if plan is not None:
            try:
                outcomes = await self._durable_outcomes(job.job_id)
                bundle = plugin.aggregate(
                    plan,
                    tuple(sorted(outcomes, key=lambda outcome: (outcome.ordinal, outcome.unit_id))),
                )
                _validate_bundle(bundle)
                self._artifact_store.write_manifest(
                    job.job_id,
                    _plain_public_result(bundle.public_result),
                )
            except BaseException:
                self._artifact_store.write_manifest(job.job_id, {"state": "cancelled"})
        else:
            self._artifact_store.write_manifest(job.job_id, {"state": "cancelled"})
        latest = await self._require_job(job.job_id)
        if latest.state is JobState.CANCELLATION_REQUESTED:
            await self._repository.transition_job(job.job_id, JobState.CANCELLED)

    async def _finish_unplanned_failure(self, job: EvaluationJob) -> None:
        self._artifact_store.write_manifest(job.job_id, {"state": "failed"})
        latest = await self._require_job(job.job_id)
        if latest.state in {JobState.VALIDATING, JobState.QUEUED, JobState.RUNNING}:
            await self._repository.transition_job(job.job_id, JobState.FAILED)

    async def _preflight(self, plugin: EvaluationPlugin, request: EvaluationRequest) -> PluginPlan:
        plan = await plugin.preflight(request)
        if not isinstance(plan, PluginPlan) or plan.dataset_ref != request.dataset_ref:
            raise ValueError("evaluation preflight returned an invalid plan")
        return plan

    async def _durable_work_units(
        self, job: EvaluationJob, plan: PluginPlan
    ) -> tuple[WorkUnit, ...]:
        stored_units = await self._repository.list_units(job.job_id)
        units = tuple(
            WorkUnit(unit_id=unit.unit_id, ordinal=unit.ordinal, payload=unit.payload)
            for unit in stored_units
        )
        if (
            len(units) != plan.ready_work
            or tuple(unit.ordinal for unit in units) != tuple(range(len(units)))
            or len({unit.unit_id for unit in units}) != len(units)
        ):
            raise ValueError("durable evaluation work units are inconsistent with the plan")
        return units

    async def _durable_outcomes(self, job_id: str) -> tuple[WorkUnitOutcome, ...]:
        outcomes: list[WorkUnitOutcome] = []
        for unit in await self._repository.list_units(job_id):
            if unit.state not in {UnitState.SUCCEEDED, UnitState.FAILED, UnitState.CANCELLED}:
                continue
            private_result: object = None
            if unit.state is UnitState.SUCCEEDED:
                if unit.outcome_ref is None:
                    raise ValueError("successful durable unit is missing its private outcome")
                private_result = self._artifact_store.read_private_details(unit.outcome_ref)
            outcomes.append(
                WorkUnitOutcome(
                    unit_id=unit.unit_id,
                    ordinal=unit.ordinal,
                    state=unit.state,
                    provider_requests=unit.provider_requests,
                    total_tokens=unit.total_tokens,
                    private_result=private_result,
                )
            )
        return tuple(outcomes)

    async def _is_cancel_requested(self, job_id: str) -> bool:
        job = await self._require_job(job_id)
        return job.state is JobState.CANCELLATION_REQUESTED

    async def _require_job(self, job_id: str) -> EvaluationJob:
        job = await self._repository.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def _create_scratch_dir(self, job_id: str, lane_id: str, attempt_id: str) -> Path:
        self._scratch_root.mkdir(parents=True, exist_ok=True)
        return Path(
            tempfile.mkdtemp(
                prefix=f"{job_id}-{lane_id}-{attempt_id}-",
                dir=self._scratch_root,
            )
        )

    def _retry_delay(self, failed_attempt_number: int, retry_after_seconds: int) -> float:
        exponential = self._retry_backoff_base_seconds * (2 ** (failed_attempt_number - 1))
        provider_delay = float(retry_after_seconds)
        requested = exponential if exponential >= provider_delay else provider_delay
        return (
            self._retry_backoff_max_seconds
            if requested > self._retry_backoff_max_seconds
            else requested
        )

    async def _cleanup(
        self,
        plugin: EvaluationPlugin,
        context: WorkContext | None,
        scratch_dir: Path,
    ) -> _CleanupResult:
        warnings: tuple[EvaluationWarning, ...] = ()
        error: BaseException | None = None
        try:
            if context is not None:
                outcome = await plugin.cleanup(context)
                if not isinstance(outcome, CleanupOutcome):
                    raise ValueError("evaluation plug-in returned an invalid cleanup outcome")
                warnings = outcome.warnings
        except BaseException as raised:
            error = raised
        finally:
            try:
                shutil.rmtree(scratch_dir)
            except BaseException as raised:
                if error is None:
                    error = raised
        return _CleanupResult(warnings, error)


def _step_state_for(outcome: str) -> StepState:
    if outcome == "succeeded":
        return StepState.SUCCEEDED
    if outcome == "cancelled":
        return StepState.SKIPPED
    return StepState.FAILED


def _positive_delay(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be numeric")
    delay = float(value)
    if delay <= 0:
        raise ValueError(f"{name} must be positive")
    return delay


def _validate_outcome(unit: WorkUnit, outcome: WorkUnitOutcome) -> None:
    if (
        not isinstance(outcome, WorkUnitOutcome)
        or outcome.unit_id != unit.unit_id
        or outcome.ordinal != unit.ordinal
        or outcome.state not in {UnitState.SUCCEEDED, UnitState.FAILED, UnitState.CANCELLED}
    ):
        raise ValueError("evaluation plug-in returned an invalid work outcome")


def _classify(plugin: EvaluationPlugin, error: BaseException) -> FailureClassification:
    if isinstance(error, BudgetExhausted | CleanupFailed | UnsafeArtifact):
        return FailureClassification(
            FailureClass.EVALUATION,
            retryable=False,
            credential_state=None,
        )
    try:
        classification = plugin.classify_failure(error)
    except BaseException:
        return FailureClassification(FailureClass.UNKNOWN, retryable=False, credential_state=None)
    if not isinstance(classification, FailureClassification):
        return FailureClassification(FailureClass.UNKNOWN, retryable=False, credential_state=None)
    return classification


def _validate_bundle(bundle: ArtifactBundle) -> None:
    if not isinstance(bundle, ArtifactBundle):
        raise ValueError("evaluation plug-in returned an invalid artifact bundle")


def _plain_public_result(value: Mapping[str, object]) -> Mapping[str, object]:
    return {key: _plain_public_value(item) for key, item in value.items()}


def _plain_public_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain_public_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_public_value(item) for item in value]
    return value


def _cancelled_outcome(unit: WorkUnit) -> WorkUnitOutcome:
    return WorkUnitOutcome(
        unit_id=unit.unit_id,
        ordinal=unit.ordinal,
        state=UnitState.CANCELLED,
        provider_requests=0,
        total_tokens=0,
        private_result=None,
    )


def _failed_outcome(unit: WorkUnit) -> WorkUnitOutcome:
    return WorkUnitOutcome(
        unit_id=unit.unit_id,
        ordinal=unit.ordinal,
        state=UnitState.FAILED,
        provider_requests=0,
        total_tokens=0,
        private_result=None,
    )
