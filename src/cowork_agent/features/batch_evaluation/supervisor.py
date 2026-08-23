"""Process-local ownership and restart recovery for durable evaluation jobs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from cowork_agent.features.batch_evaluation.contracts import JobState
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
_RECOVERABLE_STATES = frozenset(
    {
        JobState.QUEUED,
        JobState.RUNNING,
        JobState.COLLECTING,
        JobState.CANCELLATION_REQUESTED,
    }
)
_ORPHAN_CLASSIFICATION_STATES = frozenset({JobState.RUNNING, JobState.COLLECTING})


@dataclass(frozen=True, slots=True)
class SupervisorFailure:
    """A safe process-local failure signal suitable for status surfaces."""

    job_id: str
    code: str


class EvaluationSupervisorError(RuntimeError):
    """A supervisor operation failed without exposing private exception details."""

    def __init__(
        self,
        message: str = "evaluation supervisor operation failed",
        *,
        failures: tuple[SupervisorFailure, ...] = (),
    ) -> None:
        super().__init__(message)
        self.failures = failures


class _ManagedRunnerFailure(RuntimeError):
    """Internal safe marker whose originating exception has already been recorded."""


class SupervisorRepository(Protocol):
    """Durable control-plane operations needed before a runner may execute."""

    async def get_job(self, job_id: str) -> EvaluationJob | None: ...

    async def list_recoverable_jobs(self) -> tuple[EvaluationJob, ...]: ...

    async def request_cancellation(self, job_id: str) -> EvaluationJob: ...

    async def recover_orphaned_attempts(self, job_id: str) -> EvaluationRecovery: ...

    async def transition_job(self, job_id: str, state: JobState) -> EvaluationJob: ...


class EvaluationRunner(Protocol):
    """The runner owns leases, private artifacts, and terminal durable writes."""

    async def run(self, job_id: str) -> None: ...


class EvaluationSupervisor:
    """Own one local runner task per durable job without making tasks authoritative."""

    def __init__(self, *, repository: SupervisorRepository, runner: EvaluationRunner) -> None:
        self._repository = repository
        self._runner = runner
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancellation_tasks: set[asyncio.Task[None]] = set()
        self._failures: dict[str, SupervisorFailure] = {}
        self._lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._closing = False
        self._closed = False

    @property
    def failures(self) -> tuple[SupervisorFailure, ...]:
        """Return current safe background failures in deterministic job order."""

        return tuple(self._failures[job_id] for job_id in sorted(self._failures))

    def drain_failures(self) -> tuple[SupervisorFailure, ...]:
        """Return and clear safe background failures so close can be retried."""

        failures = self.failures
        self._failures.clear()
        return failures

    async def start(self, job_id: str) -> None:
        """Start one runner only when the durable job is executable or recoverable."""

        await self._start_task(job_id, allow_during_close=False)

    async def cancel(self, job_id: str) -> None:
        """Persist cancellation, stop the local runner, then await its cleanup."""

        async with self._lock:
            job = await self._require_job(job_id)
            if job.state in _TERMINAL_STATES:
                return
            try:
                await self._repository.request_cancellation(job_id)
            except asyncio.CancelledError:
                raise
            except BaseException:
                failure = SupervisorFailure(job_id, "CANCELLATION_REQUEST_FAILED")
                raise EvaluationSupervisorError(failures=(failure,)) from None
            task = self._tasks.get(job_id)
            active_task = task is not None and not task.done()
            if active_task and task not in self._cancellation_tasks:
                assert task is not None
                self._cancellation_tasks.add(task)
                task.cancel()
            else:
                task = await self._start_task_locked(job_id, allow_during_close=True)

        if task is not None:
            await self._await_cleanup(job_id, task)
        await self._finish_cancellation(job_id)

    async def recover(self) -> None:
        """Classify unsafe in-flight attempts before resuming each durable job."""

        jobs = await self._repository.list_recoverable_jobs()
        for job in jobs:
            if job.state in _ORPHAN_CLASSIFICATION_STATES:
                await self._repository.recover_orphaned_attempts(job.job_id)
        for job in jobs:
            await self.start(job.job_id)

    async def close(self) -> None:
        """Cancel all active runners and surface any safe cleanup failure."""

        async with self._close_lock:
            async with self._lock:
                if self._closed:
                    return
                self._closing = True
                job_ids = tuple(self._tasks)

            completed = await asyncio.gather(
                *(self.cancel(job_id) for job_id in job_ids),
                return_exceptions=True,
            )
            failures = list(self.failures)
            for job_id, result in zip(job_ids, completed, strict=True):
                if isinstance(result, EvaluationSupervisorError):
                    failures.extend(result.failures)
                elif isinstance(result, BaseException):
                    failures.append(SupervisorFailure(job_id, "CANCELLATION_FAILED"))
            unique_failures = tuple(
                {(failure.job_id, failure.code): failure for failure in failures}.values()
            )
            async with self._lock:
                if unique_failures:
                    self._closing = False
                else:
                    self._closed = True
            if unique_failures:
                raise EvaluationSupervisorError(failures=unique_failures) from None

    async def _start_task(
        self,
        job_id: str,
        *,
        allow_during_close: bool,
    ) -> asyncio.Task[None] | None:
        async with self._lock:
            return await self._start_task_locked(job_id, allow_during_close=allow_during_close)

    async def _start_task_locked(
        self,
        job_id: str,
        *,
        allow_during_close: bool,
    ) -> asyncio.Task[None] | None:
        if self._closing and not allow_during_close:
            return None
        current = self._tasks.get(job_id)
        if current is not None and not current.done():
            return current
        job = await self._require_job(job_id)
        if job.state not in _RECOVERABLE_STATES:
            return None
        task = asyncio.create_task(self._run_managed(job_id), name=f"evaluation-job:{job_id}")
        self._tasks[job_id] = task
        if job.state is JobState.CANCELLATION_REQUESTED:
            self._cancellation_tasks.add(task)
        task.add_done_callback(lambda finished: self._discard_task(job_id, finished))
        return task

    async def _finish_cancellation(self, job_id: str) -> None:
        job = await self._require_job(job_id)
        if job.state is not JobState.CANCELLATION_REQUESTED:
            return
        task = await self._start_task(job_id, allow_during_close=True)
        if task is None:
            job = await self._require_job(job_id)
            if job.state is JobState.CANCELLATION_REQUESTED:
                raise EvaluationSupervisorError("evaluation job cancellation did not complete")
            return
        await self._await_cleanup(job_id, task)
        if (await self._require_job(job_id)).state is JobState.CANCELLATION_REQUESTED:
            raise EvaluationSupervisorError("evaluation job cancellation did not complete")

    async def _run_managed(self, job_id: str) -> None:
        try:
            await self._runner.run(job_id)
        except asyncio.CancelledError:
            raise
        except BaseException:
            failure = SupervisorFailure(job_id, "RUNNER_FAILED")
            self._failures[job_id] = failure
            try:
                job = await self._repository.get_job(job_id)
                if job is not None and job.state in {
                    JobState.QUEUED,
                    JobState.RUNNING,
                    JobState.COLLECTING,
                }:
                    await self._repository.transition_job(job_id, JobState.FAILED)
            except asyncio.CancelledError:
                raise
            except BaseException:
                pass
            raise _ManagedRunnerFailure("evaluation runner failed") from None

    async def _await_cleanup(self, job_id: str, task: asyncio.Task[None]) -> None:
        try:
            await task
        except asyncio.CancelledError:
            return
        except BaseException:
            failure = self._failures.get(
                job_id, SupervisorFailure(job_id, "RUNNER_CLEANUP_FAILED")
            )
            raise EvaluationSupervisorError(failures=(failure,)) from None

    async def _require_job(self, job_id: str) -> EvaluationJob:
        job = await self._repository.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def _discard_task(self, job_id: str, task: asyncio.Task[None]) -> None:
        self._cancellation_tasks.discard(task)
        try:
            error = task.exception()
        except asyncio.CancelledError:
            error = None
        if error is not None and not isinstance(error, _ManagedRunnerFailure):
            self._failures.setdefault(
                job_id, SupervisorFailure(job_id, "SUPERVISOR_TASK_FAILED")
            )
        if self._tasks.get(job_id) is task:
            self._tasks.pop(job_id, None)
