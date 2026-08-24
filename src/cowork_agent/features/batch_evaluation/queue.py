"""Durable work-unit queue projection."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Protocol, TypeVar

from .contracts import WorkUnit, WorkUnitOutcome

_OperationResult = TypeVar("_OperationResult")
_UnitKey = tuple[str, str]


async def _resolve_durable_operation(
    operation: Awaitable[_OperationResult],
) -> tuple[_OperationResult | None, BaseException | None, bool]:
    """Wait for a shielded store operation and record caller cancellation."""

    task = asyncio.ensure_future(operation)
    cancellation_requested = False
    while True:
        try:
            return await asyncio.shield(task), None, cancellation_requested
        except asyncio.CancelledError:
            cancellation_requested = True
            if task.done():
                try:
                    return task.result(), None, cancellation_requested
                except BaseException as error:
                    return None, error, cancellation_requested
        except BaseException as error:
            return None, error, cancellation_requested


class UnitStore(Protocol):
    """Durable source of truth for atomic work-unit transitions."""

    async def claim_ready_unit(self, job_id: str, worker_id: str) -> WorkUnit | None: ...

    async def claim_ready_unit_by_id(
        self, job_id: str, unit_id: str, worker_id: str
    ) -> WorkUnit | None: ...

    async def complete_unit(self, job_id: str, outcome: WorkUnitOutcome) -> None: ...


class DurableWorkUnitQueue:
    """Bounded in-memory projection that never owns work-unit state."""

    def __init__(self, store: UnitStore, capacity: int) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        self._store = store
        self._capacity = capacity
        self._slots = asyncio.Semaphore(capacity)
        self._owned_slots: set[_UnitKey] = set()
        self._completing_slots: set[_UnitKey] = set()

    async def claim_next(self, job_id: str, worker_id: str) -> WorkUnit | None:
        """Atomically claim durable ready work; no in-memory claim is authoritative."""

        await self._slots.acquire()
        unit, error, cancellation_requested = await _resolve_durable_operation(
            self._store.claim_ready_unit(job_id, worker_id)
        )
        if error is not None:
            self._slots.release()
            if cancellation_requested:
                raise asyncio.CancelledError from error
            raise error
        if unit is None:
            self._slots.release()
        else:
            key = (job_id, unit.unit_id)
            if key in self._owned_slots or key in self._completing_slots:
                self._slots.release()
                raise RuntimeError("store returned an already outstanding work unit")
            self._owned_slots.add(key)
        if cancellation_requested:
            raise asyncio.CancelledError
        return unit

    async def complete(self, job_id: str, outcome: WorkUnitOutcome) -> None:
        """Complete durable work and free capacity for one pending claim."""

        key = (job_id, outcome.unit_id)
        if key not in self._owned_slots:
            raise ValueError("work unit is not outstanding for this job")

        self._owned_slots.remove(key)
        self._completing_slots.add(key)
        _, error, cancellation_requested = await _resolve_durable_operation(
            self._store.complete_unit(job_id, outcome)
        )
        if error is not None:
            self._completing_slots.remove(key)
            self._owned_slots.add(key)
            if cancellation_requested:
                raise asyncio.CancelledError from error
            raise error

        self._completing_slots.remove(key)
        self._slots.release()
        if cancellation_requested:
            raise asyncio.CancelledError
