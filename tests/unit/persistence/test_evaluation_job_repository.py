import asyncio
import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from cowork_agent.features.batch_evaluation.contracts import (
    AttemptState,
    EvaluationBudget,
    EvaluationRequest,
    EvaluationWarning,
    ExecutionMode,
    FailureClass,
    JobState,
    StepState,
    UnitState,
    WorkUnit,
    WorkUnitOutcome,
)
from cowork_agent.persistence.repositories import evaluation_jobs
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


async def queue_job(repository: SQLiteEvaluationJobRepository, job_id: str) -> None:
    validating = await repository.transition_job(job_id, JobState.VALIDATING)
    await repository.transition_job(validating.job_id, JobState.QUEUED)


class TransactionOrderedRepository(SQLiteEvaluationJobRepository):
    """Expose test-only events around the actual SQLite writer-lock boundary."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.hold_operation: str | None = None
        self.writer_lock_acquired = threading.Event()
        self.claim_begin_attempted = threading.Event()
        self.cancellation_begin_attempted = threading.Event()
        self.release_writer_lock = threading.Event()
        self._claim_thread_id: int | None = None
        self._cancellation_thread_id: int | None = None
        self._after_write_lock_acquired_for_tests = self._hold_writer_lock

    def _claim_ready_unit_sync(self, job_id: str, worker_id: str) -> WorkUnit | None:
        self._claim_thread_id = threading.get_ident()
        return super()._claim_ready_unit_sync(job_id, worker_id)

    def _request_cancellation_sync(self, job_id: str) -> evaluation_jobs.EvaluationJob:
        self._cancellation_thread_id = threading.get_ident()
        return super()._request_cancellation_sync(job_id)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with super()._connect() as database:
            database.set_trace_callback(self._trace_sql)
            yield database

    def _trace_sql(self, statement: str) -> None:
        if statement.strip().upper() != "BEGIN IMMEDIATE":
            return
        thread_id = threading.get_ident()
        if thread_id == self._claim_thread_id:
            self.claim_begin_attempted.set()
        if thread_id == self._cancellation_thread_id:
            self.cancellation_begin_attempted.set()

    def _hold_writer_lock(self, operation: str) -> None:
        if operation != self.hold_operation:
            return
        self.writer_lock_acquired.set()
        if not self.release_writer_lock.wait(timeout=5):
            raise TimeoutError("test did not release the SQLite writer lock")


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
        await queue_job(repository, job.job_id)

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
        await queue_job(repository, job.job_id)
        assert await repository.claim_ready_unit(job.job_id, "worker-1") is not None

        attempt = await repository.start_attempt(
            job.job_id, "unit-1", "worker-1", "credential-1"
        )
        assert attempt.attempt_number == 1
        step = await repository.write_step(
            job.job_id,
            "unit-1",
            "worker-1",
            attempt.attempt_id,
            step_id="request-1",
            ordinal=0,
            state=StepState.RUNNING,
            safe_metadata={"request_id": "request-1"},
        )
        assert step.state is StepState.RUNNING
        completed = await repository.write_step(
            job.job_id,
            "unit-1",
            "worker-1",
            attempt.attempt_id,
            step_id="request-1",
            ordinal=0,
            state=StepState.SUCCEEDED,
            safe_metadata={"request_id": "request-1"},
        )
        assert completed.state is StepState.SUCCEEDED
        replayed = await repository.write_step(
            job.job_id,
            "unit-1",
            "worker-1",
            attempt.attempt_id,
            step_id="request-1",
            ordinal=0,
            state=StepState.SUCCEEDED,
            safe_metadata={"request_id": "request-1"},
        )
        assert replayed.state is StepState.SUCCEEDED
        with pytest.raises(InvalidStateTransition):
            await repository.write_step(
                job.job_id,
                "unit-1",
                "worker-1",
                attempt.attempt_id,
                step_id="request-1",
                ordinal=0,
                state=StepState.PENDING,
                safe_metadata={"request_id": "request-1"},
            )
        with pytest.raises(ValueError):
            await repository.write_step(
                job.job_id,
                "unit-1",
                "worker-1",
                attempt.attempt_id,
                step_id="private-step",
                ordinal=1,
                state=StepState.PENDING,
                safe_metadata={"question": "private evaluation content"},
            )

        recovered = await repository.recover_orphaned_attempts(job.job_id)
        assert recovered.unknown_attempt_ids == (attempt.attempt_id,)
        assert recovered.blocked_unit_ids == ("unit-1",)
        stored_attempt = await repository.get_attempt(attempt.attempt_id)
        assert stored_attempt is not None
        assert stored_attempt.state is AttemptState.UNKNOWN
        assert stored_attempt.failure_class is FailureClass.UNKNOWN
        assert await repository.claim_ready_unit(job.job_id, "worker-2") is None

    asyncio.run(scenario())


def test_recovery_only_requeues_claims_without_provider_attempts(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SQLiteEvaluationJobRepository(tmp_path / "evaluation-jobs.db")
        await repository.initialize()
        job, _ = await repository.create_or_get(request(), "recovery-key", "hash-a")
        await repository.add_units(
            job.job_id,
            (
                WorkUnit(unit_id="unit-no-attempt", ordinal=0, payload={"case_id": "case-1"}),
                WorkUnit(unit_id="unit-running", ordinal=1, payload={"case_id": "case-2"}),
                WorkUnit(unit_id="unit-terminal", ordinal=2, payload={"case_id": "case-3"}),
            ),
        )
        await queue_job(repository, job.job_id)
        assert await repository.claim_ready_unit(job.job_id, "worker-safe") is not None
        assert await repository.claim_ready_unit(job.job_id, "worker-running") is not None
        assert await repository.claim_ready_unit(job.job_id, "worker-terminal") is not None
        running = await repository.start_attempt(
            job.job_id, "unit-running", "worker-running", "credential-1"
        )
        terminal = await repository.start_attempt(
            job.job_id, "unit-terminal", "worker-terminal", "credential-1"
        )
        await repository.finish_attempt(
            terminal.attempt_id,
            AttemptState.SUCCEEDED,
            worker_id="worker-terminal",
        )

        recovered = await repository.recover_orphaned_attempts(job.job_id)

        assert recovered.requeued_unit_ids == ("unit-no-attempt",)
        assert recovered.unknown_attempt_ids == (running.attempt_id,)
        assert recovered.blocked_unit_ids == ("unit-running", "unit-terminal")
        assert (await repository.get_unit(job.job_id, "unit-no-attempt")).claimed_by is None  # type: ignore[union-attr]
        assert [unit.unit_id for unit in await repository.list_running_units(job.job_id)] == [
            "unit-running",
            "unit-terminal",
        ]
        attempts = await repository.list_attempts(job.job_id, "unit-running")
        assert attempts == (
            (await repository.get_attempt(running.attempt_id)),  # type: ignore[arg-type]
        )
        assert attempts[0].state is AttemptState.UNKNOWN
        assert await repository.claim_ready_unit(job.job_id, "worker-replay") == WorkUnit(
            unit_id="unit-no-attempt", ordinal=0, payload={"case_id": "case-1"}
        )
        with pytest.raises(InvalidStateTransition):
            await repository.start_attempt(
                job.job_id, "unit-running", "worker-running", "credential-1"
            )

    asyncio.run(scenario())


def test_claim_and_attempt_ownership_are_persisted_and_enforced(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SQLiteEvaluationJobRepository(tmp_path / "evaluation-jobs.db")
        await repository.initialize()
        job, _ = await repository.create_or_get(request(), "ownership-key", "hash-a")
        await repository.add_units(
            job.job_id,
            (WorkUnit(unit_id="unit-1", ordinal=0, payload={"case_id": "case-1"}),),
        )
        await queue_job(repository, job.job_id)
        assert await repository.claim_ready_unit(job.job_id, "worker-1") is not None
        claimed = await repository.get_unit(job.job_id, "unit-1")
        assert claimed is not None
        assert claimed.claimed_by == "worker-1"
        with pytest.raises(InvalidStateTransition):
            await repository.start_attempt(job.job_id, "unit-1", "worker-2", "credential-1")

        attempt = await repository.start_attempt(
            job.job_id, "unit-1", "worker-1", "credential-1"
        )
        assert attempt.worker_id == "worker-1"
        with pytest.raises(InvalidStateTransition):
            await repository.start_attempt(job.job_id, "unit-1", "worker-1", "credential-1")
        with pytest.raises(InvalidStateTransition):
            await repository.finish_attempt(
                attempt.attempt_id, AttemptState.FAILED, worker_id="worker-2"
            )
        assert (
            await repository.finish_attempt(
                attempt.attempt_id, AttemptState.FAILED, worker_id="worker-1"
            )
        ).state is AttemptState.FAILED

    asyncio.run(scenario())


def test_job_reads_preserve_safe_request_and_warnings_by_default(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SQLiteEvaluationJobRepository(tmp_path / "evaluation-jobs.db")
        await repository.initialize()
        submitted = request()
        job, _ = await repository.create_or_get(submitted, "read-key", "hash-a")
        warning = EvaluationWarning(
            code="WORKER_COUNT_REDUCED",
            message="Worker count was reduced because fewer credentials are healthy.",
            details={"requested_workers": 2, "available": 1},
        )
        validating = await repository.transition_job(
            job.job_id, JobState.VALIDATING, warnings=(warning,)
        )
        queued = await repository.transition_job(validating.job_id, JobState.QUEUED)

        assert queued.request == submitted
        assert queued.warnings == (warning,)
        assert await repository.list_recoverable_jobs() == (queued,)

    asyncio.run(scenario())


def test_job_reads_reject_tampered_or_unknown_warning_messages(tmp_path: Path) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "evaluation-jobs.db"
        repository = SQLiteEvaluationJobRepository(database_path)
        await repository.initialize()
        job, _ = await repository.create_or_get(request(), "legacy-warning-key", "hash-a")
        for payload in (
            '[{"code":"WORKER_COUNT_REDUCED","message":"raw provider error",'
            '"details":{"requested_workers":2}}]',
            '[{"code":"UNKNOWN_WARNING","message":"Unknown warning text",'
            '"details":{"requested_workers":2}}]',
            '[{"code":"WORKER_COUNT_REDUCED","message":'
            '"Worker count was reduced because fewer credentials are healthy.",'
            '"details":{"requested_workers":2},"extra":1}]',
            '[{"code":"WORKER_COUNT_REDUCED","details":{"token":"private"}}]',
            '[{"code":"WORKER_COUNT_REDUCED","details":{"requested_workers":{}}}]',
        ):
            with sqlite3.connect(database_path) as database:
                database.execute(
                    "UPDATE evaluation_jobs SET warnings_json = ? WHERE job_id = ?",
                    (payload, job.job_id),
                )
            with pytest.raises(ValueError):
                await repository.get_job(job.job_id)

    asyncio.run(scenario())


def test_legacy_warning_rows_are_readable_and_rewritten_in_canonical_form(tmp_path: Path) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "evaluation-jobs.db"
        repository = SQLiteEvaluationJobRepository(database_path)
        await repository.initialize()
        job, _ = await repository.create_or_get(request(), "legacy-warning-key", "hash-a")
        legacy_warning_json = (
            '[{"code":"WORKER_COUNT_REDUCED",'
            '"details":{"requested_workers":2,"effective_workers":1}}]'
        )
        with sqlite3.connect(database_path) as database:
            database.execute(
                "UPDATE evaluation_jobs SET warnings_json = ? WHERE job_id = ?",
                (legacy_warning_json, job.job_id),
            )

        legacy = await repository.get_job(job.job_id)
        assert legacy is not None
        assert legacy.warnings == (
            EvaluationWarning(
                code="WORKER_COUNT_REDUCED",
                details={"requested_workers": 2, "effective_workers": 1},
            ),
        )

        updated = await repository.transition_job(job.job_id, JobState.VALIDATING)
        assert updated.warnings == legacy.warnings
        with sqlite3.connect(database_path) as database:
            row = database.execute(
                "SELECT warnings_json FROM evaluation_jobs WHERE job_id = ?", (job.job_id,)
            ).fetchone()
        assert row is not None
        assert json.loads(str(row[0])) == [
            {
                "code": "WORKER_COUNT_REDUCED",
                "message": "Worker count was reduced because fewer credentials are healthy.",
                "details": {"effective_workers": 1, "requested_workers": 2},
            }
        ]

    asyncio.run(scenario())


def test_cancelled_transition_replay_is_idempotent(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SQLiteEvaluationJobRepository(tmp_path / "evaluation-jobs.db")
        await repository.initialize()
        job, _ = await repository.create_or_get(request(), "cancelled-key", "hash-a")
        requested = await repository.request_cancellation(job.job_id)
        cancelled = await repository.transition_job(requested.job_id, JobState.CANCELLED)

        assert await repository.transition_job(cancelled.job_id, JobState.CANCELLED) == cancelled

    asyncio.run(scenario())


def test_step_metadata_rejects_non_finite_json_values(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SQLiteEvaluationJobRepository(tmp_path / "evaluation-jobs.db")
        await repository.initialize()
        job, _ = await repository.create_or_get(request(), "non-finite-key", "hash-a")
        await repository.add_units(
            job.job_id,
            (WorkUnit(unit_id="unit-1", ordinal=0, payload={"case_id": "case-1"}),),
        )
        await queue_job(repository, job.job_id)
        assert await repository.claim_ready_unit(job.job_id, "worker-1") is not None
        attempt = await repository.start_attempt(
            job.job_id, "unit-1", "worker-1", "credential-1"
        )

        with pytest.raises(ValueError):
            await repository.write_step(
                job.job_id,
                "unit-1",
                "worker-1",
                attempt.attempt_id,
                step_id="request-1",
                ordinal=0,
                state=StepState.RUNNING,
                safe_metadata={"score": float("nan")},
            )

    asyncio.run(scenario())


def test_unit_read_rejects_exponent_overflow_without_exposing_raw_json(tmp_path: Path) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "evaluation-jobs.db"
        repository = SQLiteEvaluationJobRepository(database_path)
        await repository.initialize()
        job, _ = await repository.create_or_get(request(), "overflow-key", "hash-a")
        await repository.add_units(
            job.job_id,
            (WorkUnit(unit_id="unit-1", ordinal=0, payload={"case_id": "case-1"}),),
        )
        with sqlite3.connect(database_path) as database:
            database.execute(
                "UPDATE evaluation_units SET safe_payload_json = "
                "? WHERE job_id = ? AND unit_id = ?",
                ('{"case_id":"case-1","score":1.25}', job.job_id, "unit-1"),
            )
        finite = await repository.get_unit(job.job_id, "unit-1")
        assert finite is not None
        assert finite.payload == {"case_id": "case-1", "score": 1.25}

        with sqlite3.connect(database_path) as database:
            database.execute(
                "UPDATE evaluation_units SET safe_payload_json = "
                "? WHERE job_id = ? AND unit_id = ?",
                ('{"case_id":1e9999}', job.job_id, "unit-1"),
            )
        with pytest.raises(ValueError) as error:
            await repository.get_unit(job.job_id, "unit-1")

        assert "1e9999" not in str(error.value)

    asyncio.run(scenario())


def test_claim_requires_an_executable_job_and_stops_after_cancellation(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SQLiteEvaluationJobRepository(tmp_path / "evaluation-jobs.db")
        await repository.initialize()
        job, _ = await repository.create_or_get(request(), "claim-state-key", "hash-a")
        await repository.add_units(
            job.job_id,
            (
                WorkUnit(unit_id="unit-1", ordinal=0, payload={"case_id": "case-1"}),
                WorkUnit(unit_id="unit-2", ordinal=1, payload={"case_id": "case-2"}),
            ),
        )

        assert await repository.claim_ready_unit(job.job_id, "worker-1") is None
        await queue_job(repository, job.job_id)
        assert (await repository.claim_ready_unit(job.job_id, "worker-1")).unit_id == "unit-1"  # type: ignore[union-attr]

        cancelled = await repository.request_cancellation(job.job_id)
        assert cancelled.state is JobState.CANCELLATION_REQUESTED
        assert await repository.claim_ready_unit(job.job_id, "worker-2") is None
        remaining = await repository.get_unit(job.job_id, "unit-2")
        assert remaining is not None
        assert remaining.state is UnitState.READY

    asyncio.run(scenario())


def test_specific_unit_claim_is_atomic_and_respects_ready_and_cancellation_state(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        repository = SQLiteEvaluationJobRepository(tmp_path / "evaluation-jobs.db")
        await repository.initialize()
        job, _ = await repository.create_or_get(request(), "specific-claim-key", "hash-a")
        await repository.add_units(
            job.job_id,
            (
                WorkUnit(unit_id="unit-1", ordinal=0, payload={"case_id": "case-1"}),
                WorkUnit(unit_id="unit-2", ordinal=1, payload={"case_id": "case-2"}),
                WorkUnit(unit_id="unit-3", ordinal=2, payload={"case_id": "case-3"}),
            ),
        )

        assert await repository.claim_ready_unit_by_id(job.job_id, "unit-1", "worker-1") is None
        await queue_job(repository, job.job_id)
        assert await repository.claim_ready_unit_by_id(job.job_id, "missing", "worker-1") is None
        claimed = await repository.claim_ready_unit_by_id(job.job_id, "unit-2", "worker-1")
        assert claimed == WorkUnit(unit_id="unit-2", ordinal=1, payload={"case_id": "case-2"})
        assert await repository.claim_ready_unit_by_id(job.job_id, "unit-2", "worker-2") is None

        competing = await asyncio.gather(
            repository.claim_ready_unit_by_id(job.job_id, "unit-1", "worker-3"),
            repository.claim_ready_unit_by_id(job.job_id, "unit-1", "worker-4"),
        )
        assert sum(item is not None for item in competing) == 1
        await repository.request_cancellation(job.job_id)
        assert await repository.claim_ready_unit_by_id(job.job_id, "unit-3", "worker-5") is None

    asyncio.run(scenario())


def test_cancellation_commit_prevents_claim_after_sqlite_writer_lock_ordering(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        repository = TransactionOrderedRepository(tmp_path / "evaluation-jobs.db")
        await repository.initialize()
        job, _ = await repository.create_or_get(request(), "claim-race-key", "hash-a")
        await repository.add_units(
            job.job_id,
            (WorkUnit(unit_id="unit-1", ordinal=0, payload={"case_id": "case-1"}),),
        )
        await queue_job(repository, job.job_id)

        repository.hold_operation = "cancellation"
        cancellation = asyncio.create_task(repository.request_cancellation(job.job_id))
        assert await asyncio.to_thread(repository.writer_lock_acquired.wait, 5)
        claim = asyncio.create_task(repository.claim_ready_unit(job.job_id, "worker-1"))
        assert await asyncio.to_thread(repository.claim_begin_attempted.wait, 5)
        repository.release_writer_lock.set()

        assert (await cancellation).state is JobState.CANCELLATION_REQUESTED
        assert await claim is None
        unit = await repository.get_unit(job.job_id, "unit-1")
        assert unit is not None
        assert unit.state is UnitState.READY
        assert unit.claimed_by is None

    asyncio.run(scenario())


def test_claim_commit_preserves_single_owner_before_later_cancellation(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = TransactionOrderedRepository(tmp_path / "evaluation-jobs.db")
        await repository.initialize()
        job, _ = await repository.create_or_get(request(), "claim-first-key", "hash-a")
        await repository.add_units(
            job.job_id,
            (WorkUnit(unit_id="unit-1", ordinal=0, payload={"case_id": "case-1"}),),
        )
        await queue_job(repository, job.job_id)

        repository.hold_operation = "claim"
        claim_task = asyncio.create_task(repository.claim_ready_unit(job.job_id, "worker-1"))
        assert await asyncio.to_thread(repository.writer_lock_acquired.wait, 5)
        cancellation_task = asyncio.create_task(repository.request_cancellation(job.job_id))
        assert await asyncio.to_thread(repository.cancellation_begin_attempted.wait, 5)
        repository.release_writer_lock.set()

        assert await claim_task == WorkUnit(
            unit_id="unit-1", ordinal=0, payload={"case_id": "case-1"}
        )
        assert (await cancellation_task).state is JobState.CANCELLATION_REQUESTED
        unit = await repository.get_unit(job.job_id, "unit-1")
        assert unit is not None
        assert unit.state is UnitState.RUNNING
        assert unit.claimed_by == "worker-1"
        assert await repository.claim_ready_unit(job.job_id, "worker-2") is None
        assert await repository.list_running_units(job.job_id) == (unit,)

    asyncio.run(scenario())


def test_step_writes_require_the_live_attempt_owner_and_unit(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SQLiteEvaluationJobRepository(tmp_path / "evaluation-jobs.db")
        await repository.initialize()
        job, _ = await repository.create_or_get(request(), "step-owner-key", "hash-a")
        await repository.add_units(
            job.job_id,
            (
                WorkUnit(unit_id="unit-1", ordinal=0, payload={"case_id": "case-1"}),
                WorkUnit(unit_id="unit-2", ordinal=1, payload={"case_id": "case-2"}),
            ),
        )
        await queue_job(repository, job.job_id)
        assert await repository.claim_ready_unit(job.job_id, "worker-1") is not None
        assert await repository.claim_ready_unit(job.job_id, "worker-2") is not None

        with pytest.raises(InvalidStateTransition):
            await repository.write_step(
                job.job_id,
                "unit-1",
                "worker-1",
                "attempt-missing",
                step_id="request-1",
                ordinal=0,
                state=StepState.RUNNING,
                safe_metadata={"request_id": "request-1"},
            )
        first = await repository.start_attempt(
            job.job_id, "unit-1", "worker-1", "credential-1"
        )
        with pytest.raises(InvalidStateTransition):
            await repository.write_step(
                job.job_id,
                "unit-1",
                "worker-2",
                first.attempt_id,
                step_id="request-1",
                ordinal=0,
                state=StepState.RUNNING,
                safe_metadata={"request_id": "request-1"},
            )
        with pytest.raises(InvalidStateTransition):
            await repository.write_step(
                job.job_id,
                "unit-2",
                "worker-1",
                first.attempt_id,
                step_id="request-1",
                ordinal=0,
                state=StepState.RUNNING,
                safe_metadata={"request_id": "request-1"},
            )
        await repository.finish_attempt(first.attempt_id, AttemptState.FAILED, worker_id="worker-1")
        with pytest.raises(InvalidStateTransition):
            await repository.write_step(
                job.job_id,
                "unit-1",
                "worker-1",
                first.attempt_id,
                step_id="request-1",
                ordinal=0,
                state=StepState.RUNNING,
                safe_metadata={"request_id": "request-1"},
            )

        second = await repository.start_attempt(
            job.job_id, "unit-2", "worker-2", "credential-1"
        )
        await repository.recover_orphaned_attempts(job.job_id)
        with pytest.raises(InvalidStateTransition):
            await repository.write_step(
                job.job_id,
                "unit-2",
                "worker-2",
                second.attempt_id,
                step_id="request-2",
                ordinal=0,
                state=StepState.RUNNING,
                safe_metadata={"request_id": "request-2"},
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("constant", ("NaN", "Infinity", "-Infinity"))
def test_repository_rejects_handcrafted_non_finite_persisted_json(
    tmp_path: Path, constant: str
) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "evaluation-jobs.db"
        repository = SQLiteEvaluationJobRepository(database_path)
        await repository.initialize()
        job, _ = await repository.create_or_get(request(), "crafted-json-key", "hash-a")
        request_json = (
            '{"evaluation_type":"memory_eval","provider":"openai","target_model":"model_1",'
            '"dataset_ref":"probe_set_1","credential_pool":"eval_pool",'
            '"execution_mode":"workflow_shards","max_workers":2,'
            '"max_attempts_per_unit":2,"budget":{"max_provider_requests":10,'
            '"max_total_tokens":1000},"parameters":{"threshold":' + constant + "}}"
        )
        with sqlite3.connect(database_path) as database:
            database.execute(
                "UPDATE evaluation_jobs SET request_json = ? WHERE job_id = ?",
                (request_json, job.job_id),
            )

        with pytest.raises(ValueError):
            await repository.get_job(job.job_id)

    asyncio.run(scenario())


def test_repository_closes_every_sqlite_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class TrackingConnection(sqlite3.Connection):
        was_closed: bool

        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            self.was_closed = False

        def close(self) -> None:
            self.was_closed = True
            super().close()

    connections: list[TrackingConnection] = []
    connect = sqlite3.connect

    def tracking_connect(*args: object, **kwargs: object) -> TrackingConnection:
        kwargs["factory"] = TrackingConnection
        database = connect(*args, **kwargs)
        assert isinstance(database, TrackingConnection)
        connections.append(database)
        return database

    monkeypatch.setattr(evaluation_jobs.sqlite3, "connect", tracking_connect)

    async def scenario() -> None:
        repository = SQLiteEvaluationJobRepository(tmp_path / "evaluation-jobs.db")
        await repository.initialize()
        job, _ = await repository.create_or_get(request(), "connection-key", "hash-a")
        assert await repository.get_job(job.job_id) is not None

    asyncio.run(scenario())

    assert connections
    assert all(database.was_closed for database in connections)
