import asyncio
from datetime import UTC, datetime, timedelta, tzinfo

import pytest

from cowork_agent.features.ai_chat.retention import MemoryPurgeCoordinator, MemoryPurgeReport


class Store:
    def __init__(self, counts: list[int]) -> None:
        self.counts = counts
        self.seen: list[datetime] = []

    async def purge_expired(self, now: datetime) -> int:
        self.seen.append(now)
        return self.counts.pop(0)


def test_purge_uses_one_utc_boundary_and_is_retryable() -> None:
    profiles, episodes = Store([2, 0]), Store([3, 0])
    coordinator = MemoryPurgeCoordinator(profiles, episodes)
    now = datetime(2026, 8, 10, tzinfo=UTC)
    first = asyncio.run(coordinator.purge_expired(now))
    second = asyncio.run(coordinator.purge_expired(now))
    assert (first.profile_count, first.episode_count, first.complete) == (2, 3, True)
    assert (second.profile_count, second.episode_count) == (0, 0)
    assert profiles.seen == episodes.seen == [now, now]
    assert "tenant" not in repr(first) and "content" not in repr(first)


def test_purge_rejects_naive_datetime_and_propagates_store_failure() -> None:
    with pytest.raises(ValueError):
        asyncio.run(MemoryPurgeCoordinator(Store([0]), Store([0])).purge_expired(datetime.now()))

    class Failing(Store):
        async def purge_expired(self, now: datetime) -> int:
            raise RuntimeError("failure")

    with pytest.raises(RuntimeError):
        coordinator = MemoryPurgeCoordinator(Failing([]), Store([0]))
        asyncio.run(coordinator.purge_expired(datetime.now(UTC)))


def test_purge_accepts_any_zero_offset_timezone_and_report_is_strict_metadata() -> None:
    class ZeroOffset(tzinfo):
        def utcoffset(self, dt: datetime | None) -> timedelta:
            return timedelta(0)

        def dst(self, dt: datetime | None) -> timedelta:
            return timedelta(0)

    result = asyncio.run(
        MemoryPurgeCoordinator(Store([0]), Store([0])).purge_expired(
            datetime(2026, 8, 10, tzinfo=ZeroOffset())
        )
    )
    assert result.complete is True
    for kwargs in (
        {"profile_count": True, "episode_count": 0, "complete": True},
        {"profile_count": -1, "episode_count": 0, "complete": True},
        {"profile_count": 0, "episode_count": 0, "complete": "yes"},
    ):
        with pytest.raises(ValueError):
            MemoryPurgeReport(**kwargs)
    assert not {"scheduler", "thread", "timer", "start"} & set(dir(MemoryPurgeCoordinator))
