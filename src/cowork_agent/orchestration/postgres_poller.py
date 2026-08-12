"""Small metadata-only polling loop for durable Postgres jobs."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

logger = logging.getLogger(__name__)


class ClaimableJobSource(Protocol):
    async def next_id(self) -> str | None: ...


class JobExecutor(Protocol):
    async def execute(self, identifier: str) -> object: ...


class PollerMaintenance(Protocol):
    async def run(self) -> None: ...


class CallableJobSource:
    """Adapt a repository's metadata-only next-job method to the poller port."""

    def __init__(self, next_identifier: Callable[[], Awaitable[str | None]]) -> None:
        self._next_identifier = next_identifier

    async def next_id(self) -> str | None:
        return await self._next_identifier()


class PostgresPoller:
    """Discover one opaque ID per iteration; repository CAS owns execution safety."""

    def __init__(
        self,
        source: ClaimableJobSource,
        executor: JobExecutor,
        *,
        interval_seconds: float = 1.0,
        maintenance: PollerMaintenance | None = None,
        maintenance_interval_seconds: float = 60.0,
    ) -> None:
        if interval_seconds <= 0 or maintenance_interval_seconds <= 0:
            raise ValueError("poller intervals must be positive")
        self._source = source
        self._executor = executor
        self._interval_seconds = interval_seconds
        self._maintenance = maintenance
        self._maintenance_interval_seconds = maintenance_interval_seconds

    async def poll_once(self) -> bool:
        identifier = await self._source.next_id()
        if identifier is None:
            return False
        await self._executor.execute(identifier)
        return True

    async def maintain_once(self) -> None:
        if self._maintenance is not None:
            await self._maintenance.run()

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        next_maintenance = 0.0
        while stop is None or not stop.is_set():
            now = asyncio.get_running_loop().time()
            if now >= next_maintenance:
                try:
                    await self.maintain_once()
                except Exception:
                    logger.exception("Postgres job maintenance iteration failed")
                next_maintenance = now + self._maintenance_interval_seconds
            try:
                found = await self.poll_once()
            except Exception:
                # Jobs are still durable in Postgres; a later iteration retries.
                logger.exception("Postgres job polling iteration failed")
                found = False
            if not found:
                await asyncio.sleep(self._interval_seconds)
