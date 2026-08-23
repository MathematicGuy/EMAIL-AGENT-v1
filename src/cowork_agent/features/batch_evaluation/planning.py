"""Pure worker planning and deterministic work sharding."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

from .contracts import EvaluationWarning, WorkerResolution

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class IndexedItem(Generic[T]):
    """One input item paired with its ordinal from the original sequence."""

    ordinal: int
    value: T


class DataSharder:
    """Partition work in stable round-robin order without empty shards."""

    def partition(
        self, items: Sequence[T], shard_count: int
    ) -> tuple[tuple[IndexedItem[T], ...], ...]:
        _require_positive_int(shard_count, "shard_count")
        if not items:
            return ()

        shard_total = min(shard_count, len(items))
        shards: list[list[IndexedItem[T]]] = [[] for _ in range(shard_total)]
        for ordinal, item in enumerate(items):
            shards[ordinal % shard_total].append(IndexedItem(ordinal=ordinal, value=item))
        return tuple(tuple(shard) for shard in shards)


def resolve_worker_count(
    requested: int, healthy_credentials: int, ready_work: int
) -> WorkerResolution:
    """Resolve a safe lane count from requested capacity and available work."""

    _require_positive_int(requested, "requested_workers")
    _require_non_negative_int(healthy_credentials, "healthy_credentials")
    _require_non_negative_int(ready_work, "ready_work")

    effective = min(requested, healthy_credentials, ready_work)
    warning = (
        EvaluationWarning(
            code="WORKER_COUNT_REDUCED",
            message="Worker count was reduced because fewer credentials are healthy.",
            details={
                "requested_workers": requested,
                "effective_workers": effective,
                "healthy_credentials": healthy_credentials,
            },
        )
        if requested > healthy_credentials and healthy_credentials <= ready_work
        else None
    )
    return WorkerResolution(
        requested_workers=requested,
        effective_workers=effective,
        healthy_credentials=healthy_credentials,
        ready_work=ready_work,
        warning=warning,
    )


def _require_positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _require_non_negative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value
