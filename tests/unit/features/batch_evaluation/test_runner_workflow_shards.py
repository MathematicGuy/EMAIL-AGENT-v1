from __future__ import annotations

from collections.abc import Mapping, Sequence
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
    UnitState,
    WorkContext,
    WorkUnit,
    WorkUnitOutcome,
)
from cowork_agent.features.batch_evaluation.credentials import CredentialLeasingPool
from cowork_agent.features.batch_evaluation.registry import PluginRegistry
from cowork_agent.features.batch_evaluation.runner import EvaluationJobRunner
from cowork_agent.features.batch_evaluation.service import EvaluationJobService
from cowork_agent.persistence.repositories.evaluation_jobs import SQLiteEvaluationJobRepository


class RetryableFailure(RuntimeError):
    pass


class FakeReplyFactory:
    max_output_tokens = 1

    def __init__(self) -> None:
        self.bound_aliases: list[str] = []
        self.bound_settled: list[bool] = []
        self.bound_active: list[bool] = []

    def bind(self, lease: object, model: str, attempt_sink: object) -> object:
        del model, attempt_sink
        alias = lease.alias
        self.bound_aliases.append(alias)
        self.bound_settled.append(lease._settled)
        self.bound_active.append(lease._record.active_lease is lease)
        return object()


class ShardPlugin:
    evaluation_type = "shard-eval"
    version = "1"
    supported_modes = frozenset({ExecutionMode.WORKFLOW_SHARDS})
    parameter_schema: Mapping[str, object] = {"type": "object"}

    def __init__(self) -> None:
        self.executions: list[tuple[str, int]] = []
        self.contexts: list[tuple[int, WorkContext]] = []
        self._fail_once = True
        self.build_allowed = True
        self.aggregate_outcomes: tuple[WorkUnitOutcome, ...] = ()

    async def preflight(self, request: EvaluationRequest) -> PluginPlan:
        return PluginPlan(request.dataset_ref, 4, object())

    def build_work_units(self, plan: PluginPlan, lane_count: int) -> tuple[WorkUnit, ...]:
        del plan, lane_count
        if not self.build_allowed:
            raise AssertionError("runner must use the durable fixed assignment")
        return tuple(
            WorkUnit(
                unit_id=f"unit-{ordinal}",
                ordinal=ordinal,
                payload={"probe_id": f"probe-{ordinal}"},
            )
            for ordinal in range(4)
        )

    async def execute_work(self, unit: WorkUnit, context: WorkContext) -> WorkUnitOutcome:
        self.contexts.append((unit.ordinal, context))
        self.executions.append((context.credential_alias, unit.ordinal))
        if unit.ordinal == 0 and self._fail_once:
            self._fail_once = False
            raise RetryableFailure()
        return WorkUnitOutcome(
            unit_id=unit.unit_id,
            ordinal=unit.ordinal,
            state=UnitState.SUCCEEDED,
            provider_requests=0,
            total_tokens=0,
            private_result=None,
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
        return CleanupOutcome(removed_resources=1, warnings=())

    def classify_failure(self, error: BaseException) -> FailureClassification:
        return FailureClassification(
            failure_class=FailureClass.PROVIDER,
            retryable=isinstance(error, RetryableFailure),
            credential_state=None,
        )


class TrackingRepository(SQLiteEvaluationJobRepository):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.claimed_unit_ids: list[str] = []

    async def claim_ready_unit(self, job_id: str, worker_id: str) -> WorkUnit | None:
        del job_id, worker_id
        raise AssertionError("fixed workflow shards must never claim from the global queue")

    async def claim_ready_unit_by_id(
        self, job_id: str, unit_id: str, worker_id: str
    ) -> WorkUnit | None:
        self.claimed_unit_ids.append(unit_id)
        return await super().claim_ready_unit_by_id(job_id, unit_id, worker_id)

class TrackingCredentialPool(CredentialLeasingPool):
    def __init__(self) -> None:
        super().__init__(
            "MISTRAL_API_KEY",
            ("secret-one", "secret-two"),
            clock=lambda: 0.0,
        )
        self.leased_aliases: list[str] = []

    async def lease(self):  # type: ignore[no-untyped-def]
        lease = await super().lease()
        self.leased_aliases.append(lease.alias)
        return lease


def request() -> EvaluationRequest:
    return EvaluationRequest(
        evaluation_type="shard-eval",
        provider="mistral",
        target_model="small-model",
        dataset_ref="dataset-v1",
        credential_pool="mistral-eval",
        execution_mode=ExecutionMode.WORKFLOW_SHARDS,
        max_workers=2,
        max_attempts_per_unit=2,
        budget=EvaluationBudget(max_provider_requests=20, max_total_tokens=20),
        parameters={},
    )


@pytest.mark.asyncio
async def test_fixed_shards_keep_one_lease_execute_assigned_work_sequentially_and_retry_fresh(
    tmp_path: Path,
) -> None:
    artifacts = FilesystemEvaluationArtifactStore(tmp_path / "artifacts")
    repository = TrackingRepository(tmp_path / "evaluation-jobs.db")
    await repository.initialize()
    registry = PluginRegistry()
    plugin = ShardPlugin()
    registry.register(plugin)
    pool = TrackingCredentialPool()
    factory = FakeReplyFactory()
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
    job = await service.submit(request(), idempotency_key="shards-key")
    plugin.build_allowed = False

    await runner.run(job.job_id)

    assert sorted(repository.claimed_unit_ids) == ["unit-0", "unit-1", "unit-2", "unit-3"]
    assert len(pool.leased_aliases) == 2
    assert len(factory.bound_aliases) == 5
    assert factory.bound_settled == [False] * 5
    assert factory.bound_active == [True] * 5
    by_alias = {
        alias: [
            ordinal
            for execution_alias, ordinal in plugin.executions
            if execution_alias == alias
        ]
        for alias in factory.bound_aliases
    }
    assert {frozenset(ordinals) for ordinals in by_alias.values()} == {
        frozenset({0, 2}),
        frozenset({1, 3}),
    }
    attempts = await repository.list_attempts(job.job_id, "unit-0")
    assert [attempt.attempt_number for attempt in attempts] == [1, 2]
    unit_zero_contexts = [
        context
        for ordinal, context in plugin.contexts
        if ordinal == 0 and context.job_id == job.job_id
    ]
    assert len({context.attempt_id for context in unit_zero_contexts}) >= 2
    assert len({context.scratch_dir for context in unit_zero_contexts}) >= 2
    assert [outcome.ordinal for outcome in plugin.aggregate_outcomes] == [0, 1, 2, 3]
