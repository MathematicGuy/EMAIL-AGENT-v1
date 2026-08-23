"""Process-local ownership and restart recovery for durable evaluation jobs."""

from __future__ import annotations

import asyncio
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


class EvaluationSupervisorError(RuntimeError):
    """A runner cleanup failed without exposing provider or evaluator details."""


class SupervisorRepository(Protocol):
    """Durable control-plane operations needed before a runner may execute."""

    async def get_job(self, job_id: str) -> EvaluationJob | None: ...

    async def list_recoverable_jobs(self) -> tuple[EvaluationJob, ...]: ...

    async def request_cancellation(self, job_id: str) -> EvaluationJob: ...

    async def recover_orphaned_attempts(self, job_id: str) -> EvaluationRecovery: ...


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
        self._lock = asyncio.Lock()
        self._closing = False
        self._closed = False

    async def start(self, job_id: str) -> None:
        """Start one runner only when the durable job is executable or recoverable."""

        await self._start_task(job_id, allow_during_close=False)

    async def cancel(self, job_id: str) -> None:
        """Persist cancellation, stop the local runner, then await its cleanup."""

        async with self._lock:
            job = await self._require_job(job_id)
            if job.state in _TERMINAL_STATES:
                return
            await self._repository.request_cancellation(job_id)
            task = self._tasks.get(job_id)
            active_task = task is not None and not task.done()
            if active_task and task not in self._cancellation_tasks:
                assert task is not None
                self._cancellation_tasks.add(task)
                task.cancel()
            else:
                task = await self._start_task_locked(job_id, allow_during_close=True)

        if task is not None:
            await self._await_cleanup(task)
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

        async with self._lock:
            if self._closed:
                return
            self._closing = True
            job_ids = tuple(self._tasks)

        completed = await asyncio.gather(
            *(self.cancel(job_id) for job_id in job_ids),
            return_exceptions=True,
        )
        async with self._lock:
            self._closed = True
        if any(isinstance(result, BaseException) for result in completed):
            raise EvaluationSupervisorError("evaluation runner cleanup failed") from None

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
        task = asyncio.create_task(self._runner.run(job_id), name=f"evaluation-job:{job_id}")
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
        await self._await_cleanup(task)
        if (await self._require_job(job_id)).state is JobState.CANCELLATION_REQUESTED:
            raise EvaluationSupervisorError("evaluation job cancellation did not complete")

    async def _await_cleanup(self, task: asyncio.Task[None]) -> None:
        try:
            await task
        except asyncio.CancelledError:
            return
        except BaseException:
            raise EvaluationSupervisorError("evaluation runner cleanup failed") from None

    async def _require_job(self, job_id: str) -> EvaluationJob:
        job = await self._repository.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def _discard_task(self, job_id: str, task: asyncio.Task[None]) -> None:
        self._cancellation_tasks.discard(task)
        try:
            task.exception()
        except BaseException:
            pass
        if self._tasks.get(job_id) is task:
            self._tasks.pop(job_id, None)
