"""Durable work-unit queue projection."""

from __future__ import annotations

from typing import Protocol

from .contracts import WorkUnit, WorkUnitOutcome


class UnitStore(Protocol):
    """Durable source of truth for atomic work-unit transitions."""

    async def claim_ready_unit(self, job_id: str, worker_id: str) -> WorkUnit | None: ...

    async def complete_unit(self, job_id: str, outcome: WorkUnitOutcome) -> None: ...


class DurableWorkUnitQueue:
    """Bounded in-memory projection that never owns work-unit state."""

    def __init__(self, store: UnitStore, capacity: int) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        self._store = store
        self._capacity = capacity

    async def claim_next(self, job_id: str, worker_id: str) -> WorkUnit | None:
        """Atomically claim durable ready work; no in-memory claim is authoritative."""

        return await self._store.claim_ready_unit(job_id, worker_id)
