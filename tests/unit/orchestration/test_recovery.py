"""Stuck-run recovery sweep (V1-H T5.4)."""

import asyncio
from datetime import UTC, datetime, timedelta

from cowork_agent.domain import DigestRun, RunStatus, RunTrigger
from cowork_agent.orchestration.recovery import sweep_stuck_runs
from cowork_agent.persistence.repositories.local import InMemoryRunRepository

NOW = datetime(2026, 8, 8, 12, tzinfo=UTC)
OLD = NOW - timedelta(hours=1)


def _run(run_id: str, *, key: str) -> DigestRun:
    return DigestRun(
        id=run_id,
        user_id="u1",
        mailbox_connection_id="mbx1",
        trigger=RunTrigger.ON_DEMAND,
        status=RunStatus.QUEUED,
        query="is:unread in:inbox",
        idempotency_key=key,
        max_emails=50,
    )


def test_sweep_resets_stuck_running_and_reenqueues_both_orphans() -> None:
    async def scenario() -> None:
        runs = InMemoryRunRepository()
        crashed, orphan, healthy, fresh = (
            _run("run_crashed", key="k1"),
            _run("run_orphan", key="k2"),
            _run("run_healthy", key="k3"),
            _run("run_fresh", key="k4"),
        )
        for run in (crashed, orphan, healthy, fresh):
            await runs.create(run)
        crashed.status, crashed.started_at = RunStatus.RUNNING, OLD
        orphan.created_at = OLD
        healthy.status, healthy.started_at = RunStatus.RUNNING, NOW
        fresh.created_at = NOW
        reenqueued: list[str] = []

        async def requeue(run: DigestRun) -> None:
            reenqueued.append(run.id)

        recovered = await sweep_stuck_runs(
            runs,
            now=NOW,
            requeue=requeue,
            running_timeout=timedelta(minutes=15),
            queued_timeout=timedelta(minutes=15),
        )

        assert recovered == 2
        # Crashed RUNNING run returns to the claimable pool in the same
        # pass it is re-enqueued.
        assert crashed.status is RunStatus.QUEUED
        assert crashed.started_at is None
        assert reenqueued == ["run_crashed", "run_orphan"]
        # Recent runs are untouched.
        assert healthy.status is RunStatus.RUNNING
        assert fresh.status is RunStatus.QUEUED

    asyncio.run(scenario())


def test_sweep_reset_is_compare_and_set() -> None:
    async def scenario() -> None:
        runs = InMemoryRunRepository()
        crashed = _run("run_crashed", key="k1")
        await runs.create(crashed)
        crashed.status, crashed.started_at = RunStatus.RUNNING, OLD

        first = await sweep_stuck_runs(runs, now=NOW)
        # A second pass sees the run back in QUEUED with no created_at age
        # signal and no requeue callback: nothing left to recover.
        second = await sweep_stuck_runs(runs, now=NOW)
        assert first == 1
        assert second == 0
        assert crashed.status is RunStatus.QUEUED

    asyncio.run(scenario())


def test_sweep_without_requeue_only_resets_running() -> None:
    async def scenario() -> None:
        runs = InMemoryRunRepository()
        orphan = _run("run_orphan", key="k1")
        await runs.create(orphan)
        orphan.created_at = OLD

        recovered = await sweep_stuck_runs(runs, now=NOW)

        # Without a queue to re-enqueue into, orphaned QUEUED runs wait.
        assert recovered == 0
        assert orphan.status is RunStatus.QUEUED

    asyncio.run(scenario())
