"""Unit tests for the purge_chat_memory script's core logic."""

import asyncio
from datetime import UTC, datetime

from cowork_agent.features.ai_chat.retention import MemoryPurgeReport


class FakePurgePort:
    def __init__(self, count: int = 0) -> None:
        self.count = count
        self.calls: list[datetime] = []

    async def purge_expired(self, now: datetime) -> int:
        self.calls.append(now)
        return self.count


async def _run_purge_with_fakes(
    profiles: FakePurgePort, episodes: FakePurgePort
) -> MemoryPurgeReport:
    """Reproduce the coordinator logic without requiring PostgreSQL."""

    from cowork_agent.features.ai_chat.retention import MemoryPurgeCoordinator

    coordinator = MemoryPurgeCoordinator(profiles, episodes)
    return await coordinator.purge_expired(datetime.now(UTC))


def test_run_purge_returns_metadata_report() -> None:
    profiles = FakePurgePort(2)
    episodes = FakePurgePort(5)
    report = asyncio.run(_run_purge_with_fakes(profiles, episodes))

    assert report == MemoryPurgeReport(2, 5, True)
    assert len(profiles.calls) == 1
    assert len(episodes.calls) == 1
    assert profiles.calls[0].tzinfo is not None
    assert episodes.calls[0].tzinfo is not None


def test_run_purge_with_zero_expired_rows() -> None:
    profiles = FakePurgePort(0)
    episodes = FakePurgePort(0)
    report = asyncio.run(_run_purge_with_fakes(profiles, episodes))

    assert report == MemoryPurgeReport(0, 0, True)


def test_purge_script_module_is_importable() -> None:
    """The script module must be importable without side effects."""

    import importlib.util
    from pathlib import Path

    script_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "purge_chat_memory.py"
    )
    spec = importlib.util.spec_from_file_location("purge_chat_memory", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "run_purge")
    assert hasattr(module, "_main")
