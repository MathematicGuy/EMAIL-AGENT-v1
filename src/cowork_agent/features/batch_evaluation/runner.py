"""Budget-aware lane execution for durable Level 1 evaluation jobs."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from pathlib import Path
from typing import Protocol

from cowork_agent.domain.chat_contracts import ChatMessageRequest
from cowork_agent.features.ai_chat.generation_context import GenerationContext
from cowork_agent.features.ai_chat.ports import ChatReplyChunk, ChatReplyPort
from cowork_agent.features.batch_evaluation.artifacts import FilesystemEvaluationArtifactStore
from cowork_agent.features.batch_evaluation.contracts import (
    ArtifactBundle,
    AttemptState,
    CredentialState,
    EvaluationBudget,
    EvaluationPlugin,
    EvaluationRequest,
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
from cowork_agent.features.batch_evaluation.queue import DurableWorkUnitQueue
from cowork_agent.features.batch_evaluation.registry import PluginRegistry
from cowork_agent.integrations.llm.evaluation_mistral import MistralEvaluationReplyFactory
from cowork_agent.persistence.repositories.evaluation_jobs import (
    EvaluationJob,
    InvalidStateTransition,
    SQLiteEvaluationJobRepository,
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


class BudgetExhausted(RuntimeError):
    """No further provider attempt fits within the durable job's conservative budget."""


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
        repository: SQLiteEvaluationJobRepository,
        credential_pool: CredentialLeasingPool,
        artifact_store: FilesystemEvaluationArtifactStore,
        scratch_root: Path,
        reply_factory: EvaluationReplyFactory | None = None,
    ) -> None:
        self._registry = registry
        self._repository = repository
        self._credential_pool = credential_pool
        self._artifact_store = artifact_store
        self._scratch_root = scratch_root
        self._reply_factory = reply_factory or MistralEvaluationReplyFactory()
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
            await self._finish_cancelled(job, plugin, None, ())
            return
        try:
            plan = await self._preflight(plugin, job.request)
            units = self._validated_units(plugin, plan, job)
        except BaseException:
            await self._finish_unplanned_failure(job)
            return
        job = await self._require_job(job_id)
        if job.state is JobState.CANCELLATION_REQUESTED:
            await self._finish_cancelled(job, plugin, plan, ())
            return
        if job.state is JobState.QUEUED:
            try:
                job = await self._repository.transition_job(job.job_id, JobState.RUNNING)
            except InvalidStateTransition:
                cancelled = await self._require_job(job.job_id)
                if cancelled.state is JobState.CANCELLATION_REQUESTED:
                    await self._finish_cancelled(cancelled, plugin, plan, ())
                    return
                raise
        ledger = BudgetLedger(job.request.budget, self._reply_factory.max_output_tokens)
        if job.request.execution_mode.value == "request_batch":
            outcomes = await self._run_pull_lanes(job, plugin, plan, units, ledger)
        else:
            outcomes = await self._run_fixed_shards(job, plugin, plan, units, ledger)
        if await self._is_cancel_requested(job.job_id):
            await self._finish_cancelled(job, plugin, plan, outcomes)
            return
        try:
            await self._finish_collected(job, plugin, plan, units, outcomes)
        except InvalidStateTransition:
            cancelled = await self._require_job(job.job_id)
            if cancelled.state is JobState.CANCELLATION_REQUESTED:
                await self._finish_cancelled(cancelled, plugin, plan, outcomes)
                return
            raise

    async def _run_pull_lanes(
        self,
        job: EvaluationJob,
        plugin: EvaluationPlugin,
        plan: PluginPlan,
        units: tuple[WorkUnit, ...],
        ledger: BudgetLedger,
    ) -> tuple[WorkUnitOutcome, ...]:
        del units
        queue = DurableWorkUnitQueue(self._repository, job.effective_workers)
        lane_tasks = tuple(
            asyncio.create_task(
                self._run_pull_lane(job, plugin, plan, queue, ledger, f"lane-{index + 1}")
            )
            for index in range(job.effective_workers)
        )
        completed = await asyncio.gather(*lane_tasks, return_exceptions=True)
        outcomes: list[WorkUnitOutcome] = []
        for result in completed:
            if isinstance(result, BaseException):
                continue
            outcomes.extend(result)
        return tuple(outcomes)

    async def _run_pull_lane(
        self,
        job: EvaluationJob,
        plugin: EvaluationPlugin,
        plan: PluginPlan,
        queue: DurableWorkUnitQueue,
        ledger: BudgetLedger,
        lane_id: str,
    ) -> tuple[WorkUnitOutcome, ...]:
        outcomes: list[WorkUnitOutcome] = []
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
                    outcome = await self._execute_claimed_unit(
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
                    outcome = (
                        _cancelled_outcome(unit)
                        if cancellation_requested
                        else _failed_outcome(unit)
                    )
                await queue.complete(job.job_id, outcome)
                outcomes.append(outcome)
        finally:
            await lease.release()
        return tuple(outcomes)

    async def _run_fixed_shards(
        self,
        job: EvaluationJob,
        plugin: EvaluationPlugin,
        plan: PluginPlan,
        units: tuple[WorkUnit, ...],
        ledger: BudgetLedger,
    ) -> tuple[WorkUnitOutcome, ...]:
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
        outcomes: list[WorkUnitOutcome] = []
        for result in completed:
            if isinstance(result, BaseException):
                continue
            outcomes.extend(result)
        return tuple(outcomes)

    async def _run_fixed_lane(
        self,
        job: EvaluationJob,
        plugin: EvaluationPlugin,
        plan: PluginPlan,
        assigned_units: tuple[WorkUnit, ...],
        ledger: BudgetLedger,
        lane_id: str,
    ) -> tuple[WorkUnitOutcome, ...]:
        outcomes: list[WorkUnitOutcome] = []
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
                    outcome = await self._execute_claimed_unit(
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
                    outcome = (
                        _cancelled_outcome(unit)
                        if cancellation_requested
                        else _failed_outcome(unit)
                    )
                await self._repository.complete_unit(job.job_id, outcome)
                outcomes.append(outcome)
        finally:
            await lease.release()
        return tuple(outcomes)

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
    ) -> WorkUnitOutcome:
        for attempt_number in range(1, job.request.max_attempts_per_unit + 1):
            if await self._is_cancel_requested(job.job_id):
                return _cancelled_outcome(unit)
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

            async def observe(
                event: ProviderAttemptEvent,
                *,
                observed_attempt_id: str = attempt_id,
            ) -> None:
                nonlocal event_ordinal
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
                if event.outcome == "rate_limited":
                    await lease.cool_down(event.retry_after_seconds or 0)
                    lane_stopped.set()
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
            cleanup_error = await self._cleanup(plugin, context, scratch_dir)
            if cleanup_error is not None and error is None:
                error = cleanup_error
            if error is None:
                assert outcome is not None
                await self._repository.finish_attempt(
                    attempt.attempt_id,
                    AttemptState.SUCCEEDED,
                    worker_id=lane_id,
                )
                if await self._is_cancel_requested(job.job_id):
                    return _cancelled_outcome(unit)
                return outcome

            classification = _classify(plugin, error)
            cancellation_requested = await self._is_cancel_requested(job.job_id)
            cancelled = isinstance(error, asyncio.CancelledError) or cancellation_requested
            await self._repository.finish_attempt(
                attempt.attempt_id,
                AttemptState.CANCELLED if cancelled else AttemptState.FAILED,
                None if cancelled else classification.failure_class,
                worker_id=lane_id,
            )
            if classification.credential_state is CredentialState.COOLING_DOWN:
                await lease.cool_down(0)
                lane_stopped.set()
            elif classification.credential_state is CredentialState.DISABLED:
                await lease.disable()
                lane_stopped.set()
            if cancelled:
                return _cancelled_outcome(unit)
            if (
                classification.retryable
                and attempt_number < job.request.max_attempts_per_unit
                and not lane_stopped.is_set()
                and not ledger.exhausted
            ):
                continue
            return _failed_outcome(unit)
        return _failed_outcome(unit)

    async def _fail_budget_exhausted_unit(
        self,
        job: EvaluationJob,
        unit: WorkUnit,
        lane_id: str,
        credential_alias: str,
    ) -> WorkUnitOutcome:
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
        return _failed_outcome(unit)

    async def _finish_collected(
        self,
        job: EvaluationJob,
        plugin: EvaluationPlugin,
        plan: PluginPlan,
        units: tuple[WorkUnit, ...],
        outcomes: tuple[WorkUnitOutcome, ...],
    ) -> None:
        try:
            collecting = await self._repository.transition_job(job.job_id, JobState.COLLECTING)
        except InvalidStateTransition:
            cancelled = await self._require_job(job.job_id)
            if cancelled.state is JobState.CANCELLATION_REQUESTED:
                await self._finish_cancelled(cancelled, plugin, plan, outcomes)
                return
            raise
        ordered = tuple(sorted(outcomes, key=lambda outcome: (outcome.ordinal, outcome.unit_id)))
        succeeded = sum(outcome.state is UnitState.SUCCEEDED for outcome in ordered)
        if succeeded == len(units) and len(ordered) == len(units):
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
            await self._finish_cancelled(latest, plugin, plan, outcomes)
            return
        await self._repository.transition_job(collecting.job_id, terminal)

    async def _finish_cancelled(
        self,
        job: EvaluationJob,
        plugin: EvaluationPlugin,
        plan: PluginPlan | None,
        outcomes: tuple[WorkUnitOutcome, ...],
    ) -> None:
        if plan is not None:
            try:
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

    def _validated_units(
        self, plugin: EvaluationPlugin, plan: PluginPlan, job: EvaluationJob
    ) -> tuple[WorkUnit, ...]:
        units = plugin.build_work_units(plan, job.effective_workers)
        if (
            not isinstance(units, tuple)
            or len(units) != plan.ready_work
            or any(not isinstance(unit, WorkUnit) for unit in units)
        ):
            raise ValueError("evaluation plug-in returned invalid work units")
        return units

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

    async def _cleanup(
        self,
        plugin: EvaluationPlugin,
        context: WorkContext | None,
        scratch_dir: Path,
    ) -> BaseException | None:
        try:
            if context is not None:
                await plugin.cleanup(context)
            shutil.rmtree(scratch_dir)
        except BaseException as error:
            return error
        return None


def _step_state_for(outcome: str) -> StepState:
    if outcome == "succeeded":
        return StepState.SUCCEEDED
    if outcome == "cancelled":
        return StepState.SKIPPED
    return StepState.FAILED


def _validate_outcome(unit: WorkUnit, outcome: WorkUnitOutcome) -> None:
    if (
        not isinstance(outcome, WorkUnitOutcome)
        or outcome.unit_id != unit.unit_id
        or outcome.ordinal != unit.ordinal
        or outcome.state not in {UnitState.SUCCEEDED, UnitState.FAILED, UnitState.CANCELLED}
    ):
        raise ValueError("evaluation plug-in returned an invalid work outcome")


def _classify(plugin: EvaluationPlugin, error: BaseException) -> FailureClassification:
    if isinstance(error, BudgetExhausted):
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
