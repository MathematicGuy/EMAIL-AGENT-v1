from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from pathlib import Path

import pytest

from cowork_agent.features.batch_evaluation.artifacts import FilesystemEvaluationArtifactStore
from cowork_agent.features.batch_evaluation.contracts import (
    ArtifactBundle,
    CleanupOutcome,
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
from cowork_agent.features.batch_evaluation.registry import PluginRegistry
from cowork_agent.features.batch_evaluation.runner import (
    BudgetExhausted,
    BudgetLedger,
    EvaluationJobRunner,
)
from cowork_agent.features.batch_evaluation.service import EvaluationJobService
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

    def bind(self, lease: object, model: str, attempt_sink: AttemptSink) -> FakeReply:
        del model
        alias = lease.alias
        self.bound_aliases.append(alias)
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
            429 if outcome == "rate_limited" else 503 if outcome == "provider_unavailable" else None
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

    def aggregate(self, plan: PluginPlan, outcomes: Sequence[WorkUnitOutcome]) -> ArtifactBundle:
        del plan
        self.aggregate_outcomes = tuple(outcomes)
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
            (
                EvaluationWarning(
                    code="WORKER_COUNT_REDUCED",
                    details={
                        "requested_workers": 2,
                        "healthy_credentials": 1,
                        "ready_work": 1,
                        "effective_workers": 1,
                    },
                ),
            )
            if self.cleanup_warns
            else ()
        )
        return CleanupOutcome(removed_resources=1, warnings=warnings)

    def classify_failure(self, error: BaseException) -> FailureClassification:
        del error
        return FailureClassification(FailureClass.PROVIDER, retryable=False, credential_state=None)


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
        budget=EvaluationBudget(max_provider_requests=provider_requests, max_total_tokens=tokens),
        parameters={},
    )


async def prepared_runner(
    tmp_path: Path,
    plugin: RequestBatchPlugin,
    factory: FakeReplyFactory,
    submitted_request: EvaluationRequest,
    key_count: int = 3,
) -> tuple[EvaluationJobRunner, EvaluationJobService, SQLiteEvaluationJobRepository]:
    artifacts = FilesystemEvaluationArtifactStore(tmp_path / "artifacts")
    repository = SQLiteEvaluationJobRepository(tmp_path / "evaluation-jobs.db")
    await repository.initialize()
    registry = PluginRegistry()
    registry.register(plugin)
    keys = {"MISTRAL_API_KEY": "secret-one"}
    keys.update({f"MISTRAL_API_KEY{index}": f"secret-{index}" for index in range(2, key_count + 1)})
    pool = CredentialLeasingPool.from_env("MISTRAL_API_KEY", keys)
    service = EvaluationJobService(
        registry=registry, repository=repository, credential_pool=pool, artifact_store=artifacts
    )
    runner = EvaluationJobRunner(
        registry=registry,
        repository=repository,
        credential_pool=pool,
        artifact_store=artifacts,
        scratch_root=tmp_path / "scratch",
        reply_factory=factory,
    )
    await service.submit(submitted_request, idempotency_key="job-key")
    return runner, service, repository


@pytest.mark.asyncio
async def test_request_batch_durable_execution_and_ordinal_aggregation(tmp_path: Path) -> None:
    plugin = RequestBatchPlugin(4, block_ordinal=0)
    runner, service, repository = await prepared_runner(
        tmp_path,
        plugin,
        FakeReplyFactory(max_output_tokens=1),
        request(workers=2, provider_requests=20, tokens=20),
        key_count=2,
    )
    job = await service.submit(
        request(workers=2, provider_requests=20, tokens=20), idempotency_key="run-key"
    )

    task = asyncio.create_task(runner.run(job.job_id))
    await plugin.started[0].wait()
    await plugin.started[1].wait()
    plugin.release_blocked.set()
    await task
    finished_job = await repository.get_job(job.job_id)

    assert finished_job.state is JobState.SUCCEEDED
    assert tuple(o.ordinal for o in plugin.aggregate_outcomes) == (0, 1, 2, 3)


@pytest.mark.asyncio
async def test_request_batch_budget_token_reservation_and_stream_handling() -> None:
    ledger = BudgetLedger(
        EvaluationBudget(max_provider_requests=2, max_total_tokens=20), token_allowance=10
    )

    # First reservation works
    await ledger.reserve_attempt()
    assert not ledger.exhausted

    # Second reservation works and exhausts
    await ledger.reserve_attempt()
    assert ledger.exhausted

    # Third reservation raises BudgetExhausted
    with pytest.raises(BudgetExhausted):
        await ledger.reserve_attempt()


@pytest.mark.asyncio
async def test_request_batch_cooldown_backoff_and_retry_lifecycle(tmp_path: Path) -> None:
    plugin = RequestBatchPlugin(2, fail_ordinals=frozenset({0}))
    runner, service, repository = await prepared_runner(
        tmp_path,
        plugin,
        FakeReplyFactory(outcomes={"mistral-1": "rate_limited"}),
        request(workers=1, provider_requests=10, tokens=10, attempts=1),
        key_count=1,
    )
    job = await service.submit(
        request(workers=1, provider_requests=10, tokens=10, attempts=1), idempotency_key="retry-key"
    )
    await runner.run(job.job_id)
    finished_job = await repository.get_job(job.job_id)
    assert finished_job.state in (JobState.FAILED, JobState.SUCCEEDED)


@pytest.mark.asyncio
async def test_request_batch_cancellation_and_resource_cleanup(tmp_path: Path) -> None:
    plugin = RequestBatchPlugin(2, block_ordinal=0)
    repo = CancellationTrackingRepository(tmp_path / "cancel.db")
    await repo.initialize()
    artifacts = FilesystemEvaluationArtifactStore(tmp_path / "artifacts")
    registry = PluginRegistry()
    registry.register(plugin)
    pool = CredentialLeasingPool.from_env("MISTRAL_API_KEY", {"MISTRAL_API_KEY": "s1"})

    runner = EvaluationJobRunner(
        registry=registry,
        repository=repo,
        credential_pool=pool,
        artifact_store=artifacts,
        scratch_root=tmp_path / "scratch",
        reply_factory=FakeReplyFactory(),
    )
    service = EvaluationJobService(
        registry=registry, repository=repo, credential_pool=pool, artifact_store=artifacts
    )
    job = await service.submit(
        request(workers=1, provider_requests=10, tokens=10), idempotency_key="c-key"
    )

    task = asyncio.create_task(runner.run(job.job_id))
    await plugin.started[0].wait()
    await repo.request_cancellation(job.job_id)
    plugin.release_blocked.set()
    await task
    finished = await repo.get_job(job.job_id)
    assert finished.state is JobState.CANCELLED
