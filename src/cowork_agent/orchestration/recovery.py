"""Stuck-run recovery for the durable control plane (V1-H T5.4).

A hard worker crash leaves its run RUNNING; a crash between run creation
and enqueue orphans it in QUEUED with no queue message. The sweep returns
both to a recoverable state:

- RUNNING past ``running_timeout`` is reset to QUEUED via a
  compare-and-set (``reset_stuck_run``), so concurrent sweepers can never
  clobber a run another worker just re-claimed;
- reset and orphaned-QUEUED runs are re-enqueued when a ``requeue``
  callback is supplied (enqueues are idempotent and the CAS claim keeps
  execution single).

At-least-once caveat: a worker that exceeds ``running_timeout`` may be
superseded — the original and the replacement can overlap briefly, so
executors must stay idempotent at the task level (the idempotent task key
provides that).
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from cowork_agent.domain import DigestRun, RunStatus
from cowork_agent.features.email_action_plan.ports import RunRepository

logger = logging.getLogger(__name__)

DEFAULT_RUNNING_TIMEOUT = timedelta(minutes=15)
DEFAULT_QUEUED_TIMEOUT = timedelta(minutes=15)

Requeue = Callable[[DigestRun], Awaitable[None]]


async def sweep_stuck_runs(
    runs: RunRepository,
    *,
    now: datetime,
    requeue: Requeue | None = None,
    running_timeout: timedelta = DEFAULT_RUNNING_TIMEOUT,
    queued_timeout: timedelta = DEFAULT_QUEUED_TIMEOUT,
) -> int:
    """Recover stuck runs; returns the number of runs recovered."""
    stuck = await runs.list_stuck_runs(
        running_before=now - running_timeout, queued_before=now - queued_timeout
    )
    recovered = 0
    for run in stuck:
        if run.status is RunStatus.RUNNING:
            if not await runs.reset_stuck_run(run.id, started_before=now - running_timeout):
                continue  # another sweeper or worker got there first
            logger.warning("Recovered stuck running run %s back to queued", run.id)
            recovered += 1
            if requeue is not None:
                await requeue(run)
        elif requeue is not None:
            await requeue(run)
            logger.warning("Re-enqueued orphaned queued run %s", run.id)
            recovered += 1
    return recovered
