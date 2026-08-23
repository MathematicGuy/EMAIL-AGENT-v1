"""Exclusive, non-secret credential leases for evaluation lanes."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Self

from cowork_agent.features.batch_evaluation.contracts import CredentialState
from cowork_agent.integrations.key_rotation import parse_api_keys_from_env


@dataclass(slots=True)
class _CredentialRecord:
    alias: str
    api_key: str = field(repr=False)
    state: CredentialState = CredentialState.AVAILABLE
    cooling_until: float | None = field(default=None, repr=False)


class CredentialLease:
    """A lease exposes only its stable alias; its key stays internal to the adapter."""

    __slots__ = ("_api_key", "_pool", "_record", "_settled", "alias")

    def __init__(
        self,
        pool: CredentialLeasingPool,
        record: _CredentialRecord,
    ) -> None:
        self.alias = record.alias
        self._api_key = record.api_key
        self._pool = pool
        self._record = record
        self._settled = False

    def __repr__(self) -> str:
        return f"CredentialLease(alias={self.alias!r})"

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        del args
        await self.release()

    async def release(self) -> None:
        """Return an unused lease to the available pool."""

        if not self._settled:
            await self._pool.release(self)

    async def cool_down(self, retry_after_seconds: int) -> None:
        """Apply the provider's non-negative cooldown and settle this lease."""

        if not self._settled:
            await self._pool.cool_down(self, retry_after_seconds)

    async def disable(self) -> None:
        """Permanently stop using this credential and settle this lease."""

        if not self._settled:
            await self._pool.disable(self)


class CredentialLeasingPool:
    """Own one exclusive lease and lifecycle state for every configured key."""

    def __init__(
        self,
        prefix: str,
        keys: tuple[str, ...],
        *,
        clock: Callable[[], float],
    ) -> None:
        provider_name = prefix.removesuffix("_API_KEY").removesuffix("_KEY").lower()
        if not provider_name:
            raise ValueError("credential prefix must name a provider")
        self._records = [
            _CredentialRecord(alias=f"{provider_name}-{index}", api_key=key)
            for index, key in enumerate(keys, start=1)
        ]
        self._clock = clock
        self._lock = asyncio.Lock()
        self._next_index = 0

    @classmethod
    def from_env(
        cls,
        prefix: str,
        environ: Mapping[str, str] | None = None,
        *,
        clock: Callable[[], float] | None = None,
    ) -> CredentialLeasingPool:
        """Discover the numbered keys with the shared parsing and deduplication rules."""

        if environ is None:
            environ = os.environ
        return cls(prefix, parse_api_keys_from_env(environ, prefix), clock=clock or _monotonic)

    async def lease(self) -> CredentialLease:
        """Lease exactly one currently healthy credential, or fail without exposing it."""

        async with self._lock:
            self._recover_cooled_credentials()
            record_count = len(self._records)
            for offset in range(record_count):
                index = (self._next_index + offset) % record_count
                record = self._records[index]
                if record.state is CredentialState.AVAILABLE:
                    record.state = CredentialState.LEASED
                    self._next_index = (index + 1) % record_count
                    return CredentialLease(self, record)
        raise RuntimeError("No healthy credential is available for leasing")

    async def release(self, lease: CredentialLease) -> None:
        async with self._lock:
            record = self._require_active_lease(lease)
            record.state = CredentialState.AVAILABLE
            lease._settled = True

    async def cool_down(self, lease: CredentialLease, retry_after_seconds: int) -> None:
        if (
            isinstance(retry_after_seconds, bool)
            or not isinstance(retry_after_seconds, int)
            or retry_after_seconds < 0
        ):
            raise ValueError("retry_after_seconds must be a non-negative integer")
        async with self._lock:
            record = self._require_active_lease(lease)
            record.state = CredentialState.COOLING_DOWN
            record.cooling_until = self._clock() + retry_after_seconds
            lease._settled = True

    async def disable(self, lease: CredentialLease) -> None:
        async with self._lock:
            record = self._require_active_lease(lease)
            record.state = CredentialState.DISABLED
            record.cooling_until = None
            lease._settled = True

    def state_for(self, alias: str) -> CredentialState:
        """Return a non-secret lifecycle state for one known alias."""

        self._recover_cooled_credentials()
        for record in self._records:
            if record.alias == alias:
                return record.state
        raise ValueError(f"Unknown credential alias: {alias}")

    @property
    def healthy_count(self) -> int:
        """Count credentials that are available now; leased keys remain unavailable."""

        self._recover_cooled_credentials()
        return sum(record.state is CredentialState.AVAILABLE for record in self._records)

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple(record.alias for record in self._records)

    def _recover_cooled_credentials(self) -> None:
        now = self._clock()
        for record in self._records:
            if (
                record.state is CredentialState.COOLING_DOWN
                and record.cooling_until is not None
                and record.cooling_until <= now
            ):
                record.state = CredentialState.AVAILABLE
                record.cooling_until = None

    def _require_active_lease(self, lease: CredentialLease) -> _CredentialRecord:
        if lease._pool is not self:
            raise RuntimeError("Credential lease belongs to another pool")
        if lease._settled or lease._record.state is not CredentialState.LEASED:
            raise RuntimeError("Credential lease is no longer active")
        return lease._record


def _monotonic() -> float:
    import time

    return time.monotonic()
