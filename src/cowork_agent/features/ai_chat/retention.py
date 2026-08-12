"""Explicit infrastructure-invoked durable chat-memory purge coordination."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from langfuse import observe

from cowork_agent.domain.chat_contracts import MemoryType

from .memory_observability import (
    MemoryOperation,
    MemoryOperationEvent,
    MemoryOperationSink,
    MemoryOutcome,
)


def compute_expires_at(
    now: datetime, retention_seconds: int | None
) -> datetime | None:
    """Return ``now + retention_seconds`` or ``None`` when retention is unset.

    ``now`` must be timezone-aware UTC. ``retention_seconds`` must be a
    positive ``int`` (not ``bool``) or ``None``.
    """

    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError("now must be timezone-aware UTC")
    if retention_seconds is None:
        return None
    if isinstance(retention_seconds, bool) or not isinstance(retention_seconds, int):
        raise ValueError("retention_seconds must be a positive int or None")
    if retention_seconds <= 0:
        raise ValueError("retention_seconds must be a positive int or None")
    return now + timedelta(seconds=retention_seconds)


class ExpiredMemoryPurgePort(Protocol):
    async def purge_expired(self, now: datetime) -> int: ...


@dataclass(frozen=True, slots=True)
class MemoryPurgeReport:
    profile_count: int
    episode_count: int
    complete: bool

    def __post_init__(self) -> None:
        if (
            isinstance(self.profile_count, bool)
            or isinstance(self.episode_count, bool)
            or not isinstance(self.profile_count, int)
            or not isinstance(self.episode_count, int)
            or self.profile_count < 0
            or self.episode_count < 0
            or self.complete is not True
        ):
            raise ValueError("purge report requires nonnegative completed counts")


class MemoryPurgeCoordinator:
    def __init__(
        self,
        profiles: ExpiredMemoryPurgePort,
        episodes: ExpiredMemoryPurgePort,
        *,
        sink: MemoryOperationSink | None = None,
    ) -> None:
        self._profiles = profiles
        self._episodes = episodes
        self._sink = sink

    @observe(name="chat_retention_purge_expired")
    async def purge_expired(self, now: datetime) -> MemoryPurgeReport:
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError("now must be timezone-aware UTC")
        profile_count = await self._profiles.purge_expired(now)
        episode_count = await self._episodes.purge_expired(now)
        if self._sink is not None:
            try:
                self._sink.emit(
                    MemoryOperationEvent(
                        memory_type=MemoryType.LONG_TERM,
                        operation=MemoryOperation.DELETE,
                        outcome=MemoryOutcome.SUCCESS,
                        result_count=profile_count,
                        reason_code="purge_expired",
                    )
                )
            except Exception:
                pass
            try:
                self._sink.emit(
                    MemoryOperationEvent(
                        memory_type=MemoryType.EPISODIC,
                        operation=MemoryOperation.DELETE,
                        outcome=MemoryOutcome.SUCCESS,
                        result_count=episode_count,
                        reason_code="purge_expired",
                    )
                )
            except Exception:
                pass
        return MemoryPurgeReport(profile_count, episode_count, True)
