from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval import live_execution
from cowork_agent.features.ai_chat.memory_eval.arms import Arm
from cowork_agent.features.ai_chat.memory_eval.live_env import LiveEnvironment
from cowork_agent.features.ai_chat.memory_eval.probes import Probe, ProbeSet, ProbeTest, SeedSpec
from cowork_agent.features.ai_chat.memory_eval.runner import run_probe_rows


def test_run_probe_rows_order_and_arming() -> None:
    probe_set = ProbeSet(
        schema_version="2.0.0",
        probe_set_id="rows",
        label="rows",
        seed=SeedSpec((), {}, (), None),
        probes=(
            Probe("probe-1", MemoryType.SHORT_TERM, ProbeTest.RECALL, "one?", ("one",)),
            Probe("probe-2", MemoryType.LONG_TERM, ProbeTest.RECALL, "two?", ("two",)),
        ),
    )
    calls: list[tuple[str, Arm, MemoryType | None]] = []

    async def ask(probe: Probe, arm: Arm, masked: MemoryType | None) -> tuple[str, int]:
        calls.append((probe.probe_id, arm, masked))
        return (probe.expect_any[0] if arm is Arm.FULL else "no match", 1)

    rows = asyncio.run(run_probe_rows(probe_set, ask))
    assert [row.probe_id for row in rows] == ["probe-1", "probe-2"]
    assert calls == [
        ("probe-1", Arm.FULL, None),
        ("probe-1", Arm.ABLATED, MemoryType.SHORT_TERM),
        ("probe-1", Arm.CONTROL, None),
        ("probe-2", Arm.FULL, None),
        ("probe-2", Arm.ABLATED, MemoryType.LONG_TERM),
        ("probe-2", Arm.CONTROL, None),
    ]


class _Pool:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def close(self) -> None:
        self.events.append("pool-close")


class _Session:
    def __init__(self, **kwargs: object) -> None:
        self.gateways: list[object] = []
        self.seed_failures: list[str] = []
        self.ask_errors: list[dict[str, object]] = []
        for name, value in kwargs.items():
            setattr(self, name, value)


def _configure(monkeypatch: pytest.MonkeyPatch, scratch_path: Path | None = None) -> list[str]:
    events: list[str] = []
    pool = _Pool(events)

    async def build_adapters(environment: LiveEnvironment, probe_set: ProbeSet):
        del probe_set
        if scratch_path is not None:
            scratch_path.write_text("scratch", encoding="utf-8")
        return object(), ["adapter finding"], pool

    async def ask(session: _Session, probe: Probe, arm: Arm, masked: MemoryType | None):
        del session, masked
        return (probe.expect_any[0] if arm is Arm.FULL else "no match", 7)

    async def cleanup(gateways: list[object]) -> int:
        del gateways
        events.append("teardown")
        return 0

    monkeypatch.setattr(live_execution, "_build_adapters", build_adapters)
    monkeypatch.setattr(
        live_execution,
        "build_identity",
        lambda *_: SimpleNamespace(run_key="run-key", nonce="nonce"),
    )
    monkeypatch.setattr(live_execution, "LiveSession", _Session)
    monkeypatch.setattr(live_execution, "ask_live", ask)
    monkeypatch.setattr(live_execution, "teardown", cleanup)
    monkeypatch.setattr(live_execution, "unavailable_scopes", lambda _: ())
    return events


def test_execute_memory_shard_success_and_scratch_sqlite_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scratch_path = tmp_path / "memeval-one.db"
    events = _configure(monkeypatch, scratch_path=scratch_path)
    probe_set = ProbeSet(
        "2.0.0",
        "live",
        "live",
        SeedSpec((), {}, (), None),
        (Probe("probe-1", MemoryType.SHORT_TERM, ProbeTest.RECALL, "one?", ("one",)),),
    )

    result = asyncio.run(
        live_execution.execute_memory_shard(
            probe_set,
            LiveEnvironment(None, scratch_path, True, True, "", sqlite_path_owned=True),
            object(),
            provider="provider",
            model="model",
        )
    )

    assert [row.probe_id for row in result.rows] == ["probe-1"]
    assert result.seed_failure_ids == ("adapter finding",)
    assert result.scratch_removed is True
    assert events == ["teardown", "pool-close"]
    assert not scratch_path.exists()


def test_execute_memory_shard_unowned_sqlite_safety(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    user_path = tmp_path / "chat.db"
    _configure(monkeypatch, scratch_path=user_path)
    probe_set = ProbeSet(
        "2.0.0",
        "live",
        "live",
        SeedSpec((), {}, (), None),
        (Probe("probe-1", MemoryType.SHORT_TERM, ProbeTest.RECALL, "one?", ("one",)),),
    )

    result = asyncio.run(
        live_execution.execute_memory_shard(
            probe_set,
            LiveEnvironment(None, user_path, True, True, ""),
            object(),
            provider="provider",
            model="model",
        )
    )
    assert result.scratch_removed is False
    assert user_path.exists()


def test_execute_memory_shard_abort_cancellation_and_error_findings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scratch_path = tmp_path / "memeval-provider.db"
    events = _configure(monkeypatch, scratch_path=scratch_path)

    class ProviderFailure(RuntimeError):
        pass

    calls = 0

    async def fail_after_one(
        session: _Session, probe: Probe, arm: Arm, masked: MemoryType | None
    ) -> tuple[str, int]:
        del session, probe, arm, masked
        nonlocal calls
        calls += 1
        if calls == 1:
            return "one", 1
        raise ProviderFailure("tripped")

    monkeypatch.setattr(live_execution, "ask_live", fail_after_one)
    monkeypatch.setattr(live_execution, "ExcessiveSeedFailuresError", ProviderFailure)

    probe_set = ProbeSet(
        "2.0.0",
        "live",
        "live",
        SeedSpec((), {}, (), None),
        (Probe("probe-1", MemoryType.SHORT_TERM, ProbeTest.RECALL, "one?", ("one",)),),
    )
    result = asyncio.run(
        live_execution.execute_memory_shard(
            probe_set,
            LiveEnvironment(None, scratch_path, True, True, "", sqlite_path_owned=True),
            object(),
            provider="provider",
            model="model",
        )
    )

    assert result.rows == ()
    assert result.provider_findings == ("aborted: tripped",)
    assert events == ["teardown", "pool-close"]
    assert not scratch_path.exists()
