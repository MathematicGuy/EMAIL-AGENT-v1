from __future__ import annotations

import asyncio

import pytest

from cowork_agent.features.batch_evaluation.contracts import UnitState, WorkUnit, WorkUnitOutcome
from cowork_agent.features.batch_evaluation.queue import DurableWorkUnitQueue


def unit(unit_id: str, ordinal: int) -> WorkUnit:
    return WorkUnit(unit_id=unit_id, ordinal=ordinal, payload={"item_id": f"item-{ordinal}"})


class FakeUnitStore:
    def __init__(self, ready: tuple[WorkUnit, ...], claim_failures: int = 0) -> None:
        self._ready = ready
        self._claimed: set[str] = set()
        self._completed: set[str] = set()
        self._claim_failures = claim_failures
        self.claim_calls: list[tuple[str, str]] = []
        self.complete_calls: list[tuple[str, WorkUnitOutcome]] = []

    async def claim_ready_unit(self, job_id: str, worker_id: str) -> WorkUnit | None:
        self.claim_calls.append((job_id, worker_id))
        if self._claim_failures:
            self._claim_failures -= 1
            raise RuntimeError("durable claim failed")
        for ready_unit in self._ready:
            if (
                ready_unit.unit_id not in self._claimed
                and ready_unit.unit_id not in self._completed
            ):
                self._claimed.add(ready_unit.unit_id)
                return ready_unit
        return None

    async def complete_unit(self, job_id: str, outcome: WorkUnitOutcome) -> None:
        self.complete_calls.append((job_id, outcome))
        self._completed.add(outcome.unit_id)

    async def complete(self, job_id: str, unit_id: str) -> None:
        await self.complete_unit(
            job_id,
            WorkUnitOutcome(
                unit_id=unit_id,
                ordinal=0,
                state=UnitState.SUCCEEDED,
                provider_requests=1,
                total_tokens=1,
                private_result=None,
            ),
        )


def successful_outcome(unit_id: str, ordinal: int) -> WorkUnitOutcome:
    return WorkUnitOutcome(
        unit_id=unit_id,
        ordinal=ordinal,
        state=UnitState.SUCCEEDED,
        provider_requests=1,
        total_tokens=1,
        private_result=None,
    )


@pytest.mark.asyncio
async def test_queue_claims_from_store_and_never_replays_completed_unit() -> None:
    store = FakeUnitStore(ready=(unit("a", 0), unit("b", 1)))
    queue = DurableWorkUnitQueue(store, capacity=1)

    first = await queue.claim_next("job-1", "lane-1")
    assert first is not None
    assert first.unit_id == "a"

    await queue.complete("job-1", successful_outcome("a", 0))

    second = await queue.claim_next("job-1", "lane-1")
    assert second is not None
    assert second.unit_id == "b"
    assert store.claim_calls == [("job-1", "lane-1"), ("job-1", "lane-1")]
    assert [outcome.unit_id for _, outcome in store.complete_calls] == ["a"]


@pytest.mark.asyncio
async def test_queue_blocks_second_claim_until_first_unit_is_completed() -> None:
    store = FakeUnitStore(ready=(unit("a", 0), unit("b", 1)))
    queue = DurableWorkUnitQueue(store, capacity=1)

    first = await queue.claim_next("job-1", "lane-1")
    assert first is not None

    pending_claim = asyncio.create_task(queue.claim_next("job-1", "lane-2"))
    await asyncio.sleep(0)

    assert not pending_claim.done()
    assert store.claim_calls == [("job-1", "lane-1")]

    await queue.complete("job-1", successful_outcome("a", 0))

    second = await pending_claim
    assert second is not None
    assert second.unit_id == "b"
    assert store.claim_calls == [("job-1", "lane-1"), ("job-1", "lane-2")]


@pytest.mark.asyncio
async def test_queue_releases_capacity_when_durable_claim_finds_no_work() -> None:
    store = FakeUnitStore(ready=())
    queue = DurableWorkUnitQueue(store, capacity=1)

    assert await queue.claim_next("job-1", "lane-1") is None
    assert await queue.claim_next("job-1", "lane-2") is None

    assert store.claim_calls == [("job-1", "lane-1"), ("job-1", "lane-2")]


@pytest.mark.asyncio
async def test_queue_releases_capacity_when_durable_claim_raises() -> None:
    store = FakeUnitStore(ready=(unit("a", 0),), claim_failures=1)
    queue = DurableWorkUnitQueue(store, capacity=1)

    with pytest.raises(RuntimeError, match="durable claim failed"):
        await queue.claim_next("job-1", "lane-1")

    claimed = await queue.claim_next("job-1", "lane-2")

    assert claimed is not None
    assert claimed.unit_id == "a"
    assert store.claim_calls == [("job-1", "lane-1"), ("job-1", "lane-2")]


@pytest.mark.asyncio
async def test_cancelled_waiting_claim_does_not_consume_capacity() -> None:
    store = FakeUnitStore(ready=(unit("a", 0), unit("b", 1)))
    queue = DurableWorkUnitQueue(store, capacity=1)

    first = await queue.claim_next("job-1", "lane-1")
    assert first is not None

    cancelled_claim = asyncio.create_task(queue.claim_next("job-1", "lane-2"))
    await asyncio.sleep(0)
    cancelled_claim.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_claim

    await queue.complete("job-1", successful_outcome("a", 0))
    second = await queue.claim_next("job-1", "lane-3")

    assert second is not None
    assert second.unit_id == "b"
    assert store.claim_calls == [("job-1", "lane-1"), ("job-1", "lane-3")]
