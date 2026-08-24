from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from pathlib import Path

import pytest

from cowork_agent.features.batch_evaluation.artifacts import FilesystemEvaluationArtifactStore
from cowork_agent.features.batch_evaluation.contracts import (
    ArtifactBundle,
    AttemptState,
    CleanupOutcome,
    CredentialState,
    EvaluationBudget,
    EvaluationRequest,
    EvaluationWarning,
    ExecutionMode,
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
from cowork_agent.features.batch_evaluation.credentials import CredentialLeasingPool
from cowork_agent.features.batch_evaluation.queue import DurableWorkUnitQueue
from cowork_agent.features.batch_evaluation.registry import PluginRegistry
from cowork_agent.features.batch_evaluation.runner import (
    BudgetedChatReplyPort,
    BudgetExhausted,
    BudgetLedger,
    EvaluationJobRunner,
)
from cowork_agent.features.batch_evaluation.service import EvaluationJobService
from cowork_agent.features.batch_evaluation.supervisor import EvaluationSupervisor
from cowork_agent.persistence.repositories.evaluation_jobs import SQLiteEvaluationJobRepository

AttemptSink = Callable[[ProviderAttemptEvent], Awaitable[None] | None]


class FakeReply:
    def __init__(self, sink: AttemptSink, alias: str, factory: FakeReplyFactory) -> None:
        self._sink = sink
        self._alias = alias
        self._factory = factory

    def stream_reply(self, request: object, context: object) -> AsyncIterator[str]:
        del request, context

        async def stream() -> AsyncIterator[str]:
            self._factory.provider_calls += 1
            outcome, status_code, retry_after_seconds = self._factory.attempt_metadata(
                self._alias,
                self._factory.provider_calls,
            )
            event = ProviderAttemptEvent(
                credential_alias=self._alias,
                request_attempt_id=f"provider-{self._factory.provider_calls}",
                outcome=outcome,
                status_code=status_code,
                retry_after_seconds=retry_after_seconds,
                latency_ms=0,
            )
            result = self._sink(event)
            if result is not None:
                await result
            if event.outcome in self._factory.raising_outcomes:
                raise ProviderFailure()
            yield "private reply"

        return stream()


class FakeReplyFactory:
    def __init__(
        self,
        *,
        max_output_tokens: int = 10,
        outcomes: Mapping[str, str] | None = None,
        scripted_events: Sequence[tuple[str, int | None]] = (),
        raising_outcomes: frozenset[str] = frozenset({"timed_out"}),
    ) -> None:
        self.max_output_tokens = max_output_tokens
        self._outcomes = dict(outcomes or {})
        self._scripted_events = tuple(scripted_events)
        self.raising_outcomes = raising_outcomes
        self.provider_calls = 0
        self.bound_aliases: list[str] = []
        self.bound_settled: list[bool] = []
        self.bound_active: list[bool] = []
        self.bound_states: list[CredentialState] = []

    def bind(self, lease: object, model: str, attempt_sink: AttemptSink) -> FakeReply:
        del model
        alias = lease.alias
        self.bound_aliases.append(alias)
        self.bound_settled.append(lease._settled)
        self.bound_active.append(lease._record.active_lease is lease)
        self.bound_states.append(lease._record.state)
        return FakeReply(attempt_sink, alias, self)

    def attempt_metadata(
        self, alias: str, attempt_number: int
    ) -> tuple[str, int | None, int | None]:
        if attempt_number <= len(self._scripted_events):
            outcome, retry_after = self._scripted_events[attempt_number - 1]
        else:
            outcome = self._outcomes.get(alias, "succeeded")
            retry_after = None
        status = (
            429
            if outcome == "rate_limited"
            else 503 if outcome == "provider_unavailable" else None
        )
        return outcome, status, retry_after


class ProviderFailure(RuntimeError):
    pass


class CancellationTrackingRepository(SQLiteEvaluationJobRepository):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.cancellation_committed = asyncio.Event()
        self.events: list[str] = []
        self.step_states: list[StepState] = []
        self.completed_units: list[str] = []

    async def request_cancellation(self, job_id: str):  # type: ignore[no-untyped-def]
        job = await super().request_cancellation(job_id)
        self.events.append("cancellation_committed")
        self.cancellation_committed.set()
        return job

    async def write_step(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        step = await super().write_step(*args, **kwargs)  # type: ignore[arg-type]
        self.step_states.append(step.state)
        return step

    async def complete_unit(
        self,
        job_id: str,
        outcome: WorkUnitOutcome,
        *,
        outcome_ref: str | None = None,
    ) -> None:
        await super().complete_unit(job_id, outcome, outcome_ref=outcome_ref)
        self.completed_units.append(outcome.unit_id)


class ClaimGateRepository(CancellationTrackingRepository):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.claim_committed = asyncio.Event()
        self.return_claim = asyncio.Event()

    async def claim_ready_unit(self, job_id: str, worker_id: str) -> WorkUnit | None:
        unit = await super().claim_ready_unit(job_id, worker_id)
        if unit is not None:
            self.claim_committed.set()
            await self.return_claim.wait()
        return unit


class BlockingAttemptReplyFactory(FakeReplyFactory):
    def __init__(
        self,
        repository: CancellationTrackingRepository,
        *,
        expected_running: int = 1,
    ) -> None:
        super().__init__(max_output_tokens=1, raising_outcomes=frozenset())
        self._repository = repository
        self._expected_running = expected_running
        self._running_attempts = 0
        self.attempt_running = asyncio.Event()
        self.release_attempt = asyncio.Event()

    def bind(self, lease: object, model: str, attempt_sink: AttemptSink) -> object:
        del model
        self.bound_aliases.append(lease.alias)

        class Reply:
            def stream_reply(inner_self, request: object, context: object) -> AsyncIterator[str]:
                del inner_self, request, context

                async def stream() -> AsyncIterator[str]:
                    self.provider_calls += 1
                    observed = attempt_sink(
                        ProviderAttemptEvent(
                            credential_alias=lease.alias,
                            request_attempt_id="provider-running",
                            outcome="running",
                            status_code=None,
                            retry_after_seconds=None,
                            latency_ms=0,
                        )
                    )
                    if observed is not None:
                        await observed
                    self._running_attempts += 1
                    if self._running_attempts == self._expected_running:
                        self.attempt_running.set()
                    try:
                        await self.release_attempt.wait()
                    except asyncio.CancelledError:
                        self._repository.events.append("attempt_cancelled")
                        raise
                    yield "private reply"

                return stream()

        return Reply()


class RequestBatchPlugin:
    evaluation_type = "request-eval"
    version = "1"
    supported_modes = frozenset({ExecutionMode.REQUEST_BATCH})
    parameter_schema: Mapping[str, object] = {"type": "object"}

    def __init__(
        self,
        work_count: int,
        *,
        block_ordinal: int | None = None,
        fail_ordinals: frozenset[int] = frozenset(),
        cleanup_fails: bool = False,
        cleanup_warns: bool = False,
        provider_calls_per_unit: int = 2,
    ) -> None:
        self.work_count = work_count
        self.block_ordinal = block_ordinal
        self.fail_ordinals = fail_ordinals
        self.cleanup_fails = cleanup_fails
        self.cleanup_warns = cleanup_warns
        self.provider_calls_per_unit = provider_calls_per_unit
        self.started = tuple(asyncio.Event() for _ in range(work_count))
        self.finished = tuple(asyncio.Event() for _ in range(work_count))
        self.release_blocked = asyncio.Event()
        self.executions: list[tuple[str, int]] = []
        self.finish_order: list[int] = []
        self.aggregate_outcomes: tuple[WorkUnitOutcome, ...] = ()
        self.aggregate_private_results: tuple[object, ...] = ()
        self.scratch_dirs: list[Path] = []
        self.cleanup_calls = 0

    async def preflight(self, request: EvaluationRequest) -> PluginPlan:
        return PluginPlan(request.dataset_ref, self.work_count, object())

    def build_work_units(self, plan: PluginPlan, lane_count: int) -> tuple[WorkUnit, ...]:
        del plan, lane_count
        return tuple(
            WorkUnit(
                unit_id=f"unit-{ordinal}",
                ordinal=ordinal,
                payload={"case_id": f"case-{ordinal}"},
            )
            for ordinal in range(self.work_count)
        )

    async def execute_work(self, unit: WorkUnit, context: WorkContext) -> WorkUnitOutcome:
        self.scratch_dirs.append(context.scratch_dir)
        self.started[unit.ordinal].set()
        self.executions.append((context.credential_alias, unit.ordinal))
        reply = context.provider_client
        for _ in range(self.provider_calls_per_unit):
            stream = reply.stream_reply(object(), object())
            assert [item async for item in stream] == ["private reply"]
        if self.block_ordinal == unit.ordinal:
            await self.release_blocked.wait()
        self.finish_order.append(unit.ordinal)
        if unit.ordinal in self.fail_ordinals:
            raise ProviderFailure()
        self.finished[unit.ordinal].set()
        return WorkUnitOutcome(
            unit_id=unit.unit_id,
            ordinal=unit.ordinal,
            state=UnitState.SUCCEEDED,
            provider_requests=2,
            total_tokens=2,
            private_result={"private": unit.unit_id},
        )

    def aggregate(
        self, plan: PluginPlan, outcomes: Sequence[WorkUnitOutcome]
    ) -> ArtifactBundle:
        del plan
        self.aggregate_outcomes = tuple(outcomes)
        self.aggregate_private_results = tuple(outcome.private_result for outcome in outcomes)
        return ArtifactBundle(
            public_result={"ordinals": tuple(outcome.ordinal for outcome in outcomes)},
            private_artifact_ids=(),
        )

    async def cleanup(self, context: WorkContext) -> CleanupOutcome:
        del context
        self.cleanup_calls += 1
        if self.cleanup_fails:
            raise RuntimeError("private cleanup failure")
        warnings = (
            EvaluationWarning(
                code="WORKER_COUNT_REDUCED",
                details={
                    "requested_workers": 2,
                    "healthy_credentials": 1,
                    "ready_work": 1,
                    "effective_workers": 1,
                },
            ),
        ) if self.cleanup_warns else ()
        return CleanupOutcome(removed_resources=1, warnings=warnings)

    def classify_failure(self, error: BaseException) -> FailureClassification:
        del error
        return FailureClassification(FailureClass.PROVIDER, retryable=False, credential_state=None)


class RetryableRequestBatchPlugin(RequestBatchPlugin):
    def classify_failure(self, error: BaseException) -> FailureClassification:
        del error
        return FailureClassification(FailureClass.PROVIDER, retryable=True, credential_state=None)


class NonJsonPrivateResultPlugin(RequestBatchPlugin):
    async def execute_work(self, unit: WorkUnit, context: WorkContext) -> WorkUnitOutcome:
        outcome = await super().execute_work(unit, context)
        return WorkUnitOutcome(
            unit_id=outcome.unit_id,
            ordinal=outcome.ordinal,
            state=outcome.state,
            provider_requests=outcome.provider_requests,
            total_tokens=outcome.total_tokens,
            private_result=object(),
        )


def request(
    *, workers: int, provider_requests: int, tokens: int, attempts: int = 1
) -> EvaluationRequest:
    return EvaluationRequest(
        evaluation_type="request-eval",
        provider="mistral",
        target_model="small-model",
        dataset_ref="dataset-v1",
        credential_pool="mistral-eval",
        execution_mode=ExecutionMode.REQUEST_BATCH,
        max_workers=workers,
        max_attempts_per_unit=attempts,
        budget=EvaluationBudget(provider_requests, tokens),
        parameters={},
    )


async def prepared_runner(
    tmp_path: Path,
    plugin: RequestBatchPlugin,
    factory: FakeReplyFactory,
    submitted_request: EvaluationRequest,
    key_count: int = 3,
    sleeper: Callable[[float], Awaitable[None]] | None = None,
    retry_backoff_base_seconds: float = 1.0,
    retry_backoff_max_seconds: float = 30.0,
    credential_pool: CredentialLeasingPool | None = None,
) -> tuple[EvaluationJobRunner, EvaluationJobService, SQLiteEvaluationJobRepository]:
    artifacts = FilesystemEvaluationArtifactStore(tmp_path / "artifacts")
    repository = SQLiteEvaluationJobRepository(tmp_path / "evaluation-jobs.db")
    await repository.initialize()
    registry = PluginRegistry()
    registry.register(plugin)
    keys = {"MISTRAL_API_KEY": "secret-one"}
    keys.update({f"MISTRAL_API_KEY{index}": f"secret-{index}" for index in range(2, key_count + 1)})
    pool = credential_pool or CredentialLeasingPool.from_env("MISTRAL_API_KEY", keys)
    service = EvaluationJobService(
        registry=registry,
        repository=repository,
        credential_pool=pool,
        artifact_store=artifacts,
    )
    runner = EvaluationJobRunner(
        registry=registry,
        repository=repository,
        credential_pool=pool,
        artifact_store=artifacts,
        scratch_root=tmp_path / "scratch",
        reply_factory=factory,
        sleeper=sleeper,
        retry_backoff_base_seconds=retry_backoff_base_seconds,
        retry_backoff_max_seconds=retry_backoff_max_seconds,
    )
    await service.submit(submitted_request, idempotency_key="job-key")
    return runner, service, repository


async def prepared_supervised_runner(
    tmp_path: Path,
    plugin: RequestBatchPlugin,
    factory: FakeReplyFactory,
    repository: CancellationTrackingRepository,
    pool: CredentialLeasingPool,
) -> tuple[EvaluationJobService, SQLiteEvaluationJobRepository]:
    artifacts = FilesystemEvaluationArtifactStore(tmp_path / "artifacts")
    await repository.initialize()
    registry = PluginRegistry()
    registry.register(plugin)
    runner = EvaluationJobRunner(
        registry=registry,
        repository=repository,
        credential_pool=pool,
        artifact_store=artifacts,
        scratch_root=tmp_path / "scratch",
        reply_factory=factory,
    )
    supervisor = EvaluationSupervisor(repository=repository, runner=runner)
    service = EvaluationJobService(
        registry=registry,
        repository=repository,
        credential_pool=pool,
        artifact_store=artifacts,
        supervisor=supervisor,
    )
    return service, repository


@pytest.mark.asyncio
async def test_request_batch_pulls_durably_and_aggregates_by_original_ordinal(
    tmp_path: Path,
) -> None:
    plugin = RequestBatchPlugin(4, block_ordinal=0)
    runner, service, _ = await prepared_runner(
        tmp_path,
        plugin,
        FakeReplyFactory(max_output_tokens=1),
        request(workers=2, provider_requests=20, tokens=20),
        key_count=2,
    )
    job = await service.submit(
        request(workers=2, provider_requests=20, tokens=20),
        idempotency_key="run-key",
    )

    running = asyncio.create_task(runner.run(job.job_id))
    await plugin.started[0].wait()
    await plugin.started[1].wait()
    await plugin.finished[3].wait()
    plugin.release_blocked.set()
    await running

    assert plugin.finish_order != [0, 1, 2, 3]
    assert [outcome.ordinal for outcome in plugin.aggregate_outcomes] == [0, 1, 2, 3]
    assert await service.get_result(job.job_id) == {"ordinals": [0, 1, 2, 3]}
    assert (await service.get_status(job.job_id))["state"] == "succeeded"


@pytest.mark.asyncio
async def test_success_writes_private_outcome_before_durable_completion(
    tmp_path: Path,
) -> None:
    plugin = RequestBatchPlugin(1)
    runner, service, repository = await prepared_runner(
        tmp_path,
        plugin,
        FakeReplyFactory(max_output_tokens=1),
        request(workers=1, provider_requests=4, tokens=4),
        key_count=1,
    )
    job = await service.submit(
        request(workers=1, provider_requests=4, tokens=4),
        idempotency_key="private-outcome-key",
    )

    await runner.run(job.job_id)

    stored = (await repository.list_units(job.job_id))[0]
    assert stored.provider_requests == 2
    assert stored.total_tokens == 2
    assert stored.outcome_ref is not None
    artifacts = FilesystemEvaluationArtifactStore(tmp_path / "artifacts")
    assert artifacts.read_private_details(stored.outcome_ref) == {"private": "unit-0"}
    public_result = await service.get_result(job.job_id)
    assert public_result == {"ordinals": [0]}
    assert "unit-0" not in repr(public_result)


@pytest.mark.asyncio
async def test_non_json_private_outcome_fails_without_a_durable_reference(tmp_path: Path) -> None:
    plugin = NonJsonPrivateResultPlugin(1)
    runner, service, repository = await prepared_runner(
        tmp_path,
        plugin,
        FakeReplyFactory(max_output_tokens=1),
        request(workers=1, provider_requests=4, tokens=4),
        key_count=1,
    )
    job = await service.submit(
        request(workers=1, provider_requests=4, tokens=4),
        idempotency_key="non-json-outcome-key",
    )

    await runner.run(job.job_id)

    stored = (await repository.list_units(job.job_id))[0]
    attempts = await repository.list_attempts(job.job_id, "unit-0")
    assert stored.state is UnitState.FAILED
    assert stored.outcome_ref is None
    assert attempts[0].failure_class is FailureClass.EVALUATION
    assert await service.get_result(job.job_id) == {"ordinals": [0]}


@pytest.mark.asyncio
async def test_restart_aggregates_a_completed_unit_without_reexecuting_it(
    tmp_path: Path,
) -> None:
    initial_plugin = RequestBatchPlugin(2)
    _, service, repository = await prepared_runner(
        tmp_path,
        initial_plugin,
        FakeReplyFactory(max_output_tokens=1),
        request(workers=1, provider_requests=10, tokens=10),
        key_count=1,
    )
    job = await service.submit(
        request(workers=1, provider_requests=10, tokens=10),
        idempotency_key="restart-key",
    )
    await repository.transition_job(job.job_id, JobState.RUNNING)
    completed_unit = await repository.claim_ready_unit(job.job_id, "crashed-lane")
    assert completed_unit is not None and completed_unit.ordinal == 0
    completed_outcome = WorkUnitOutcome(
        unit_id=completed_unit.unit_id,
        ordinal=completed_unit.ordinal,
        state=UnitState.SUCCEEDED,
        provider_requests=2,
        total_tokens=3,
        private_result={"private": "restored-unit-0"},
    )
    artifacts = FilesystemEvaluationArtifactStore(tmp_path / "artifacts")
    completed_ref = artifacts.write_private_details(
        job.job_id,
        "unit-0-crashed-attempt-outcome",
        completed_outcome.private_result,
    )
    await repository.complete_unit(job.job_id, completed_outcome, outcome_ref=completed_ref)

    restarted_plugin = RequestBatchPlugin(2)
    restarted_registry = PluginRegistry()
    restarted_registry.register(restarted_plugin)
    restarted_factory = FakeReplyFactory(max_output_tokens=1)
    restarted_runner = EvaluationJobRunner(
        registry=restarted_registry,
        repository=repository,
        credential_pool=CredentialLeasingPool.from_env(
            "MISTRAL_API_KEY", {"MISTRAL_API_KEY": "secret-one"}
        ),
        artifact_store=artifacts,
        scratch_root=tmp_path / "restarted-scratch",
        reply_factory=restarted_factory,
    )

    await restarted_runner.run(job.job_id)

    assert [ordinal for _, ordinal in restarted_plugin.executions] == [1]
    assert [outcome.ordinal for outcome in restarted_plugin.aggregate_outcomes] == [0, 1]
    assert restarted_plugin.aggregate_private_results[0] == {"private": "restored-unit-0"}
    assert restarted_factory.provider_calls == 2


@pytest.mark.asyncio
async def test_collecting_restart_aggregates_all_durable_outcomes_without_execution(
    tmp_path: Path,
) -> None:
    initial_plugin = RequestBatchPlugin(1)
    _, service, repository = await prepared_runner(
        tmp_path,
        initial_plugin,
        FakeReplyFactory(max_output_tokens=1),
        request(workers=1, provider_requests=4, tokens=4),
        key_count=1,
    )
    job = await service.submit(
        request(workers=1, provider_requests=4, tokens=4),
        idempotency_key="collecting-restart-key",
    )
    await repository.transition_job(job.job_id, JobState.RUNNING)
    completed_unit = await repository.claim_ready_unit(job.job_id, "crashed-lane")
    assert completed_unit is not None
    completed_outcome = WorkUnitOutcome(
        unit_id=completed_unit.unit_id,
        ordinal=completed_unit.ordinal,
        state=UnitState.SUCCEEDED,
        provider_requests=1,
        total_tokens=1,
        private_result={"private": "collecting-result"},
    )
    artifacts = FilesystemEvaluationArtifactStore(tmp_path / "artifacts")
    outcome_ref = artifacts.write_private_details(
        job.job_id,
        "unit-0-collecting-outcome",
        completed_outcome.private_result,
    )
    await repository.complete_unit(job.job_id, completed_outcome, outcome_ref=outcome_ref)
    await repository.transition_job(job.job_id, JobState.COLLECTING)

    restarted_plugin = RequestBatchPlugin(1)
    registry = PluginRegistry()
    registry.register(restarted_plugin)
    runner = EvaluationJobRunner(
        registry=registry,
        repository=repository,
        credential_pool=CredentialLeasingPool.from_env(
            "MISTRAL_API_KEY", {"MISTRAL_API_KEY": "secret-one"}
        ),
        artifact_store=artifacts,
        scratch_root=tmp_path / "collecting-scratch",
        reply_factory=FakeReplyFactory(max_output_tokens=1),
    )

    await runner.run(job.job_id)

    assert restarted_plugin.executions == []
    assert [outcome.ordinal for outcome in restarted_plugin.aggregate_outcomes] == [0]
    assert (await service.get_status(job.job_id))["state"] == "succeeded"


@pytest.mark.asyncio
async def test_cooldown_and_disabled_lanes_do_not_stall_a_healthy_lane(tmp_path: Path) -> None:
    plugin = RequestBatchPlugin(5)
    factory = FakeReplyFactory(
        max_output_tokens=1,
        outcomes={"mistral-1": "rate_limited", "mistral-2": "authentication_failed"},
    )
    runner, service, _ = await prepared_runner(
        tmp_path,
        plugin,
        factory,
        request(workers=3, provider_requests=20, tokens=20),
    )
    job = await service.submit(
        request(workers=3, provider_requests=20, tokens=20),
        idempotency_key="run-key",
    )

    await runner.run(job.job_id)

    assert plugin.aggregate_outcomes
    assert set(ordinal for _, ordinal in plugin.executions) == set(range(5))
    assert [alias for alias, _ in plugin.executions].count("mistral-3") >= 3


@pytest.mark.asyncio
async def test_budget_blocks_a_second_provider_call_before_the_provider_is_invoked(
    tmp_path: Path,
) -> None:
    plugin = RequestBatchPlugin(1)
    factory = FakeReplyFactory(max_output_tokens=10)
    runner, service, repository = await prepared_runner(
        tmp_path,
        plugin,
        factory,
        request(workers=1, provider_requests=1, tokens=10),
        key_count=1,
    )
    job = await service.submit(
        request(workers=1, provider_requests=1, tokens=10),
        idempotency_key="run-key",
    )

    await runner.run(job.job_id)

    assert factory.provider_calls == 1
    assert (await repository.get_job(job.job_id)).state.value == "failed"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_token_allowance_blocks_a_second_provider_call_before_the_provider_is_invoked(
    tmp_path: Path,
) -> None:
    plugin = RequestBatchPlugin(1)
    factory = FakeReplyFactory(max_output_tokens=10)
    runner, service, _ = await prepared_runner(
        tmp_path,
        plugin,
        factory,
        request(workers=1, provider_requests=2, tokens=10),
        key_count=1,
    )
    job = await service.submit(
        request(workers=1, provider_requests=2, tokens=10),
        idempotency_key="run-key",
    )

    await runner.run(job.job_id)

    assert factory.provider_calls == 1


@pytest.mark.asyncio
async def test_uniterated_stream_reserves_nothing_before_a_provider_attempt() -> None:
    pool = CredentialLeasingPool.from_env("MISTRAL_API_KEY", {"MISTRAL_API_KEY": "secret-key"})
    lease = await pool.lease()
    factory = FakeReplyFactory(max_output_tokens=10)
    ledger = BudgetLedger(EvaluationBudget(max_provider_requests=1, max_total_tokens=10), 10)
    reply = BudgetedChatReplyPort(factory.bind(lease, "small-model", lambda event: None), ledger)

    unused = reply.stream_reply(object(), object())
    active = reply.stream_reply(object(), object())

    assert factory.provider_calls == 0
    assert [item async for item in active] == ["private reply"]
    with pytest.raises(BudgetExhausted):
        await anext(unused)
    assert factory.provider_calls == 1
    await lease.release()


@pytest.mark.asyncio
async def test_concurrent_lanes_cannot_over_reserve_the_request_budget(tmp_path: Path) -> None:
    plugin = RequestBatchPlugin(2)
    factory = FakeReplyFactory(max_output_tokens=1)
    runner, service, _ = await prepared_runner(
        tmp_path,
        plugin,
        factory,
        request(workers=2, provider_requests=1, tokens=2),
        key_count=2,
    )
    job = await service.submit(
        request(workers=2, provider_requests=1, tokens=2),
        idempotency_key="run-key",
    )

    await runner.run(job.job_id)

    assert factory.provider_calls == 1


@pytest.mark.asyncio
async def test_ambiguous_timeout_is_unknown_and_never_retried(tmp_path: Path) -> None:
    slept: list[float] = []

    async def sleeper(delay: float) -> None:
        slept.append(delay)

    plugin = RetryableRequestBatchPlugin(1, provider_calls_per_unit=1)
    factory = FakeReplyFactory(
        max_output_tokens=1,
        outcomes={"mistral-1": "timed_out"},
    )
    runner, service, repository = await prepared_runner(
        tmp_path,
        plugin,
        factory,
        request(workers=1, provider_requests=10, tokens=10, attempts=3),
        key_count=1,
        sleeper=sleeper,
    )
    job = await service.submit(
        request(workers=1, provider_requests=10, tokens=10, attempts=3),
        idempotency_key="timeout-key",
    )

    await runner.run(job.job_id)

    attempts = await repository.list_attempts(job.job_id, "unit-0")
    assert factory.provider_calls == 1
    assert len(attempts) == 1
    assert attempts[0].state is AttemptState.UNKNOWN
    assert attempts[0].failure_class is FailureClass.UNKNOWN
    assert (await service.get_status(job.job_id))["state"] == "failed"
    assert slept == []


@pytest.mark.asyncio
async def test_retryable_failures_use_bounded_exponential_and_retry_after_backoff(
    tmp_path: Path,
) -> None:
    class FakeClock:
        now = 0.0

        def __call__(self) -> float:
            return self.now

    clock = FakeClock()
    slept: list[float] = []

    async def sleeper(delay: float) -> None:
        slept.append(delay)
        clock.now += delay

    plugin = RetryableRequestBatchPlugin(1, provider_calls_per_unit=1)
    factory = FakeReplyFactory(
        max_output_tokens=1,
        scripted_events=(
            ("provider_unavailable", None),
            ("rate_limited", 7),
            ("succeeded", None),
        ),
        raising_outcomes=frozenset({"provider_unavailable", "rate_limited"}),
    )
    pool = CredentialLeasingPool.from_env(
        "MISTRAL_API_KEY", {"MISTRAL_API_KEY": "secret-one"}, clock=clock
    )
    runner, service, repository = await prepared_runner(
        tmp_path,
        plugin,
        factory,
        request(workers=1, provider_requests=10, tokens=10, attempts=3),
        key_count=1,
        sleeper=sleeper,
        retry_backoff_base_seconds=1,
        retry_backoff_max_seconds=5,
        credential_pool=pool,
    )
    job = await service.submit(
        request(workers=1, provider_requests=10, tokens=10, attempts=3),
        idempotency_key="backoff-key",
    )

    await runner.run(job.job_id)

    attempts = await repository.list_attempts(job.job_id, "unit-0")
    assert slept == [1, 7]
    assert factory.provider_calls == 3
    assert factory.bound_settled == [False, False, False]
    assert factory.bound_active == [True, True, True]
    assert factory.bound_states == [CredentialState.LEASED] * 3
    assert [attempt.state for attempt in attempts] == [
        AttemptState.FAILED,
        AttemptState.FAILED,
        AttemptState.SUCCEEDED,
    ]
    assert (await service.get_status(job.job_id))["state"] == "succeeded"


@pytest.mark.asyncio
async def test_cancellation_during_retained_cooldown_releases_alias_after_deadline(
    tmp_path: Path,
) -> None:
    class FakeClock:
        now = 0.0

        def __call__(self) -> float:
            return self.now

    clock = FakeClock()
    sleeper_started = asyncio.Event()
    release_sleeper = asyncio.Event()
    slept: list[float] = []

    async def sleeper(delay: float) -> None:
        slept.append(delay)
        sleeper_started.set()
        await release_sleeper.wait()

    plugin = RetryableRequestBatchPlugin(1, provider_calls_per_unit=1)
    factory = FakeReplyFactory(
        max_output_tokens=1,
        scripted_events=(("rate_limited", 7),),
        raising_outcomes=frozenset({"rate_limited"}),
    )
    pool = CredentialLeasingPool.from_env(
        "MISTRAL_API_KEY", {"MISTRAL_API_KEY": "secret-one"}, clock=clock
    )
    runner, service, repository = await prepared_runner(
        tmp_path,
        plugin,
        factory,
        request(workers=1, provider_requests=10, tokens=10, attempts=3),
        key_count=1,
        sleeper=sleeper,
        retry_backoff_max_seconds=5,
        credential_pool=pool,
    )
    job = await service.submit(
        request(workers=1, provider_requests=10, tokens=10, attempts=3),
        idempotency_key="cancel-retained-cooldown-key",
    )

    run_task = asyncio.create_task(runner.run(job.job_id))
    await sleeper_started.wait()

    assert slept == [7]
    assert factory.provider_calls == 1
    assert pool.state_for("mistral-1") is CredentialState.COOLING_DOWN

    await service.request_cancel(job.job_id)
    release_sleeper.set()
    await run_task

    attempts = await repository.list_attempts(job.job_id, "unit-0")
    assert factory.provider_calls == 1
    assert len(attempts) == 1
    assert (await service.get_status(job.job_id))["state"] == "cancelled"
    assert pool._records[0].active_lease is None
    assert pool.state_for("mistral-1") is CredentialState.COOLING_DOWN
    with pytest.raises(RuntimeError, match="No healthy credential"):
        await pool.lease()

    clock.now = 7.0

    recovered = await pool.lease()
    assert recovered.alias == "mistral-1"
    with pytest.raises(RuntimeError, match="No healthy credential"):
        await pool.lease()
    await recovered.release()


@pytest.mark.asyncio
async def test_cancellation_during_backoff_stops_before_another_spend_or_claim(
    tmp_path: Path,
) -> None:
    slept: list[float] = []
    service_holder: list[EvaluationJobService] = []
    job_id_holder: list[str] = []

    async def sleeper(delay: float) -> None:
        slept.append(delay)
        await service_holder[0].request_cancel(job_id_holder[0])

    plugin = RetryableRequestBatchPlugin(2, provider_calls_per_unit=1)
    factory = FakeReplyFactory(
        max_output_tokens=1,
        outcomes={"mistral-1": "provider_unavailable"},
        raising_outcomes=frozenset({"provider_unavailable"}),
    )
    runner, service, repository = await prepared_runner(
        tmp_path,
        plugin,
        factory,
        request(workers=1, provider_requests=10, tokens=10, attempts=3),
        key_count=1,
        sleeper=sleeper,
    )
    job = await service.submit(
        request(workers=1, provider_requests=10, tokens=10, attempts=3),
        idempotency_key="cancel-backoff-key",
    )
    service_holder.append(service)
    job_id_holder.append(job.job_id)

    await runner.run(job.job_id)

    unclaimed = await repository.get_unit(job.job_id, "unit-1")
    assert slept == [1]
    assert factory.provider_calls == 1
    assert unclaimed is not None and unclaimed.state is UnitState.READY
    assert (await service.get_status(job.job_id))["state"] == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cancellation_requested", "expected_state"),
    ((False, UnitState.FAILED), (True, UnitState.CANCELLED)),
)
async def test_claimed_unstarted_unit_distinguishes_budget_exhaustion_from_cancellation(
    tmp_path: Path,
    cancellation_requested: bool,
    expected_state: UnitState,
) -> None:
    plugin = RequestBatchPlugin(1)
    factory = FakeReplyFactory(max_output_tokens=10)
    runner, service, repository = await prepared_runner(
        tmp_path,
        plugin,
        factory,
        request(workers=1, provider_requests=1, tokens=10),
        key_count=1,
    )
    job = await service.submit(
        request(workers=1, provider_requests=1, tokens=10),
        idempotency_key="run-key",
    )
    queue = DurableWorkUnitQueue(repository, capacity=1)
    claimed = await queue.claim_next(job.job_id, "lane-1")
    assert claimed is not None

    ledger = BudgetLedger(job.request.budget, factory.max_output_tokens)
    await ledger.reserve_attempt()
    if cancellation_requested:
        await service.request_cancel(job.job_id)

    lease_pool = CredentialLeasingPool.from_env(
        "MISTRAL_API_KEY", {"MISTRAL_API_KEY": "secret-key"}
    )
    lease = await lease_pool.lease()
    try:
        executed = await runner._execute_claimed_unit(
            job,
            plugin,
            await plugin.preflight(job.request),
            claimed,
            lease,
            "lane-1",
            ledger,
            asyncio.Event(),
        )
    finally:
        await lease.release()

    outcome = executed.outcome
    await queue.complete(job.job_id, outcome)

    persisted = await repository.get_unit(job.job_id, claimed.unit_id)
    assert outcome.state is expected_state
    assert persisted is not None and persisted.state is expected_state
    assert factory.bound_aliases == []
    assert factory.provider_calls == 0
    attempts = await repository.list_attempts(job.job_id, claimed.unit_id)
    if cancellation_requested:
        assert attempts == ()
    else:
        assert len(attempts) == 1
        assert attempts[0].state.value == "failed"
        assert attempts[0].failure_class is FailureClass.EVALUATION
    assert await queue.claim_next(job.job_id, "lane-followup") is None


@pytest.mark.asyncio
async def test_mixed_and_cleanup_failures_cannot_claim_clean_success(tmp_path: Path) -> None:
    partial_plugin = RequestBatchPlugin(2, fail_ordinals=frozenset({1}))
    partial_runner, partial_service, _ = await prepared_runner(
        tmp_path / "partial",
        partial_plugin,
        FakeReplyFactory(max_output_tokens=1),
        request(workers=1, provider_requests=20, tokens=20),
        key_count=1,
    )
    partial_job = await partial_service.submit(
        request(workers=1, provider_requests=20, tokens=20),
        idempotency_key="partial-key",
    )
    await partial_runner.run(partial_job.job_id)

    cleanup_plugin = RequestBatchPlugin(1, cleanup_fails=True)
    cleanup_runner, cleanup_service, cleanup_repository = await prepared_runner(
        tmp_path / "cleanup",
        cleanup_plugin,
        FakeReplyFactory(max_output_tokens=1),
        request(workers=1, provider_requests=20, tokens=20),
        key_count=1,
    )
    cleanup_job = await cleanup_service.submit(
        request(workers=1, provider_requests=20, tokens=20),
        idempotency_key="cleanup-key",
    )
    await cleanup_runner.run(cleanup_job.job_id)

    assert (await partial_service.get_status(partial_job.job_id))["state"] == "partially_succeeded"
    assert (await cleanup_service.get_status(cleanup_job.job_id))["state"] == "failed"
    cleanup_attempts = await cleanup_repository.list_attempts(cleanup_job.job_id, "unit-0")
    assert cleanup_attempts[0].failure_class is FailureClass.EVALUATION
    assert cleanup_plugin.scratch_dirs
    assert all(not path.exists() for path in cleanup_plugin.scratch_dirs)


@pytest.mark.asyncio
async def test_cleanup_warnings_are_persisted_and_prevent_clean_success(tmp_path: Path) -> None:
    plugin = RequestBatchPlugin(1, cleanup_warns=True)
    runner, service, repository = await prepared_runner(
        tmp_path,
        plugin,
        FakeReplyFactory(max_output_tokens=1),
        request(workers=1, provider_requests=4, tokens=4),
        key_count=1,
    )
    job = await service.submit(
        request(workers=1, provider_requests=4, tokens=4),
        idempotency_key="cleanup-warning-key",
    )

    await runner.run(job.job_id)

    status = await service.get_status(job.job_id)
    stored = (await repository.list_units(job.job_id))[0]
    assert status["state"] == "partially_succeeded"
    assert [warning["code"] for warning in status["warnings"]] == ["WORKER_COUNT_REDUCED"]
    assert stored.state is UnitState.SUCCEEDED
    assert stored.outcome_ref is not None
    assert plugin.scratch_dirs and all(not path.exists() for path in plugin.scratch_dirs)


@pytest.mark.asyncio
async def test_cancellation_stops_new_claims_and_finishes_cancelled(tmp_path: Path) -> None:
    plugin = RequestBatchPlugin(2, block_ordinal=0)
    runner, service, repository = await prepared_runner(
        tmp_path,
        plugin,
        FakeReplyFactory(max_output_tokens=1),
        request(workers=1, provider_requests=20, tokens=20),
        key_count=1,
    )
    job = await service.submit(
        request(workers=1, provider_requests=20, tokens=20),
        idempotency_key="cancel-key",
    )

    running = asyncio.create_task(runner.run(job.job_id))
    await plugin.started[0].wait()
    await service.request_cancel(job.job_id)
    plugin.release_blocked.set()
    await running

    assert (await service.get_status(job.job_id))["state"] == "cancelled"
    unit = await repository.get_unit(job.job_id, "unit-1")
    assert unit is not None and unit.state is UnitState.READY


@pytest.mark.asyncio
async def test_supervisor_cancellation_after_claim_commit_cleans_owned_unit_and_lease(
    tmp_path: Path,
) -> None:
    repository = ClaimGateRepository(tmp_path / "evaluation-jobs.db")
    pool = CredentialLeasingPool.from_env(
        "MISTRAL_API_KEY", {"MISTRAL_API_KEY": "secret-one"}
    )
    factory = FakeReplyFactory(max_output_tokens=1)
    service, _ = await prepared_supervised_runner(
        tmp_path,
        RequestBatchPlugin(1, provider_calls_per_unit=1),
        factory,
        repository,
        pool,
    )
    job = await service.submit(
        request(workers=1, provider_requests=4, tokens=4),
        idempotency_key="cancel-after-claim",
    )
    await repository.claim_committed.wait()

    cancelling = asyncio.create_task(service.request_cancel(job.job_id))
    await repository.cancellation_committed.wait()
    repository.return_claim.set()
    await cancelling

    stored = await repository.get_unit(job.job_id, "unit-0")
    assert stored is not None and stored.state is UnitState.CANCELLED
    assert await repository.list_attempts(job.job_id) == ()
    assert (await service.get_status(job.job_id))["state"] == "cancelled"
    assert factory.provider_calls == 0
    assert repository.completed_units == ["unit-0"]
    assert pool._records[0].active_lease is None


@pytest.mark.asyncio
async def test_supervisor_cancellation_during_attempt_cleans_step_attempt_unit_and_lease(
    tmp_path: Path,
) -> None:
    repository = CancellationTrackingRepository(tmp_path / "evaluation-jobs.db")
    pool = CredentialLeasingPool.from_env(
        "MISTRAL_API_KEY", {"MISTRAL_API_KEY": "secret-one"}
    )
    factory = BlockingAttemptReplyFactory(repository)
    plugin = RequestBatchPlugin(1, provider_calls_per_unit=1)
    service, _ = await prepared_supervised_runner(
        tmp_path,
        plugin,
        factory,
        repository,
        pool,
    )
    job = await service.submit(
        request(workers=1, provider_requests=4, tokens=4),
        idempotency_key="cancel-during-attempt",
    )
    await factory.attempt_running.wait()

    await service.request_cancel(job.job_id)

    stored = await repository.get_unit(job.job_id, "unit-0")
    attempts = await repository.list_attempts(job.job_id, "unit-0")
    assert repository.events[:2] == ["cancellation_committed", "attempt_cancelled"]
    assert repository.step_states == [StepState.RUNNING, StepState.SKIPPED]
    assert stored is not None and stored.state is UnitState.CANCELLED
    assert len(attempts) == 1 and attempts[0].state is AttemptState.CANCELLED
    assert (await service.get_status(job.job_id))["state"] == "cancelled"
    assert factory.provider_calls == 1
    assert plugin.cleanup_calls == 1
    assert repository.completed_units == ["unit-0"]
    assert plugin.scratch_dirs and all(not path.exists() for path in plugin.scratch_dirs)
    assert pool._records[0].active_lease is None


@pytest.mark.asyncio
async def test_cancellation_preserves_safe_warning_when_cleanup_fails(tmp_path: Path) -> None:
    repository = CancellationTrackingRepository(tmp_path / "evaluation-jobs.db")
    pool = CredentialLeasingPool.from_env(
        "MISTRAL_API_KEY", {"MISTRAL_API_KEY": "secret-one"}
    )
    factory = BlockingAttemptReplyFactory(repository)
    plugin = RequestBatchPlugin(1, cleanup_fails=True, provider_calls_per_unit=1)
    service, _ = await prepared_supervised_runner(
        tmp_path,
        plugin,
        factory,
        repository,
        pool,
    )
    job = await service.submit(
        request(workers=1, provider_requests=4, tokens=4),
        idempotency_key="cancel-with-cleanup-failure",
    )
    await factory.attempt_running.wait()

    await service.request_cancel(job.job_id)

    status = await service.get_status(job.job_id)
    stored_job = await repository.get_job(job.job_id)
    stored_unit = await repository.get_unit(job.job_id, "unit-0")
    attempts = await repository.list_attempts(job.job_id, "unit-0")
    safe_records = (status, stored_job, stored_unit, attempts)
    assert status["state"] == "cancelled"
    assert status["warnings"] == (
        {
            "code": "CLEANUP_FAILED",
            "message": "Evaluation cleanup did not complete.",
            "details": {"failed_resources": 1},
        },
    )
    assert stored_job is not None and stored_job.state is JobState.CANCELLED
    assert [warning.code for warning in stored_job.warnings] == ["CLEANUP_FAILED"]
    assert stored_unit is not None and stored_unit.state is UnitState.CANCELLED
    assert len(attempts) == 1 and attempts[0].state is AttemptState.CANCELLED
    assert await repository.list_running_units(job.job_id) == ()
    assert repository.step_states[-1] is StepState.SKIPPED
    assert "private cleanup failure" not in repr(safe_records)
    assert b"private cleanup failure" not in (tmp_path / "evaluation-jobs.db").read_bytes()
    assert plugin.cleanup_calls == 1
    assert repository.completed_units == ["unit-0"]
    assert plugin.scratch_dirs and all(not path.exists() for path in plugin.scratch_dirs)
    assert pool._records[0].active_lease is None


@pytest.mark.asyncio
async def test_two_cancelled_lanes_dedupe_cleanup_warning_across_replay(tmp_path: Path) -> None:
    repository = CancellationTrackingRepository(tmp_path / "evaluation-jobs.db")
    pool = CredentialLeasingPool.from_env(
        "MISTRAL_API_KEY",
        {
            "MISTRAL_API_KEY": "secret-one",
            "MISTRAL_API_KEY2": "secret-two",
        },
    )
    factory = BlockingAttemptReplyFactory(repository, expected_running=2)
    plugin = RequestBatchPlugin(2, cleanup_fails=True, provider_calls_per_unit=1)
    service, _ = await prepared_supervised_runner(
        tmp_path,
        plugin,
        factory,
        repository,
        pool,
    )
    job = await service.submit(
        request(workers=2, provider_requests=8, tokens=8),
        idempotency_key="two-lane-cleanup-failure",
    )
    await factory.attempt_running.wait()

    await service.request_cancel(job.job_id)
    cleanup_warning = EvaluationWarning(
        code="CLEANUP_FAILED",
        details={"failed_resources": 1},
    )
    await repository.append_warnings(job.job_id, (cleanup_warning,))

    status = await service.get_status(job.job_id)
    stored_job = await repository.get_job(job.job_id)
    units = await repository.list_units(job.job_id)
    attempts = await repository.list_attempts(job.job_id)
    assert status["state"] == "cancelled"
    assert [warning["code"] for warning in status["warnings"]] == ["CLEANUP_FAILED"]
    assert stored_job is not None and stored_job.warnings == (cleanup_warning,)
    assert len(units) == 2 and all(unit.state is UnitState.CANCELLED for unit in units)
    assert len(attempts) == 2 and all(
        attempt.state is AttemptState.CANCELLED for attempt in attempts
    )
    assert await repository.list_running_units(job.job_id) == ()
    assert repository.step_states.count(StepState.RUNNING) == 2
    assert repository.step_states.count(StepState.SKIPPED) == 2
    assert "private cleanup failure" not in repr((status, stored_job, units, attempts))
    assert b"private cleanup failure" not in (tmp_path / "evaluation-jobs.db").read_bytes()
    assert plugin.cleanup_calls == 2
    assert len(repository.completed_units) == 2
    assert set(repository.completed_units) == {"unit-0", "unit-1"}
    assert plugin.scratch_dirs and all(not path.exists() for path in plugin.scratch_dirs)
    assert all(record.active_lease is None for record in pool._records)
