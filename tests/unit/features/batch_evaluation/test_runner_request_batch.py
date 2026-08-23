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
    ExecutionMode,
    FailureClass,
    FailureClassification,
    PluginPlan,
    ProviderAttemptEvent,
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
            event = ProviderAttemptEvent(
                credential_alias=self._alias,
                request_attempt_id=f"provider-{self._factory.provider_calls}",
                outcome=self._factory.outcome_for(self._alias),
                status_code=None,
                retry_after_seconds=0,
                latency_ms=0,
            )
            result = self._sink(event)
            if result is not None:
                await result
            yield "private reply"

        return stream()


class FakeReplyFactory:
    def __init__(
        self,
        *,
        max_output_tokens: int = 10,
        outcomes: Mapping[str, str] | None = None,
    ) -> None:
        self.max_output_tokens = max_output_tokens
        self._outcomes = dict(outcomes or {})
        self.provider_calls = 0
        self.bound_aliases: list[str] = []

    def bind(self, lease: object, model: str, attempt_sink: AttemptSink) -> FakeReply:
        del model
        alias = lease.alias
        self.bound_aliases.append(alias)
        return FakeReply(attempt_sink, alias, self)

    def outcome_for(self, alias: str) -> str:
        return self._outcomes.get(alias, "succeeded")


class ProviderFailure(RuntimeError):
    pass


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
    ) -> None:
        self.work_count = work_count
        self.block_ordinal = block_ordinal
        self.fail_ordinals = fail_ordinals
        self.cleanup_fails = cleanup_fails
        self.started = tuple(asyncio.Event() for _ in range(work_count))
        self.finished = tuple(asyncio.Event() for _ in range(work_count))
        self.release_blocked = asyncio.Event()
        self.executions: list[tuple[str, int]] = []
        self.finish_order: list[int] = []
        self.aggregate_outcomes: tuple[WorkUnitOutcome, ...] = ()

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
        self.started[unit.ordinal].set()
        self.executions.append((context.credential_alias, unit.ordinal))
        reply = context.provider_client
        for _ in range(2):
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
        return ArtifactBundle(
            public_result={"ordinals": tuple(outcome.ordinal for outcome in outcomes)},
            private_artifact_ids=(),
        )

    async def cleanup(self, context: WorkContext) -> CleanupOutcome:
        del context
        if self.cleanup_fails:
            raise RuntimeError("private cleanup failure")
        return CleanupOutcome(removed_resources=1, warnings=())

    def classify_failure(self, error: BaseException) -> FailureClassification:
        del error
        return FailureClassification(FailureClass.PROVIDER, retryable=False, credential_state=None)


def request(*, workers: int, provider_requests: int, tokens: int) -> EvaluationRequest:
    return EvaluationRequest(
        evaluation_type="request-eval",
        provider="mistral",
        target_model="small-model",
        dataset_ref="dataset-v1",
        credential_pool="mistral-eval",
        execution_mode=ExecutionMode.REQUEST_BATCH,
        max_workers=workers,
        max_attempts_per_unit=1,
        budget=EvaluationBudget(provider_requests, tokens),
        parameters={},
    )


async def prepared_runner(
    tmp_path: Path,
    plugin: RequestBatchPlugin,
    factory: FakeReplyFactory,
    submitted_request: EvaluationRequest,
    key_count: int = 3,
) -> tuple[EvaluationJobRunner, EvaluationJobService, SQLiteEvaluationJobRepository]:
    repository = SQLiteEvaluationJobRepository(tmp_path / "evaluation-jobs.db")
    await repository.initialize()
    registry = PluginRegistry()
    registry.register(plugin)
    keys = {"MISTRAL_API_KEY": "secret-one"}
    keys.update({f"MISTRAL_API_KEY{index}": f"secret-{index}" for index in range(2, key_count + 1)})
    pool = CredentialLeasingPool.from_env("MISTRAL_API_KEY", keys)
    artifacts = FilesystemEvaluationArtifactStore(tmp_path / "artifacts")
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
    )
    await service.submit(submitted_request, idempotency_key="job-key")
    return runner, service, repository


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
        outcome = await runner._execute_claimed_unit(
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
    cleanup_runner, cleanup_service, _ = await prepared_runner(
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
