from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval import live_execution
from cowork_agent.features.ai_chat.memory_eval.arms import Arm
from cowork_agent.features.ai_chat.memory_eval.live_env import LiveEnvironment
from cowork_agent.features.ai_chat.memory_eval.probes import Probe, ProbeSet, ProbeTest, SeedSpec
from cowork_agent.features.ai_chat.memory_eval.runner import run_probe_rows


def test_run_probe_rows_keeps_probe_and_arm_order() -> None:
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
    assert [row.latency_ms for row in rows] == [3, 3]


def _live_probe_set() -> ProbeSet:
    return ProbeSet(
        schema_version="2.0.0",
        probe_set_id="live",
        label="live",
        seed=SeedSpec((), {}, (), None),
        probes=(
            Probe("probe-1", MemoryType.SHORT_TERM, ProbeTest.RECALL, "one?", ("one",)),
        ),
    )


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


def _configure_live_execution(
    monkeypatch: pytest.MonkeyPatch,
    *,
    scratch_path: Path | None = None,
) -> tuple[list[dict[str, object]], list[str]]:
    transcript: list[dict[str, object]] = []
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
    monkeypatch.setattr(live_execution, "build_identity", lambda *_: SimpleNamespace(
        run_key="run-key", nonce="nonce"
    ))
    monkeypatch.setattr(live_execution, "LiveSession", _Session)
    monkeypatch.setattr(live_execution, "ask_live", ask)
    monkeypatch.setattr(live_execution, "teardown", cleanup)
    monkeypatch.setattr(live_execution, "unavailable_scopes", lambda _: ())
    return transcript, events


def test_execute_memory_shard_returns_private_rows_and_cleans_a_scratch_sqlite_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scratch_path = tmp_path / "memeval-one.db"
    transcript, events = _configure_live_execution(monkeypatch, scratch_path=scratch_path)

    result = asyncio.run(
        live_execution.execute_memory_shard(
            _live_probe_set(),
            LiveEnvironment(None, scratch_path, True, True, ""),
            object(),
            provider="provider",
            model="model",
        )
    )

    assert [row.probe_id for row in result.rows] == ["probe-1"]
    assert result.seed_failure_ids == ("adapter finding",)
    assert result.nonce == "nonce"
    assert result.provider_findings == ("adapter finding",)
    assert len(result.private_transcript) == 3
    assert result.scratch_removed is True
    assert events == ["teardown", "pool-close"]
    assert not scratch_path.exists()


def test_execute_memory_shard_never_unlinks_a_non_scratch_sqlite_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    user_path = tmp_path / "chat.db"
    _transcript, _events = _configure_live_execution(monkeypatch, scratch_path=user_path)

    result = asyncio.run(
        live_execution.execute_memory_shard(
            _live_probe_set(),
            LiveEnvironment(None, user_path, True, True, ""),
            object(),
            provider="provider",
            model="model",
        )
    )

    assert result.scratch_removed is False
    assert user_path.exists()


def test_execute_memory_shard_returns_an_aborted_provider_finding_after_a_partial_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scratch_path = tmp_path / "memeval-provider.db"
    _transcript, events = _configure_live_execution(monkeypatch, scratch_path=scratch_path)

    class ProviderFailure(RuntimeError):
        pass

    calls = 0

    async def fail_after_one_record(
        session: _Session, probe: Probe, arm: Arm, masked: MemoryType | None
    ) -> tuple[str, int]:
        del session, probe, arm, masked
        nonlocal calls
        calls += 1
        if calls == 1:
            return "one", 1
        raise ProviderFailure("tripped")

    monkeypatch.setattr(live_execution, "ask_live", fail_after_one_record)
    monkeypatch.setattr(live_execution, "ExcessiveSeedFailuresError", ProviderFailure)

    result = asyncio.run(
        live_execution.execute_memory_shard(
            _live_probe_set(),
            LiveEnvironment(None, scratch_path, True, True, ""),
            object(),
            provider="provider",
            model="model",
        )
    )

    assert result.rows == ()
    assert result.private_transcript[0]["reply"] == "one"
    assert result.provider_findings == ("aborted: tripped", "adapter finding")
    assert events == ["teardown", "pool-close"]
    assert not scratch_path.exists()


def test_execute_memory_shard_cleans_up_before_propagating_cancellation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scratch_path = tmp_path / "memeval-cancel.db"
    _transcript, events = _configure_live_execution(monkeypatch, scratch_path=scratch_path)

    async def cancel(*args: object) -> tuple[str, int]:
        del args
        raise asyncio.CancelledError()

    monkeypatch.setattr(live_execution, "ask_live", cancel)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            live_execution.execute_memory_shard(
                _live_probe_set(),
                LiveEnvironment(None, scratch_path, True, True, ""),
                object(),
                provider="provider",
                model="model",
            )
        )

    assert events == ["teardown", "pool-close"]
    assert not scratch_path.exists()


def test_execute_memory_shard_removes_scratch_after_teardown_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scratch_path = tmp_path / "memeval-teardown.db"
    _transcript, events = _configure_live_execution(monkeypatch, scratch_path=scratch_path)

    async def fail_teardown(gateways: list[object]) -> int:
        del gateways
        events.append("teardown")
        raise RuntimeError("teardown failed")

    monkeypatch.setattr(live_execution, "teardown", fail_teardown)

    with pytest.raises(RuntimeError, match="teardown failed"):
        asyncio.run(
            live_execution.execute_memory_shard(
                _live_probe_set(),
                LiveEnvironment(None, scratch_path, True, True, ""),
                object(),
                provider="provider",
                model="model",
            )
        )

    assert events == ["teardown", "pool-close"]
    assert not scratch_path.exists()


def test_build_memory_report_merges_rows_and_calls_report_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []
    rows = asyncio.run(run_probe_rows(_live_probe_set(), _scripted_rows))

    def build_report(*args: object, **kwargs: object) -> dict[str, object]:
        calls.append((*args, kwargs))
        return {"report": "built"}

    monkeypatch.setattr(live_execution, "build_report", build_report)
    result = live_execution.MemoryShardResult(
        rows=rows,
        seed_failure_ids=("z",),
        private_transcript=(),
        nonce="nonce",
        provider_findings=("z",),
        scratch_removed=True,
    )

    assert live_execution.build_memory_report(
        _live_probe_set(),
        (result,),
        provider="provider",
        model="model",
        ran_at=datetime(2026, 8, 23),
    ) == {"report": "built"}
    assert len(calls) == 1
    assert calls[0][1] == rows
    assert calls[0][-1]["nonce"] == "nonce"
    assert calls[0][-1]["seed_failures"] == ("z",)


async def _scripted_rows(
    probe: Probe, arm: Arm, masked: MemoryType | None
) -> tuple[str, int]:
    del masked
    return (probe.expect_any[0] if arm is Arm.FULL else "no match", 1)
