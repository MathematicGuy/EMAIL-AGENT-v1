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
from cowork_agent.features.batch_evaluation.service import (
    EvaluationConflict,
    EvaluationJobService,
    EvaluationResultConflict,
    EvaluationValidationError,
)
from cowork_agent.persistence.repositories.evaluation_jobs import SQLiteEvaluationJobRepository


class FakePlugin:
    evaluation_type = "fake-eval"
    version = "1"
    supported_modes = frozenset({ExecutionMode.REQUEST_BATCH})
    parameter_schema: Mapping[str, object] = {"type": "object"}

    def __init__(
        self,
        *,
        preflight_error: BaseException | None = None,
        ready_work: int = 2,
    ) -> None:
        self.preflight_error = preflight_error
        self.ready_work = ready_work
        self.preflight_calls = 0
        self.provider_calls = 0

    async def preflight(self, request: EvaluationRequest) -> PluginPlan:
        self.preflight_calls += 1
        if self.preflight_error is not None:
            raise self.preflight_error
        return PluginPlan(
            dataset_ref=request.dataset_ref,
            ready_work=self.ready_work,
            private_plan=object(),
        )

    def build_work_units(self, plan: PluginPlan, lane_count: int) -> tuple[WorkUnit, ...]:
        del lane_count
        return tuple(
            WorkUnit(
                unit_id=f"unit-{ordinal + 1}",
                ordinal=ordinal,
                payload={"case_id": f"case-{ordinal + 1}"},
            )
            for ordinal in range(plan.ready_work)
        )

    async def execute_work(self, unit: WorkUnit, context: WorkContext) -> WorkUnitOutcome:
        del context
        self.provider_calls += 1
        return WorkUnitOutcome(
            unit_id=unit.unit_id,
            ordinal=unit.ordinal,
            state=UnitState.SUCCEEDED,
            provider_requests=1,
            total_tokens=1,
            private_result=None,
        )

    def aggregate(
        self, plan: PluginPlan, outcomes: Sequence[WorkUnitOutcome]
    ) -> ArtifactBundle:
        del plan, outcomes
        return ArtifactBundle(public_result={"complete": 1}, private_artifact_ids=())

    async def cleanup(self, context: WorkContext) -> CleanupOutcome:
        del context
        return CleanupOutcome(removed_resources=0, warnings=())

    def classify_failure(self, error: BaseException) -> FailureClassification:
        del error
        return FailureClassification(
            failure_class=FailureClass.EVALUATION,
            retryable=False,
            credential_state=None,
        )


def request(*, max_workers: int = 1) -> EvaluationRequest:
    return EvaluationRequest(
        evaluation_type="fake-eval",
        provider="mistral",
        target_model="small-model",
        dataset_ref="dataset-v1",
        credential_pool="mistral-eval",
        execution_mode=ExecutionMode.REQUEST_BATCH,
        max_workers=max_workers,
        max_attempts_per_unit=1,
        budget=EvaluationBudget(max_provider_requests=10, max_total_tokens=1000),
        parameters={"version_id": "v1"},
    )


async def service_with(
    tmp_path: Path,
    plugin: FakePlugin,
) -> tuple[EvaluationJobService, SQLiteEvaluationJobRepository]:
    path = tmp_path / "evaluation-jobs.db"
    repository = SQLiteEvaluationJobRepository(path)
    await repository.initialize()
    registry = PluginRegistry()
    registry.register(plugin)
    service = EvaluationJobService(
        registry=registry,
        repository=repository,
        credential_pool=CredentialLeasingPool.from_env(
            "MISTRAL_API_KEY",
            {"MISTRAL_API_KEY": "secret-key"},
        ),
        artifact_store=FilesystemEvaluationArtifactStore(tmp_path / "artifacts"),
    )
    return service, repository


@pytest.mark.asyncio
async def test_submit_validates_before_persisting_or_spending(tmp_path: Path) -> None:
    plugin = FakePlugin(preflight_error=ValueError("private dataset failure"))
    service, repository = await service_with(tmp_path, plugin)

    with pytest.raises(EvaluationValidationError) as error:
        await service.submit(request(), idempotency_key="key-1")

    assert "private dataset failure" not in str(error.value)
    assert await repository.list_recoverable_jobs() == ()
    assert plugin.provider_calls == 0


@pytest.mark.asyncio
async def test_submit_rejects_unknown_type_and_incompatible_mode_without_persistence(
    tmp_path: Path,
) -> None:
    plugin = FakePlugin()
    service, repository = await service_with(tmp_path, plugin)
    unknown = EvaluationRequest(
        evaluation_type="unknown-type",
        provider="mistral",
        target_model="small-model",
        dataset_ref="dataset-v1",
        credential_pool="mistral-eval",
        execution_mode=ExecutionMode.REQUEST_BATCH,
        max_workers=1,
        max_attempts_per_unit=1,
        budget=EvaluationBudget(max_provider_requests=1, max_total_tokens=1),
        parameters={},
    )

    with pytest.raises(EvaluationValidationError):
        await service.submit(unknown, idempotency_key="key-unknown")
    incompatible = EvaluationRequest(
        evaluation_type="fake-eval",
        provider="mistral",
        target_model="small-model",
        dataset_ref="dataset-v1",
        credential_pool="mistral-eval",
        execution_mode=ExecutionMode.WORKFLOW_SHARDS,
        max_workers=1,
        max_attempts_per_unit=1,
        budget=EvaluationBudget(max_provider_requests=1, max_total_tokens=1),
        parameters={},
    )
    with pytest.raises(EvaluationValidationError):
        await service.submit(incompatible, idempotency_key="key-mode")

    assert await repository.list_recoverable_jobs() == ()
    assert plugin.preflight_calls == 0


@pytest.mark.asyncio
async def test_submit_replays_idempotently_and_rejects_request_hash_conflicts(
    tmp_path: Path,
) -> None:
    plugin = FakePlugin()
    service, repository = await service_with(tmp_path, plugin)

    first = await service.submit(request(), idempotency_key="same-key")
    replay = await service.submit(request(), idempotency_key="same-key")

    assert replay.job_id == first.job_id
    assert replay.state.value == "queued"
    assert (await repository.get_unit(first.job_id, "unit-1")) is not None
    changed = request(max_workers=2)
    with pytest.raises(EvaluationConflict):
        await service.submit(changed, idempotency_key="same-key")


@pytest.mark.asyncio
async def test_submit_persists_default_worker_resolution_and_safe_status(
    tmp_path: Path,
) -> None:
    plugin = FakePlugin(ready_work=2)
    service, _ = await service_with(tmp_path, plugin)

    job = await service.submit(request(), idempotency_key="default-workers")
    status = await service.get_status(job.job_id)

    assert status["requested_workers"] == 1
    assert status["effective_workers"] == 1
    assert status["state"] == "queued"
    assert set(status) == {
        "job_id",
        "state",
        "requested_workers",
        "effective_workers",
        "warnings",
        "cancel_requested",
        "created_at",
        "updated_at",
        "completed_at",
    }
    assert "secret-key" not in repr(status)
    assert await service.list_types() == (
        {
            "type": "fake-eval",
            "version": "1",
            "modes": ("request_batch",),
            "parameter_schema": {"type": "object"},
        },
    )


@pytest.mark.asyncio
async def test_result_conflicts_while_nonterminal_and_cancellation_is_idempotent(
    tmp_path: Path,
) -> None:
    service, _ = await service_with(tmp_path, FakePlugin())
    job = await service.submit(request(), idempotency_key="cancel-key")

    with pytest.raises(EvaluationResultConflict):
        await service.get_result(job.job_id)

    first = await service.request_cancel(job.job_id)
    replay = await service.request_cancel(job.job_id)

    assert first == replay
    assert first["state"] == "cancellation_requested"
