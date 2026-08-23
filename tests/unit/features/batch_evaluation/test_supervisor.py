from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import pytest

from cowork_agent.features.batch_evaluation.contracts import (
    AttemptState,
    EvaluationBudget,
    EvaluationRequest,
    ExecutionMode,
    JobState,
    UnitState,
)
from cowork_agent.features.batch_evaluation.supervisor import (
    EvaluationSupervisor,
    EvaluationSupervisorError,
)
from cowork_agent.persistence.repositories.evaluation_jobs import (
    EvaluationJob,
    EvaluationRecovery,
)

_TERMINAL_STATES = frozenset(
    {
        JobState.SUCCEEDED,
        JobState.PARTIALLY_SUCCEEDED,
        JobState.FAILED,
        JobState.CANCELLED,
    }
)


def _request() -> EvaluationRequest:
    return EvaluationRequest(
        evaluation_type="fake-eval",
        provider="mistral",
        target_model="small-model",
        dataset_ref="dataset-v1",
        credential_pool="mistral-eval",
        execution_mode=ExecutionMode.REQUEST_BATCH,
        max_workers=1,
        max_attempts_per_unit=1,
        budget=EvaluationBudget(max_provider_requests=10, max_total_tokens=1000),
        parameters={},
    )


def _job(job_id: str, state: JobState = JobState.QUEUED) -> EvaluationJob:
    timestamp = datetime(2026, 8, 23, tzinfo=UTC)
    return EvaluationJob(
        job_id=job_id,
        request=_request(),
        state=state,
        requested_workers=1,
        effective_workers=1,
        warnings=(),
        cancel_requested_at=None,
        created_at=timestamp,
        updated_at=timestamp,
        completed_at=None,
    )


@dataclass
class _RecoveryUnit:
    state: UnitState
    attempt_ids: tuple[str, ...]


class FakeStore:
    def __init__(
        self,
        jobs: Iterable[EvaluationJob],
        *,
        cancellation_failures: int = 0,
    ) -> None:
        self.jobs = {job.job_id: job for job in jobs}
        self.events: list[tuple[str, str]] = []
        self.recovery_calls: list[str] = []
        self.units: dict[str, _RecoveryUnit] = {}
        self.attempt_states: dict[str, AttemptState] = {}
        self.cancellation_failures = cancellation_failures
        self.job_failed = asyncio.Event()

    async def get_job(self, job_id: str) -> EvaluationJob | None:
        return self.jobs.get(job_id)

    async def list_recoverable_jobs(self) -> tuple[EvaluationJob, ...]:
        return tuple(job for job in self.jobs.values() if job.state not in _TERMINAL_STATES)

    async def request_cancellation(self, job_id: str) -> EvaluationJob:
        job = self.jobs[job_id]
        self.events.append(("request_cancel", job_id))
        if self.cancellation_failures:
            self.cancellation_failures -= 1
            raise RuntimeError("database-secret")
        if job.state not in _TERMINAL_STATES:
            self.jobs[job_id] = replace(
                job,
                state=JobState.CANCELLATION_REQUESTED,
                cancel_requested_at=job.updated_at,
            )
        return self.jobs[job_id]

    async def finish_cancelled(self, job_id: str) -> None:
        job = self.jobs[job_id]
        self.events.append(("terminal_cancel", job_id))
        assert job.state is JobState.CANCELLATION_REQUESTED
        self.jobs[job_id] = replace(job, state=JobState.CANCELLED)

    async def recover_orphaned_attempts(self, job_id: str) -> EvaluationRecovery:
        self.events.append(("recover", job_id))
        self.recovery_calls.append(job_id)
        requeued: list[str] = []
        unknown_attempts: list[str] = []
        blocked: list[str] = []
        for unit_id, unit in self.units.items():
            if unit.state is not UnitState.RUNNING:
                continue
            if not unit.attempt_ids:
                unit.state = UnitState.READY
                requeued.append(unit_id)
                continue
            blocked.append(unit_id)
            for attempt_id in unit.attempt_ids:
                if self.attempt_states[attempt_id] is AttemptState.RUNNING:
                    self.attempt_states[attempt_id] = AttemptState.UNKNOWN
                    unknown_attempts.append(attempt_id)
        return EvaluationRecovery(
            requeued_unit_ids=tuple(requeued),
            unknown_attempt_ids=tuple(unknown_attempts),
            blocked_unit_ids=tuple(blocked),
        )

    async def transition_job(self, job_id: str, state: JobState) -> EvaluationJob:
        job = self.jobs[job_id]
        self.events.append((f"transition_{state.value}", job_id))
        self.jobs[job_id] = replace(job, state=state)
        if state is JobState.FAILED:
            self.job_failed.set()
        return self.jobs[job_id]


class BlockingRunner:
    def __init__(self, store: FakeStore) -> None:
        self._store = store
        self.calls: list[str] = []
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.cleanup_allowed = asyncio.Event()
        self.leases_released: set[str] = set()
        self._release = asyncio.Event()

    def finish_normally(self) -> None:
        self._release.set()

    async def run(self, job_id: str) -> None:
        self.calls.append(job_id)
        self._store.events.append(("run", job_id))
        self.started.set()
        try:
            if self._store.jobs[job_id].state is JobState.CANCELLATION_REQUESTED:
                await self._store.finish_cancelled(job_id)
                return
            await self._release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            await self.cleanup_allowed.wait()
            await self._store.finish_cancelled(job_id)
        finally:
            self.leases_released.add(job_id)


class FailingCleanupRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.released = asyncio.Event()

    async def run(self, job_id: str) -> None:
        del job_id
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise RuntimeError("provider-secret") from None
        finally:
            self.released.set()


class BackgroundFailingRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def run(self, job_id: str) -> None:
        del job_id
        self.started.set()
        raise RuntimeError("provider-secret")


class ReplacementRaceRunner:
    def __init__(self, store: FakeStore) -> None:
        self._store = store
        self.calls = 0
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()
        self.first_returning = asyncio.Event()
        self.second_started = asyncio.Event()
        self.second_cancelled = asyncio.Event()

    async def run(self, job_id: str) -> None:
        self.calls += 1
        call = self.calls
        if call == 1:
            self.first_started.set()
            await self.release_first.wait()
            self.first_returning.set()
            return
        if call == 2:
            self.second_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if call == 2:
                self.second_cancelled.set()
            await self._store.finish_cancelled(job_id)


@pytest.mark.asyncio
async def test_start_owns_one_process_local_task_per_executable_job() -> None:
    store = FakeStore((_job("job-1"), _job("finished", JobState.SUCCEEDED)))
    runner = BlockingRunner(store)
    supervisor = EvaluationSupervisor(repository=store, runner=runner)

    await supervisor.start("job-1")
    await runner.started.wait()
    await supervisor.start("job-1")
    await supervisor.start("finished")

    assert runner.calls == ["job-1"]

    close_task = asyncio.create_task(supervisor.close())
    await runner.cancelled.wait()
    assert store.jobs["job-1"].state is JobState.CANCELLATION_REQUESTED
    assert not close_task.done()
    runner.cleanup_allowed.set()
    await close_task


@pytest.mark.asyncio
async def test_cancel_persists_before_stopping_claims_and_waits_for_cleanup() -> None:
    store = FakeStore((_job("job-1"),))
    runner = BlockingRunner(store)
    supervisor = EvaluationSupervisor(repository=store, runner=runner)
    await supervisor.start("job-1")
    await runner.started.wait()

    cancelling = asyncio.create_task(supervisor.cancel("job-1"))
    await runner.cancelled.wait()

    assert store.events[:2] == [("run", "job-1"), ("request_cancel", "job-1")]
    assert store.jobs["job-1"].state is JobState.CANCELLATION_REQUESTED
    assert not cancelling.done()

    runner.cleanup_allowed.set()
    await cancelling

    assert store.events[-1] == ("terminal_cancel", "job-1")
    assert runner.leases_released == {"job-1"}
    assert supervisor.failures == ()


@pytest.mark.asyncio
async def test_concurrent_start_cancel_and_close_keep_one_task_and_finish_cleanup() -> None:
    store = FakeStore((_job("job-1"),))
    runner = BlockingRunner(store)
    supervisor = EvaluationSupervisor(repository=store, runner=runner)

    await asyncio.gather(supervisor.start("job-1"), supervisor.start("job-1"))
    await runner.started.wait()

    cancelling = asyncio.create_task(supervisor.cancel("job-1"))
    closing = asyncio.create_task(supervisor.close())
    await runner.cancelled.wait()
    runner.cleanup_allowed.set()
    await asyncio.gather(cancelling, closing)

    assert runner.calls == ["job-1"]
    assert store.jobs["job-1"].state is JobState.CANCELLED
    assert runner.leases_released == {"job-1"}


@pytest.mark.asyncio
async def test_close_interrupts_active_runner_after_external_cancellation_request() -> None:
    store = FakeStore((_job("job-1"),))
    runner = BlockingRunner(store)
    supervisor = EvaluationSupervisor(repository=store, runner=runner)
    await supervisor.start("job-1")
    await runner.started.wait()
    await store.request_cancellation("job-1")

    closing = asyncio.create_task(supervisor.close())
    await runner.cancelled.wait()
    runner.cleanup_allowed.set()
    await closing

    assert store.jobs["job-1"].state is JobState.CANCELLED
    assert runner.leases_released == {"job-1"}


@pytest.mark.asyncio
async def test_recover_classifies_running_and_collecting_before_starting_them() -> None:
    store = FakeStore(
        (
            _job("queued"),
            _job("running", JobState.RUNNING),
            _job("collecting", JobState.COLLECTING),
            _job("cancelling", JobState.CANCELLATION_REQUESTED),
        )
    )
    store.units = {
        "no-attempt": _RecoveryUnit(UnitState.RUNNING, ()),
        "running-attempt": _RecoveryUnit(UnitState.RUNNING, ("attempt-running",)),
        "unknown-attempt": _RecoveryUnit(UnitState.RUNNING, ("attempt-unknown",)),
        "terminal-attempt": _RecoveryUnit(UnitState.RUNNING, ("attempt-terminal",)),
    }
    store.attempt_states = {
        "attempt-running": AttemptState.RUNNING,
        "attempt-unknown": AttemptState.UNKNOWN,
        "attempt-terminal": AttemptState.SUCCEEDED,
    }
    runner = BlockingRunner(store)
    supervisor = EvaluationSupervisor(repository=store, runner=runner)

    await supervisor.recover()
    await runner.started.wait()

    assert store.recovery_calls == ["running", "collecting"]
    assert store.units["no-attempt"].state is UnitState.READY
    assert store.attempt_states["attempt-running"] is AttemptState.UNKNOWN
    assert store.units["running-attempt"].state is UnitState.RUNNING
    assert store.units["unknown-attempt"].state is UnitState.RUNNING
    assert store.units["terminal-attempt"].state is UnitState.RUNNING
    assert set(runner.calls) == {"queued", "running", "collecting", "cancelling"}
    for job_id in ("running", "collecting"):
        assert store.events.index(("recover", job_id)) < store.events.index(("run", job_id))

    closing = asyncio.create_task(supervisor.close())
    await runner.cancelled.wait()
    runner.cleanup_allowed.set()
    await closing
    assert runner.leases_released == {"queued", "running", "collecting", "cancelling"}


@pytest.mark.asyncio
async def test_close_surfaces_safe_cleanup_errors_after_waiting_for_release() -> None:
    store = FakeStore((_job("job-1"),))
    runner = FailingCleanupRunner()
    supervisor = EvaluationSupervisor(repository=store, runner=runner)
    await supervisor.start("job-1")
    await runner.started.wait()

    with pytest.raises(EvaluationSupervisorError) as error:
        await supervisor.close()

    assert "provider-secret" not in str(error.value)
    assert runner.released.is_set()


@pytest.mark.asyncio
async def test_close_can_retry_when_durable_cancellation_requests_fail() -> None:
    store = FakeStore((_job("job-1"), _job("job-2")), cancellation_failures=2)
    runner = BlockingRunner(store)
    supervisor = EvaluationSupervisor(repository=store, runner=runner)
    await asyncio.gather(supervisor.start("job-1"), supervisor.start("job-2"))
    await runner.started.wait()

    with pytest.raises(EvaluationSupervisorError) as error:
        await supervisor.close()

    assert "database-secret" not in str(error.value)
    assert {(failure.job_id, failure.code) for failure in error.value.failures} == {
        ("job-1", "CANCELLATION_REQUEST_FAILED"),
        ("job-2", "CANCELLATION_REQUEST_FAILED"),
    }
    assert not runner.cancelled.is_set()
    assert store.jobs["job-1"].state is JobState.QUEUED
    assert store.jobs["job-2"].state is JobState.QUEUED

    runner.cleanup_allowed.set()
    await supervisor.close()

    assert runner.cancelled.is_set()
    assert store.jobs["job-1"].state is JobState.CANCELLED
    assert store.jobs["job-2"].state is JobState.CANCELLED
    assert runner.leases_released == {"job-1", "job-2"}


@pytest.mark.asyncio
async def test_background_runner_failure_is_safe_durable_and_drainable() -> None:
    store = FakeStore((_job("job-1"),))
    runner = BackgroundFailingRunner()
    supervisor = EvaluationSupervisor(repository=store, runner=runner)
    loop = asyncio.get_running_loop()
    unhandled: list[dict[str, object]] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    try:
        await supervisor.start("job-1")
        await runner.started.wait()
        await store.job_failed.wait()
        checkpoint: asyncio.Future[None] = loop.create_future()
        loop.call_soon(checkpoint.set_result, None)
        await checkpoint

        assert store.jobs["job-1"].state is JobState.FAILED
        assert [(failure.job_id, failure.code) for failure in supervisor.failures] == [
            ("job-1", "RUNNER_FAILED")
        ]
        assert "provider-secret" not in repr(supervisor.failures)

        with pytest.raises(EvaluationSupervisorError) as error:
            await supervisor.close()
        assert "provider-secret" not in str(error.value)
        assert supervisor.drain_failures() == error.value.failures

        await supervisor.close()
        assert unhandled == []
    finally:
        loop.set_exception_handler(previous_handler)


@pytest.mark.asyncio
async def test_old_done_callback_cannot_remove_replacement_task() -> None:
    store = FakeStore((_job("job-1"),))
    runner = ReplacementRaceRunner(store)
    supervisor = EvaluationSupervisor(repository=store, runner=runner)

    await supervisor.start("job-1")
    await runner.first_started.wait()
    runner.release_first.set()
    await runner.first_returning.wait()

    # The waiter is scheduled before task A's done callback, so B becomes owned first.
    await supervisor.start("job-1")
    await runner.second_started.wait()
    await supervisor.start("job-1")
    checkpoint: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    asyncio.get_running_loop().call_soon(checkpoint.set_result, None)
    await checkpoint

    assert runner.calls == 2

    await supervisor.close()
    assert runner.second_cancelled.is_set()
    assert store.jobs["job-1"].state is JobState.CANCELLED
