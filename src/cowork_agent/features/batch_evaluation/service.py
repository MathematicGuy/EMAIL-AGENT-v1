"""Submission and safe read operations for durable evaluation jobs."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Protocol, cast

from cowork_agent.features.batch_evaluation.artifacts import FilesystemEvaluationArtifactStore
from cowork_agent.features.batch_evaluation.contracts import (
    EvaluationPlugin,
    EvaluationRequest,
    ExecutionMode,
    FailureClass,
    JobState,
    PluginPlan,
    UnitState,
    WorkerResolution,
    WorkUnit,
    canonical_request_hash,
)
from cowork_agent.features.batch_evaluation.credentials import CredentialLeasingPool
from cowork_agent.features.batch_evaluation.planning import resolve_worker_count
from cowork_agent.features.batch_evaluation.registry import PluginRegistry
from cowork_agent.persistence.repositories.evaluation_jobs import (
    EvaluationAttempt,
    EvaluationJob,
    EvaluationUnit,
    IdempotencyConflict,
    InvalidStateTransition,
    SQLiteEvaluationJobRepository,
)

_TERMINAL_STATES = frozenset(
    {
        JobState.SUCCEEDED,
        JobState.PARTIALLY_SUCCEEDED,
        JobState.FAILED,
        JobState.CANCELLED,
    }
)


class EvaluationValidationError(ValueError):
    """A public request was invalid without disclosing private evaluator details."""


class EvaluationConflict(ValueError):
    """A safe conflict prevents duplicate or incompatible durable work."""


class EvaluationResultConflict(EvaluationConflict):
    """A result is not available while its evaluation job remains non-terminal."""


class EvaluationJobSupervisor(Protocol):
    """Schedule durable jobs after persistence and drive cancellation cleanup."""

    async def start(self, job_id: str) -> None: ...

    async def cancel(self, job_id: str) -> None: ...


class _StatusRepository(Protocol):
    """The durable reads required to build safe job-status aggregates."""

    async def list_units(self, job_id: str) -> tuple[EvaluationUnit, ...]: ...

    async def list_attempts(self, job_id: str) -> tuple[EvaluationAttempt, ...]: ...


class EvaluationJobService:
    """Validate work before durable creation, then expose metadata-only job views."""

    def __init__(
        self,
        *,
        registry: PluginRegistry,
        repository: SQLiteEvaluationJobRepository,
        credential_pool: CredentialLeasingPool,
        artifact_store: FilesystemEvaluationArtifactStore,
        supervisor: EvaluationJobSupervisor | None = None,
    ) -> None:
        self._registry = registry
        self._repository = repository
        self._credential_pool = credential_pool
        self._artifact_store = artifact_store
        self._supervisor = supervisor

    async def submit(self, request: EvaluationRequest, *, idempotency_key: str) -> EvaluationJob:
        """Preflight a typed request before atomically creating its durable job."""

        plugin = self._require_compatible_plugin(request)
        plan = await self._preflight(plugin, request)
        resolution = self._resolve_workers(request, plan)
        units = self._build_units(
            plugin,
            plan,
            resolution.effective_workers,
            request.execution_mode,
        )
        request_hash = canonical_request_hash(request)
        try:
            job, created = await self._repository.create_or_get(
                request,
                idempotency_key,
                request_hash,
            )
        except IdempotencyConflict as error:
            raise EvaluationConflict(
                "idempotency key conflicts with an existing evaluation job"
            ) from error
        if not created:
            return job

        warnings = () if resolution.warning is None else (resolution.warning,)
        validating = await self._repository.transition_job(
            job.job_id,
            JobState.VALIDATING,
            effective_workers=resolution.effective_workers,
            warnings=warnings,
        )
        await self._repository.add_units(validating.job_id, units)
        queued = await self._repository.transition_job(validating.job_id, JobState.QUEUED)
        if self._supervisor is not None:
            try:
                await self._supervisor.start(queued.job_id)
            except BaseException as error:
                if isinstance(error, KeyboardInterrupt | SystemExit | asyncio.CancelledError):
                    raise
                raise EvaluationConflict("evaluation job scheduling failed") from None
        return queued

    async def get_status(self, job_id: str) -> Mapping[str, object]:
        """Return the bounded public job lifecycle metadata for one job."""

        job = await self._require_job(job_id)
        return await self._status_view(job)

    async def get_result(self, job_id: str) -> Mapping[str, object]:
        """Return the public manifest only after the runner has made the job terminal."""

        job = await self._require_job(job_id)
        if job.state not in _TERMINAL_STATES:
            raise EvaluationResultConflict("evaluation result is not available yet")
        reference = self._artifact_store.manifest_reference(job.job_id)
        return self._artifact_store.read_manifest(reference)

    async def request_cancel(self, job_id: str) -> Mapping[str, object]:
        """Record a durable cancellation request without exposing execution details."""

        existing = await self._require_job(job_id)
        if existing.state in _TERMINAL_STATES:
            return await self._status_view(existing)
        try:
            if self._supervisor is None:
                job = await self._repository.request_cancellation(job_id)
            else:
                await self._supervisor.cancel(job_id)
                job = await self._require_job(job_id)
        except InvalidStateTransition as error:
            raise EvaluationConflict("evaluation job can no longer be cancelled") from error
        except BaseException as error:
            if isinstance(error, KeyboardInterrupt | SystemExit | asyncio.CancelledError):
                raise
            raise EvaluationConflict("evaluation job cancellation failed") from None
        return await self._status_view(job)

    async def list_types(self) -> tuple[Mapping[str, object], ...]:
        """List startup-registered plug-in metadata only."""

        return self._registry.list_types()

    def _require_compatible_plugin(self, request: EvaluationRequest) -> EvaluationPlugin:
        try:
            plugin = self._registry.require(request.evaluation_type)
        except (TypeError, ValueError) as error:
            raise EvaluationValidationError("evaluation type is not registered") from error
        if request.execution_mode not in plugin.supported_modes:
            raise EvaluationValidationError("evaluation type does not support this execution mode")
        return plugin

    async def _preflight(self, plugin: EvaluationPlugin, request: EvaluationRequest) -> PluginPlan:
        try:
            plan = await plugin.preflight(request)
        except BaseException as error:
            if isinstance(error, KeyboardInterrupt | SystemExit | asyncio.CancelledError):
                raise
            raise EvaluationValidationError("evaluation request did not pass preflight") from error
        if not isinstance(plan, PluginPlan):
            raise EvaluationValidationError("evaluation preflight returned an invalid plan")
        if plan.dataset_ref != request.dataset_ref:
            raise EvaluationValidationError("evaluation preflight returned an incompatible plan")
        return plan

    def _resolve_workers(
        self,
        request: EvaluationRequest,
        plan: PluginPlan,
    ) -> WorkerResolution:
        resolution = resolve_worker_count(
            request.max_workers,
            self._credential_pool.healthy_count,
            plan.ready_work,
        )
        if resolution.effective_workers < 1:
            raise EvaluationValidationError("no compatible evaluation workers are available")
        return resolution

    def _build_units(
        self,
        plugin: EvaluationPlugin,
        plan: PluginPlan,
        effective_workers: int,
        execution_mode: ExecutionMode,
    ) -> tuple[WorkUnit, ...]:
        try:
            units = plugin.build_work_units(plan, effective_workers)
        except BaseException as error:
            if isinstance(error, KeyboardInterrupt | SystemExit):
                raise
            raise EvaluationValidationError(
                "evaluation plan could not create work units"
            ) from error
        if not isinstance(units, tuple):
            raise EvaluationValidationError("evaluation plan has an invalid work-unit count")
        valid_count = (
            len(units) == plan.ready_work
            if execution_mode is ExecutionMode.REQUEST_BATCH
            else 0 < len(units) <= plan.ready_work
        )
        if not valid_count:
            raise EvaluationValidationError("evaluation plan has an invalid work-unit count")
        if any(not isinstance(unit, WorkUnit) for unit in units):
            raise EvaluationValidationError("evaluation plan has invalid work units")
        if len({unit.unit_id for unit in units}) != len(units):
            raise EvaluationValidationError("evaluation plan has duplicate work units")
        if tuple(unit.ordinal for unit in units) != tuple(range(len(units))):
            raise EvaluationValidationError("evaluation plan has unstable work-unit order")
        return units

    async def _require_job(self, job_id: str) -> EvaluationJob:
        job = await self._repository.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    async def _status_view(self, job: EvaluationJob) -> Mapping[str, object]:
        repository = cast(_StatusRepository, self._repository)
        units = await repository.list_units(job.job_id)
        attempts = await repository.list_attempts(job.job_id)
        return _status_view(job, units, attempts)


def _status_view(
    job: EvaluationJob,
    units: tuple[EvaluationUnit, ...],
    attempts: tuple[EvaluationAttempt, ...],
) -> Mapping[str, object]:
    progress = {state.value: 0 for state in UnitState}
    for unit in units:
        progress[unit.state.value] += 1
    failure_classes = {
        failure_class.value: sum(
            attempt.failure_class is failure_class for attempt in attempts
        )
        for failure_class in FailureClass
    }
    return MappingProxyType(
        {
            "job_id": job.job_id,
            "state": job.state.value,
            "progress": MappingProxyType({"total": len(units), **progress}),
            "attempts": MappingProxyType(
                {
                    "total": len(attempts),
                    "retries": sum(attempt.attempt_number > 1 for attempt in attempts),
                }
            ),
            "failure_classes": MappingProxyType(
                {
                    failure_class: count
                    for failure_class, count in failure_classes.items()
                    if count > 0
                }
            ),
            "requested_workers": job.requested_workers,
            "effective_workers": job.effective_workers,
            "warnings": tuple(
                MappingProxyType(
                    {
                        "code": warning.code,
                        "message": warning.message,
                        "details": warning.details,
                    }
                )
                for warning in job.warnings
            ),
            "cancel_requested": job.cancel_requested_at is not None,
            "created_at": _timestamp(job.created_at),
            "updated_at": _timestamp(job.updated_at),
            "completed_at": None if job.completed_at is None else _timestamp(job.completed_at),
        }
    )


def _timestamp(value: datetime) -> str:
    return value.isoformat()
