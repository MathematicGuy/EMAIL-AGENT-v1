from __future__ import annotations

import pytest

from cowork_agent.features.batch_evaluation.contracts import UnitState, WorkUnit, WorkUnitOutcome
from cowork_agent.features.batch_evaluation.queue import DurableWorkUnitQueue


def unit(unit_id: str, ordinal: int) -> WorkUnit:
    return WorkUnit(unit_id=unit_id, ordinal=ordinal, payload={"item_id": f"item-{ordinal}"})


class FakeUnitStore:
    def __init__(self, ready: tuple[WorkUnit, ...]) -> None:
        self._ready = ready
        self._claimed: set[str] = set()
        self._completed: set[str] = set()
        self.claim_calls: list[tuple[str, str]] = []

    async def claim_ready_unit(self, job_id: str, worker_id: str) -> WorkUnit | None:
        self.claim_calls.append((job_id, worker_id))
        for ready_unit in self._ready:
            if (
                ready_unit.unit_id not in self._claimed
                and ready_unit.unit_id not in self._completed
            ):
                self._claimed.add(ready_unit.unit_id)
                return ready_unit
        return None

    async def complete_unit(self, job_id: str, outcome: WorkUnitOutcome) -> None:
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


@pytest.mark.asyncio
async def test_queue_claims_from_store_and_never_replays_completed_unit() -> None:
    store = FakeUnitStore(ready=(unit("a", 0), unit("b", 1)))
    queue = DurableWorkUnitQueue(store, capacity=1)

    first = await queue.claim_next("job-1", "lane-1")
    assert first is not None
    assert first.unit_id == "a"

    await store.complete("job-1", "a")

    second = await queue.claim_next("job-1", "lane-1")
    assert second is not None
    assert second.unit_id == "b"
    assert store.claim_calls == [("job-1", "lane-1"), ("job-1", "lane-1")]
