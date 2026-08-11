"""Explicit infrastructure-invoked durable chat-memory purge coordination."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol


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
    def __init__(self, profiles: ExpiredMemoryPurgePort, episodes: ExpiredMemoryPurgePort) -> None:
        self._profiles = profiles
        self._episodes = episodes

    async def purge_expired(self, now: datetime) -> MemoryPurgeReport:
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError("now must be timezone-aware UTC")
        profile_count = await self._profiles.purge_expired(now)
        episode_count = await self._episodes.purge_expired(now)
        return MemoryPurgeReport(profile_count, episode_count, True)
