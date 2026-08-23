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


class ControlledUnitStore:
    def __init__(
        self,
        claim_results: tuple[WorkUnit | None, ...],
        *,
        completion_failures: tuple[bool, ...] = (),
    ) -> None:
        self._claim_results = claim_results
        self._completion_failures = completion_failures
        self.claim_started = tuple(asyncio.Event() for _ in claim_results)
        self.claim_may_commit = tuple(asyncio.Event() for _ in claim_results)
        self.claim_committed = tuple(asyncio.Event() for _ in claim_results)
        self.claim_may_return = tuple(asyncio.Event() for _ in claim_results)
        self.claim_returned = tuple(asyncio.Event() for _ in claim_results)
        self.completion_started = tuple(asyncio.Event() for _ in completion_failures)
        self.completion_may_commit = tuple(asyncio.Event() for _ in completion_failures)
        self.completion_committed = tuple(asyncio.Event() for _ in completion_failures)
        self.completion_may_return = tuple(asyncio.Event() for _ in completion_failures)
        self.completion_returned = tuple(asyncio.Event() for _ in completion_failures)
        self.completion_failed = tuple(asyncio.Event() for _ in completion_failures)
        self.claim_calls: list[tuple[str, str]] = []
        self.complete_calls: list[tuple[str, WorkUnitOutcome]] = []
        self.durable_outstanding: set[tuple[str, str]] = set()
        self.max_durable_outstanding = 0

    async def claim_ready_unit(self, job_id: str, worker_id: str) -> WorkUnit | None:
        call_index = len(self.claim_calls)
        self.claim_calls.append((job_id, worker_id))
        self.claim_started[call_index].set()
        await self.claim_may_commit[call_index].wait()

        result = self._claim_results[call_index]
        if result is not None:
            self.durable_outstanding.add((job_id, result.unit_id))
            self.max_durable_outstanding = max(
                self.max_durable_outstanding, len(self.durable_outstanding)
            )
        self.claim_committed[call_index].set()

        await self.claim_may_return[call_index].wait()
        self.claim_returned[call_index].set()
        return result

    async def complete_unit(self, job_id: str, outcome: WorkUnitOutcome) -> None:
        call_index = len(self.complete_calls)
        self.complete_calls.append((job_id, outcome))
        self.completion_started[call_index].set()
        await self.completion_may_commit[call_index].wait()

        if self._completion_failures[call_index]:
            self.completion_failed[call_index].set()
            raise RuntimeError("durable completion failed")

        self.durable_outstanding.remove((job_id, outcome.unit_id))
        self.completion_committed[call_index].set()
        await self.completion_may_return[call_index].wait()
        self.completion_returned[call_index].set()


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
    store = ControlledUnitStore(
        claim_results=(unit("a", 0), unit("b", 1)),
        completion_failures=(False,),
    )
    queue = DurableWorkUnitQueue(store, capacity=1)

    first_claim = asyncio.create_task(queue.claim_next("job-1", "lane-1"))
    await store.claim_started[0].wait()

    pending_claim = asyncio.create_task(queue.claim_next("job-1", "lane-2"))
    store.claim_may_commit[0].set()
    store.claim_may_return[0].set()
    assert await first_claim == unit("a", 0)

    assert not store.claim_started[1].is_set()

    store.completion_may_commit[0].set()
    store.completion_may_return[0].set()
    await queue.complete("job-1", successful_outcome("a", 0))

    await store.claim_started[1].wait()
    store.claim_may_commit[1].set()
    store.claim_may_return[1].set()
    assert await pending_claim == unit("b", 1)


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
    store = ControlledUnitStore(
        claim_results=(unit("a", 0), unit("b", 1)),
        completion_failures=(False,),
    )
    queue = DurableWorkUnitQueue(store, capacity=1)

    first_claim = asyncio.create_task(queue.claim_next("job-1", "lane-1"))
    await store.claim_started[0].wait()
    store.claim_may_commit[0].set()
    store.claim_may_return[0].set()
    assert await first_claim == unit("a", 0)

    cancelled_claim = asyncio.create_task(queue.claim_next("job-1", "lane-2"))
    cancelled_claim.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_claim

    store.completion_may_commit[0].set()
    store.completion_may_return[0].set()
    await queue.complete("job-1", successful_outcome("a", 0))
    store.claim_may_commit[1].set()
    store.claim_may_return[1].set()
    second = await queue.claim_next("job-1", "lane-3")

    assert second == unit("b", 1)
    assert store.claim_calls == [("job-1", "lane-1"), ("job-1", "lane-3")]


@pytest.mark.asyncio
async def test_wrong_unit_completion_does_not_admit_a_second_outstanding_unit() -> None:
    store = ControlledUnitStore(
        claim_results=(unit("a", 0), unit("b", 1)),
        completion_failures=(False,),
    )
    queue = DurableWorkUnitQueue(store, capacity=1)
    store.claim_may_commit[0].set()
    store.claim_may_return[0].set()
    assert await queue.claim_next("job-1", "lane-1") == unit("a", 0)
    store.completion_may_commit[0].set()
    store.completion_may_return[0].set()

    with pytest.raises(ValueError, match="not outstanding"):
        await queue.complete("job-1", successful_outcome("b", 1))
    assert store.complete_calls == []

    pending_claim = asyncio.create_task(queue.claim_next("job-1", "lane-2"))
    store.completion_may_commit[0].set()
    store.completion_may_return[0].set()
    completion = asyncio.create_task(queue.complete("job-1", successful_outcome("a", 0)))
    await store.completion_started[0].wait()

    assert not store.claim_started[1].is_set()
    assert store.max_durable_outstanding == 1

    await completion
    await store.claim_started[1].wait()
    store.claim_may_commit[1].set()
    store.claim_may_return[1].set()
    assert await pending_claim == unit("b", 1)
    assert store.max_durable_outstanding == 1


@pytest.mark.asyncio
async def test_wrong_job_completion_does_not_admit_a_second_outstanding_unit() -> None:
    store = ControlledUnitStore(
        claim_results=(unit("a", 0), unit("b", 1)),
        completion_failures=(False,),
    )
    queue = DurableWorkUnitQueue(store, capacity=1)
    store.claim_may_commit[0].set()
    store.claim_may_return[0].set()
    assert await queue.claim_next("job-1", "lane-1") == unit("a", 0)
    store.completion_may_commit[0].set()
    store.completion_may_return[0].set()

    with pytest.raises(ValueError, match="not outstanding"):
        await queue.complete("job-2", successful_outcome("a", 0))
    assert store.complete_calls == []

    pending_claim = asyncio.create_task(queue.claim_next("job-1", "lane-2"))
    store.completion_may_commit[0].set()
    store.completion_may_return[0].set()
    completion = asyncio.create_task(queue.complete("job-1", successful_outcome("a", 0)))
    await store.completion_started[0].wait()

    assert not store.claim_started[1].is_set()
    assert store.max_durable_outstanding == 1

    await completion
    await store.claim_started[1].wait()
    store.claim_may_commit[1].set()
    store.claim_may_return[1].set()
    assert await pending_claim == unit("b", 1)
    assert store.max_durable_outstanding == 1


@pytest.mark.asyncio
async def test_concurrent_and_double_completion_are_rejected() -> None:
    store = ControlledUnitStore(
        claim_results=(unit("a", 0),),
        completion_failures=(False, False),
    )
    queue = DurableWorkUnitQueue(store, capacity=1)
    store.claim_may_commit[0].set()
    store.claim_may_return[0].set()
    assert await queue.claim_next("job-1", "lane-1") == unit("a", 0)

    first_completion = asyncio.create_task(
        queue.complete("job-1", successful_outcome("a", 0))
    )
    await store.completion_started[0].wait()
    store.completion_may_commit[1].set()
    store.completion_may_return[1].set()

    with pytest.raises(ValueError, match="not outstanding"):
        await queue.complete("job-1", successful_outcome("a", 0))

    store.completion_may_commit[0].set()
    store.completion_may_return[0].set()
    await first_completion

    with pytest.raises(ValueError, match="not outstanding"):
        await queue.complete("job-1", successful_outcome("a", 0))
    assert len(store.complete_calls) == 1


@pytest.mark.asyncio
async def test_completion_failure_restores_ownership_and_keeps_capacity() -> None:
    store = ControlledUnitStore(
        claim_results=(unit("a", 0), unit("b", 1)),
        completion_failures=(True, False),
    )
    queue = DurableWorkUnitQueue(store, capacity=1)
    store.claim_may_commit[0].set()
    store.claim_may_return[0].set()
    assert await queue.claim_next("job-1", "lane-1") == unit("a", 0)

    store.completion_may_commit[0].set()
    with pytest.raises(RuntimeError, match="durable completion failed"):
        await queue.complete("job-1", successful_outcome("a", 0))

    pending_claim = asyncio.create_task(queue.claim_next("job-1", "lane-2"))
    retry = asyncio.create_task(queue.complete("job-1", successful_outcome("a", 0)))
    await store.completion_started[1].wait()
    assert not store.claim_started[1].is_set()

    store.completion_may_commit[1].set()
    store.completion_may_return[1].set()
    await retry
    await store.claim_started[1].wait()
    store.claim_may_commit[1].set()
    store.claim_may_return[1].set()
    assert await pending_claim == unit("b", 1)


@pytest.mark.asyncio
async def test_cancelled_claim_waits_for_none_outcome_before_releasing_capacity() -> None:
    store = ControlledUnitStore(claim_results=(None, unit("a", 0)))
    queue = DurableWorkUnitQueue(store, capacity=1)

    cancelled_claim = asyncio.create_task(queue.claim_next("job-1", "lane-1"))
    await store.claim_started[0].wait()
    cancelled_claim.cancel()
    assert not cancelled_claim.done()

    store.claim_may_commit[0].set()
    store.claim_may_return[0].set()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_claim
    assert store.claim_committed[0].is_set()
    assert store.claim_returned[0].is_set()

    store.claim_may_commit[1].set()
    store.claim_may_return[1].set()
    assert await queue.claim_next("job-1", "lane-2") == unit("a", 0)


@pytest.mark.asyncio
async def test_cancelled_claim_registers_committed_unit_and_preserves_capacity() -> None:
    store = ControlledUnitStore(
        claim_results=(unit("a", 0), unit("b", 1)),
        completion_failures=(False,),
    )
    queue = DurableWorkUnitQueue(store, capacity=1)

    cancelled_claim = asyncio.create_task(queue.claim_next("job-1", "lane-1"))
    await store.claim_started[0].wait()
    store.claim_may_commit[0].set()
    await store.claim_committed[0].wait()
    cancelled_claim.cancel()
    store.claim_may_return[0].set()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_claim
    assert store.claim_returned[0].is_set()

    pending_claim = asyncio.create_task(queue.claim_next("job-1", "lane-2"))
    recovery = asyncio.create_task(queue.complete("job-1", successful_outcome("a", 0)))
    await store.completion_started[0].wait()
    assert not store.claim_started[1].is_set()
    assert store.max_durable_outstanding == 1

    store.completion_may_commit[0].set()
    store.completion_may_return[0].set()
    await recovery
    await store.claim_started[1].wait()
    store.claim_may_commit[1].set()
    store.claim_may_return[1].set()
    assert await pending_claim == unit("b", 1)
    assert store.max_durable_outstanding == 1


@pytest.mark.asyncio
async def test_cancelled_completion_releases_capacity_after_durable_commit() -> None:
    store = ControlledUnitStore(
        claim_results=(unit("a", 0), unit("b", 1)),
        completion_failures=(False,),
    )
    queue = DurableWorkUnitQueue(store, capacity=1)
    store.claim_may_commit[0].set()
    store.claim_may_return[0].set()
    assert await queue.claim_next("job-1", "lane-1") == unit("a", 0)

    cancelled_completion = asyncio.create_task(
        queue.complete("job-1", successful_outcome("a", 0))
    )
    await store.completion_started[0].wait()
    store.completion_may_commit[0].set()
    await store.completion_committed[0].wait()
    cancelled_completion.cancel()
    store.completion_may_return[0].set()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_completion
    assert store.completion_returned[0].is_set()

    store.claim_may_commit[1].set()
    store.claim_may_return[1].set()
    second = await queue.claim_next("job-1", "lane-2")
    assert second == unit("b", 1)


@pytest.mark.asyncio
async def test_cancelled_failed_completion_restores_ownership() -> None:
    store = ControlledUnitStore(
        claim_results=(unit("a", 0),),
        completion_failures=(True, False),
    )
    queue = DurableWorkUnitQueue(store, capacity=1)
    store.claim_may_commit[0].set()
    store.claim_may_return[0].set()
    assert await queue.claim_next("job-1", "lane-1") == unit("a", 0)

    cancelled_completion = asyncio.create_task(
        queue.complete("job-1", successful_outcome("a", 0))
    )
    await store.completion_started[0].wait()
    cancelled_completion.cancel()
    store.completion_may_commit[0].set()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_completion
    assert store.completion_failed[0].is_set()

    store.completion_may_commit[1].set()
    store.completion_may_return[1].set()
    await queue.complete("job-1", successful_outcome("a", 0))
