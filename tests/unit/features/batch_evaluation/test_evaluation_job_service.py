from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cowork_agent.features.batch_evaluation.artifacts import FilesystemEvaluationArtifactStore
from cowork_agent.features.batch_evaluation.contracts import (
    ArtifactBundle,
    AttemptState,
    CleanupOutcome,
    EvaluationBudget,
    EvaluationRequest,
    ExecutionMode,
    FailureClass,
    FailureClassification,
    JobState,
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
    EvaluationJobSupervisor,
    EvaluationResultConflict,
    EvaluationValidationError,
)
from cowork_agent.persistence.repositories.evaluation_jobs import (
    EvaluationAttempt,
    EvaluationJob,
    EvaluationUnit,
    SQLiteEvaluationJobRepository,
)


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
        if request.target_model != "small-model":
            raise ValueError("unsupported target model: private-model-name")
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


class StatusRepository:
    """Protocol-shaped store whose private records must never reach status output."""

    def __init__(
        self,
        job: EvaluationJob,
        units: tuple[EvaluationUnit, ...],
        attempts: tuple[EvaluationAttempt, ...],
    ) -> None:
        self.job = job
        self.units = units
        self.attempts = attempts

    async def get_job(self, job_id: str) -> EvaluationJob | None:
        return self.job if job_id == self.job.job_id else None

    async def list_units(self, job_id: str) -> tuple[EvaluationUnit, ...]:
        assert job_id == self.job.job_id
        return self.units

    async def list_attempts(self, job_id: str) -> tuple[EvaluationAttempt, ...]:
        assert job_id == self.job.job_id
        return self.attempts

    async def request_cancellation(self, job_id: str) -> EvaluationJob:
        if job_id != self.job.job_id:
            raise KeyError(job_id)
        if self.job.state in {
            JobState.SUCCEEDED,
            JobState.PARTIALLY_SUCCEEDED,
            JobState.FAILED,
            JobState.CANCELLED,
        }:
            raise AssertionError(f"terminal job {job_id} must not request cancellation")
        if self.job.state is not JobState.CANCELLATION_REQUESTED:
            self.job = replace(
                self.job,
                state=JobState.CANCELLATION_REQUESTED,
                cancel_requested_at=self.job.updated_at,
            )
        return self.job


class RecordingSupervisor:
    def __init__(self) -> None:
        self.repository: SQLiteEvaluationJobRepository | StatusRepository | None = None
        self.started: list[str] = []
        self.cancelled: list[str] = []

    async def start(self, job_id: str) -> None:
        assert self.repository is not None
        job = await self.repository.get_job(job_id)
        assert job is not None and job.state is JobState.QUEUED
        assert await self.repository.list_units(job_id)
        self.started.append(job_id)

    async def cancel(self, job_id: str) -> None:
        assert self.repository is not None
        self.cancelled.append(job_id)
        await self.repository.request_cancellation(job_id)


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
    supervisor: EvaluationJobSupervisor | None = None,
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
        supervisor=supervisor,
    )
    return service, repository


def status_job(*, state: JobState = JobState.QUEUED) -> EvaluationJob:
    timestamp = datetime(2026, 8, 23, tzinfo=UTC)
    return EvaluationJob(
        job_id="status-job",
        request=request(),
        state=state,
        requested_workers=3,
        effective_workers=2,
        warnings=(),
        cancel_requested_at=None,
        created_at=timestamp,
        updated_at=timestamp,
        completed_at=timestamp if state in {JobState.SUCCEEDED, JobState.FAILED} else None,
    )


def status_service(
    tmp_path: Path,
    repository: StatusRepository,
    supervisor: EvaluationJobSupervisor | None = None,
) -> EvaluationJobService:
    return EvaluationJobService(
        registry=PluginRegistry(),
        repository=repository,  # type: ignore[arg-type]
        credential_pool=CredentialLeasingPool.from_env(
            "MISTRAL_API_KEY", {"MISTRAL_API_KEY": "secret-key"}
        ),
        artifact_store=FilesystemEvaluationArtifactStore(tmp_path / "artifacts"),
        supervisor=supervisor,
    )


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
async def test_submit_rejects_unknown_target_model_during_plugin_preflight_without_spend(
    tmp_path: Path,
) -> None:
    plugin = FakePlugin()
    service, repository = await service_with(tmp_path, plugin)
    unknown_model = EvaluationRequest(
        evaluation_type="fake-eval",
        provider="mistral",
        target_model="unknown-model",
        dataset_ref="dataset-v1",
        credential_pool="mistral-eval",
        execution_mode=ExecutionMode.REQUEST_BATCH,
        max_workers=1,
        max_attempts_per_unit=1,
        budget=EvaluationBudget(max_provider_requests=1, max_total_tokens=1),
        parameters={},
    )

    with pytest.raises(EvaluationValidationError) as error:
        await service.submit(unknown_model, idempotency_key="unknown-model")

    assert "private-model-name" not in str(error.value)
    assert plugin.preflight_calls == 1
    assert plugin.provider_calls == 0
    assert await repository.list_recoverable_jobs() == ()


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
async def test_submit_schedules_once_after_persisting_and_replay_does_not_reschedule(
    tmp_path: Path,
) -> None:
    supervisor = RecordingSupervisor()
    service, repository = await service_with(tmp_path, FakePlugin(), supervisor)
    supervisor.repository = repository

    first = await service.submit(request(), idempotency_key="scheduled")
    replay = await service.submit(request(), idempotency_key="scheduled")

    assert replay.job_id == first.job_id
    assert supervisor.started == [first.job_id]


@pytest.mark.asyncio
async def test_submit_persists_default_worker_resolution_and_lists_types(
    tmp_path: Path,
) -> None:
    plugin = FakePlugin(ready_work=2)
    service, _ = await service_with(tmp_path, plugin)

    job = await service.submit(request(), idempotency_key="default-workers")
    assert job.requested_workers == 1
    assert job.effective_workers == 1
    assert job.state is JobState.QUEUED
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

    cancellation = status_service(tmp_path, StatusRepository(status_job(), (), ()))
    first = await cancellation.request_cancel("status-job")
    replay = await cancellation.request_cancel("status-job")

    assert first == replay
    assert first["state"] == "cancellation_requested"
    assert first["cancel_requested"] is True


@pytest.mark.asyncio
async def test_request_cancel_routes_active_jobs_through_the_supervisor(
    tmp_path: Path,
) -> None:
    repository = StatusRepository(status_job(), (), ())
    supervisor = RecordingSupervisor()
    supervisor.repository = repository
    service = status_service(tmp_path, repository, supervisor)

    status = await service.request_cancel("status-job")

    assert supervisor.cancelled == ["status-job"]
    assert status["state"] == "cancellation_requested"


@pytest.mark.asyncio
async def test_get_status_reports_evolving_safe_unit_and_attempt_progress(
    tmp_path: Path,
) -> None:
    job = status_job()
    repository = StatusRepository(
        job,
        (
            EvaluationUnit(
                job_id=job.job_id,
                unit_id="unit-1",
                ordinal=0,
                state=UnitState.READY,
                claimed_by=None,
                provider_requests=0,
                total_tokens=0,
                outcome_ref=None,
                payload={"prompt": "private"},
            ),
            EvaluationUnit(
                job_id=job.job_id,
                unit_id="unit-2",
                ordinal=1,
                state=UnitState.RUNNING,
                claimed_by="worker-1",
                provider_requests=0,
                total_tokens=0,
                outcome_ref=None,
                payload={"reply": "private"},
            ),
            EvaluationUnit(
                job_id=job.job_id,
                unit_id="unit-3",
                ordinal=2,
                state=UnitState.SUCCEEDED,
                claimed_by="worker-2",
                provider_requests=0,
                total_tokens=0,
                outcome_ref=None,
                payload={"key": "private"},
            ),
            EvaluationUnit(
                job_id=job.job_id,
                unit_id="unit-4",
                ordinal=3,
                state=UnitState.FAILED,
                claimed_by="worker-2",
                provider_requests=0,
                total_tokens=0,
                outcome_ref=None,
                payload={"outcome_ref": "private"},
            ),
            EvaluationUnit(
                job_id=job.job_id,
                unit_id="unit-5",
                ordinal=4,
                state=UnitState.CANCELLED,
                claimed_by="worker-3",
                provider_requests=0,
                total_tokens=0,
                outcome_ref=None,
                payload={"payload": "private"},
            ),
        ),
        (
            EvaluationAttempt(
                "attempt-1", job.job_id, "unit-3", "worker-2", "credential-private", 1,
                AttemptState.SUCCEEDED, None, job.created_at, job.updated_at,
            ),
            EvaluationAttempt(
                "attempt-2", job.job_id, "unit-4", "worker-2", "credential-private", 1,
                AttemptState.FAILED, FailureClass.PROVIDER, job.created_at, job.updated_at,
            ),
            EvaluationAttempt(
                "attempt-3", job.job_id, "unit-4", "worker-2", "credential-private", 2,
                AttemptState.UNKNOWN, FailureClass.UNKNOWN, job.created_at, job.updated_at,
            ),
        ),
    )
    service = status_service(tmp_path, repository)

    status = await service.get_status(job.job_id)

    assert status == {
        "job_id": "status-job",
        "state": "queued",
        "progress": {
            "total": 5,
            "ready": 1,
            "running": 1,
            "succeeded": 1,
            "failed": 1,
            "cancelled": 1,
        },
        "attempts": {"total": 3, "retries": 1},
        "failure_classes": {"provider": 1, "unknown": 1},
        "requested_workers": 3,
        "effective_workers": 2,
        "warnings": (),
        "cancel_requested": False,
        "created_at": "2026-08-23T00:00:00+00:00",
        "updated_at": "2026-08-23T00:00:00+00:00",
        "completed_at": None,
    }
    assert all(
        private not in repr(status)
        for private in ("prompt", "reply", "key", "payload", "outcome_ref", "credential-private")
    )

    repository.units = (
        EvaluationUnit(
            job_id=job.job_id,
            unit_id="unit-1",
            ordinal=0,
            state=UnitState.SUCCEEDED,
            claimed_by="worker-1",
            provider_requests=0,
            total_tokens=0,
            outcome_ref=None,
            payload={},
        ),
        EvaluationUnit(
            job_id=job.job_id,
            unit_id="unit-2",
            ordinal=1,
            state=UnitState.SUCCEEDED,
            claimed_by="worker-1",
            provider_requests=0,
            total_tokens=0,
            outcome_ref=None,
            payload={},
        ),
        *repository.units[2:],
    )
    repository.attempts = (
        *repository.attempts,
        EvaluationAttempt(
            "attempt-4",
            job.job_id,
            "unit-1",
            "worker-1",
            "credential-private",
            2,
            AttemptState.SUCCEEDED,
            None,
            job.created_at,
            job.updated_at,
        ),
    )

    evolved = await service.get_status(job.job_id)

    assert evolved["progress"] == {
        "total": 5,
        "ready": 0,
        "running": 0,
        "succeeded": 3,
        "failed": 1,
        "cancelled": 1,
    }
    assert evolved["attempts"] == {"total": 4, "retries": 2}
    assert evolved["failure_classes"] == {"provider": 1, "unknown": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    (
        JobState.SUCCEEDED,
        JobState.PARTIALLY_SUCCEEDED,
        JobState.FAILED,
        JobState.CANCELLED,
    ),
)
async def test_request_cancel_returns_current_status_for_every_terminal_state(
    tmp_path: Path,
    state: JobState,
) -> None:
    job = status_job(state=state)
    service = status_service(tmp_path, StatusRepository(job, (), ()))

    status = await service.request_cancel(job.job_id)

    assert status["state"] == state.value
    assert status["progress"] == {
        "total": 0,
        "ready": 0,
        "running": 0,
        "succeeded": 0,
        "failed": 0,
        "cancelled": 0,
    }
