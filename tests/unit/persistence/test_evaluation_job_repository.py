import asyncio
from pathlib import Path

import pytest

from cowork_agent.features.batch_evaluation.contracts import (
    AttemptState,
    EvaluationBudget,
    EvaluationRequest,
    ExecutionMode,
    FailureClass,
    JobState,
    StepState,
    UnitState,
    WorkUnit,
    WorkUnitOutcome,
)
from cowork_agent.persistence.repositories.evaluation_jobs import (
    IdempotencyConflict,
    InvalidStateTransition,
    SQLiteEvaluationJobRepository,
)


def request() -> EvaluationRequest:
    return EvaluationRequest(
        evaluation_type="memory_eval",
        provider="openai",
        target_model="model_1",
        dataset_ref="probe_set_1",
        credential_pool="eval_pool",
        execution_mode=ExecutionMode.WORKFLOW_SHARDS,
        max_workers=2,
        max_attempts_per_unit=2,
        budget=EvaluationBudget(max_provider_requests=10, max_total_tokens=1_000),
        parameters={"version_id": "v1"},
    )


def test_idempotency_is_atomic_and_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SQLiteEvaluationJobRepository(tmp_path / "evaluation-jobs.db")
        await repository.initialize()

        first, created = await repository.create_or_get(request(), "same-key", "hash-a")
        replay, replay_created = await repository.create_or_get(request(), "same-key", "hash-a")

        assert created is True
        assert replay_created is False
        assert replay.job_id == first.job_id
        with pytest.raises(IdempotencyConflict):
            await repository.create_or_get(request(), "same-key", "hash-b")

    asyncio.run(scenario())


def test_job_transitions_are_monotonic_and_cancellation_is_idempotent(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SQLiteEvaluationJobRepository(tmp_path / "evaluation-jobs.db")
        await repository.initialize()
        job, _ = await repository.create_or_get(request(), "transition-key", "hash-a")

        validating = await repository.transition_job(job.job_id, JobState.VALIDATING)
        queued = await repository.transition_job(validating.job_id, JobState.QUEUED)
        assert queued.state is JobState.QUEUED
        with pytest.raises(InvalidStateTransition):
            await repository.transition_job(queued.job_id, JobState.ACCEPTED)

        requested = await repository.request_cancellation(job.job_id)
        replayed = await repository.request_cancellation(job.job_id)
        assert requested.state is JobState.CANCELLATION_REQUESTED
        assert replayed.cancel_requested_at == requested.cancel_requested_at

    asyncio.run(scenario())


def test_unit_claims_are_atomic_and_completed_units_are_not_replayed(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SQLiteEvaluationJobRepository(tmp_path / "evaluation-jobs.db")
        await repository.initialize()
        job, _ = await repository.create_or_get(request(), "unit-key", "hash-a")
        await repository.add_units(
            job.job_id,
            (
                WorkUnit(unit_id="unit-1", ordinal=0, payload={"case_id": "case-1"}),
                WorkUnit(unit_id="unit-2", ordinal=1, payload={"case_id": "case-2"}),
            ),
        )

        claims = await asyncio.gather(
            repository.claim_ready_unit(job.job_id, "worker-1"),
            repository.claim_ready_unit(job.job_id, "worker-2"),
        )
        assert {claim.unit_id for claim in claims if claim is not None} == {"unit-1", "unit-2"}

        await repository.complete_unit(
            job.job_id,
            WorkUnitOutcome(
                unit_id="unit-1",
                ordinal=0,
                state=UnitState.SUCCEEDED,
                provider_requests=1,
                total_tokens=10,
                private_result=object(),
            ),
        )
        await repository.complete_unit(
            job.job_id,
            WorkUnitOutcome(
                unit_id="unit-2",
                ordinal=1,
                state=UnitState.SUCCEEDED,
                provider_requests=1,
                total_tokens=10,
                private_result=object(),
            ),
        )
        assert await repository.claim_ready_unit(job.job_id, "worker-3") is None

    asyncio.run(scenario())


def test_attempt_steps_and_orphan_recovery_never_requeues_running_work(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SQLiteEvaluationJobRepository(tmp_path / "evaluation-jobs.db")
        await repository.initialize()
        job, _ = await repository.create_or_get(request(), "attempt-key", "hash-a")
        await repository.add_units(
            job.job_id,
            (WorkUnit(unit_id="unit-1", ordinal=0, payload={"case_id": "case-1"}),),
        )
        assert await repository.claim_ready_unit(job.job_id, "worker-1") is not None

        attempt = await repository.start_attempt(job.job_id, "unit-1", "credential-1")
        assert attempt.attempt_number == 1
        step = await repository.write_step(
            attempt.attempt_id,
            step_id="request-1",
            ordinal=0,
            state=StepState.RUNNING,
            safe_metadata={"request_id": "request-1"},
        )
        assert step.state is StepState.RUNNING
        completed = await repository.write_step(
            attempt.attempt_id,
            step_id="request-1",
            ordinal=0,
            state=StepState.SUCCEEDED,
            safe_metadata={"request_id": "request-1"},
        )
        assert completed.state is StepState.SUCCEEDED
        replayed = await repository.write_step(
            attempt.attempt_id,
            step_id="request-1",
            ordinal=0,
            state=StepState.SUCCEEDED,
            safe_metadata={"request_id": "request-1"},
        )
        assert replayed.state is StepState.SUCCEEDED
        with pytest.raises(InvalidStateTransition):
            await repository.write_step(
                attempt.attempt_id,
                step_id="request-1",
                ordinal=0,
                state=StepState.PENDING,
                safe_metadata={"request_id": "request-1"},
            )
        with pytest.raises(ValueError):
            await repository.write_step(
                attempt.attempt_id,
                step_id="private-step",
                ordinal=1,
                state=StepState.PENDING,
                safe_metadata={"question": "private evaluation content"},
            )

        recovered = await repository.recover_orphaned_attempts(job.job_id)
        assert recovered == (attempt.attempt_id,)
        stored_attempt = await repository.get_attempt(attempt.attempt_id)
        assert stored_attempt is not None
        assert stored_attempt.state is AttemptState.UNKNOWN
        assert stored_attempt.failure_class is FailureClass.UNKNOWN
        assert await repository.claim_ready_unit(job.job_id, "worker-2") is None

    asyncio.run(scenario())
