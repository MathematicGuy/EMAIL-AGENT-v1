from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval import live_execution
from cowork_agent.features.ai_chat.memory_eval.arms import Arm
from cowork_agent.features.ai_chat.memory_eval.live_env import LiveEnvironment
from cowork_agent.features.ai_chat.memory_eval.probes import Probe, ProbeSet, ProbeTest, SeedSpec
from cowork_agent.features.ai_chat.memory_eval.report import ProbeRow
from cowork_agent.features.ai_chat.memory_eval.runner import run_probe_rows
from cowork_agent.features.ai_chat.memory_eval.scoring import Outcome


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


def _two_probe_set() -> ProbeSet:
    return ProbeSet(
        schema_version="2.0.0",
        probe_set_id="live-two",
        label="live two",
        seed=SeedSpec((), {}, (), None),
        probes=(
            Probe("probe-1", MemoryType.SHORT_TERM, ProbeTest.RECALL, "one?", ("one",)),
            Probe("probe-2", MemoryType.LONG_TERM, ProbeTest.RECALL, "two?", ("two",)),
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
            LiveEnvironment(None, scratch_path, True, True, "", sqlite_path_owned=True),
            object(),
            provider="provider",
            model="model",
        )
    )

    assert [row.probe_id for row in result.rows] == ["probe-1"]
    assert result.seed_failure_ids == ("adapter finding",)
    assert result.nonce == "nonce"
    assert result.provider_findings == ()
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


def test_execute_memory_shard_never_unlinks_an_unowned_matching_scratch_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    caller_path = tmp_path / "memeval-caller-owned.db"
    _transcript, _events = _configure_live_execution(monkeypatch, scratch_path=caller_path)

    result = asyncio.run(
        live_execution.execute_memory_shard(
            _live_probe_set(),
            LiveEnvironment(None, caller_path, True, True, ""),
            object(),
            provider="provider",
            model="model",
        )
    )

    assert result.scratch_removed is False
    assert caller_path.exists()


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
            LiveEnvironment(None, scratch_path, True, True, "", sqlite_path_owned=True),
            object(),
            provider="provider",
            model="model",
        )
    )

    assert result.rows == ()
    assert result.private_transcript[0]["reply"] == "one"
    assert result.seed_failure_ids == ("adapter finding",)
    assert result.provider_findings == ("aborted: tripped",)
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
                LiveEnvironment(None, scratch_path, True, True, "", sqlite_path_owned=True),
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
                LiveEnvironment(None, scratch_path, True, True, "", sqlite_path_owned=True),
                object(),
                provider="provider",
                model="model",
            )
        )

    assert events == ["teardown", "pool-close"]
    assert not scratch_path.exists()


def test_execute_memory_shard_closes_pool_and_removes_owned_scratch_after_session_setup_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scratch_path = tmp_path / "memeval-session-setup.db"
    _transcript, events = _configure_live_execution(monkeypatch, scratch_path=scratch_path)

    class BrokenSession:
        def __init__(self, **kwargs: object) -> None:
            del kwargs
            raise RuntimeError("session setup failed")

    monkeypatch.setattr(live_execution, "LiveSession", BrokenSession)

    with pytest.raises(RuntimeError, match="session setup failed"):
        asyncio.run(
            live_execution.execute_memory_shard(
                _live_probe_set(),
                LiveEnvironment(None, scratch_path, True, True, "", sqlite_path_owned=True),
                object(),
                provider="provider",
                model="model",
            )
        )

    assert events == ["pool-close"]
    assert not scratch_path.exists()


def test_execute_memory_shard_removes_owned_scratch_when_adapter_acquisition_is_cancelled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scratch_path = tmp_path / "memeval-adapter-cancel.db"
    scratch_path.write_text("scratch", encoding="utf-8")
    _configure_live_execution(monkeypatch, scratch_path=scratch_path)

    async def cancel_during_adapter_setup(*args: object) -> object:
        del args
        raise asyncio.CancelledError()

    monkeypatch.setattr(live_execution, "_build_adapters", cancel_during_adapter_setup)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            live_execution.execute_memory_shard(
                _live_probe_set(),
                LiveEnvironment(None, scratch_path, True, True, "", sqlite_path_owned=True),
                object(),
                provider="provider",
                model="model",
            )
        )

    assert not scratch_path.exists()


def test_build_adapters_closes_an_acquired_pool_when_open_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Pool:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def open(self, *, wait: bool) -> None:
            assert wait is True
            events.append("open")
            raise asyncio.CancelledError()

        async def close(self) -> None:
            events.append("close")

    pool_module = ModuleType("psycopg_pool")
    pool_module.AsyncConnectionPool = Pool  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg_pool", pool_module)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            live_execution._build_adapters(  # noqa: SLF001 - lifecycle seam
                LiveEnvironment("postgresql://127.0.0.1/test", None, True, False, ""),
                _live_probe_set(),
            )
        )

    assert events == ["open", "close"]


def test_execute_memory_shard_preserves_partial_transcript_in_a_caller_owned_sink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scratch_path = tmp_path / "memeval-partial-transcript.db"
    _transcript, _events = _configure_live_execution(monkeypatch, scratch_path=scratch_path)
    transcript: list[dict[str, object]] = []
    calls = 0

    async def fail_after_a_reply(*args: object) -> tuple[str, int]:
        del args
        nonlocal calls
        calls += 1
        if calls == 1:
            return "one", 1
        raise RuntimeError("ordinary failure")

    monkeypatch.setattr(live_execution, "ask_live", fail_after_a_reply)

    with pytest.raises(RuntimeError, match="ordinary failure"):
        asyncio.run(
            live_execution.execute_memory_shard(
                _live_probe_set(),
                LiveEnvironment(None, scratch_path, True, True, ""),
                object(),
                provider="provider",
                model="model",
                private_transcript_sink=transcript,
            )
        )

    assert transcript[0]["reply"] == "one"


def test_execute_memory_shard_preserves_a_reply_when_scoring_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scratch_path = tmp_path / "memeval-score-failure.db"
    _transcript, _events = _configure_live_execution(monkeypatch, scratch_path=scratch_path)
    transcript: list[dict[str, object]] = []

    def fail_scoring(*args: object) -> object:
        del args
        raise RuntimeError("scoring failed")

    monkeypatch.setattr(live_execution, "score", fail_scoring)

    with pytest.raises(RuntimeError, match="scoring failed"):
        asyncio.run(
            live_execution.execute_memory_shard(
                _live_probe_set(),
                LiveEnvironment(None, scratch_path, True, True, ""),
                object(),
                provider="provider",
                model="model",
                private_transcript_sink=transcript,
            )
        )

    assert transcript[0]["reply"] == "one"


def test_execute_memory_shard_surfaces_partial_gateway_teardown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scratch_path = tmp_path / "memeval-partial-teardown.db"
    _transcript, _events = _configure_live_execution(monkeypatch, scratch_path=scratch_path)

    class TwoGatewaySession(_Session):
        def __init__(self, **kwargs: object) -> None:
            super().__init__(**kwargs)
            self.gateways = [object(), object()]

    async def remove_one(gateways: list[object]) -> int:
        assert len(gateways) == 2
        return 1

    monkeypatch.setattr(live_execution, "LiveSession", TwoGatewaySession)
    monkeypatch.setattr(live_execution, "teardown", remove_one)

    result = asyncio.run(
        live_execution.execute_memory_shard(
            _live_probe_set(),
            LiveEnvironment(None, scratch_path, True, True, "", sqlite_path_owned=True),
            object(),
            provider="provider",
            model="model",
        )
    )

    assert result.provider_findings == ("cleanup: removed 1 of 2 memory stores",)
    assert result.scratch_removed is True


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


def test_build_memory_report_restores_original_probe_order_from_out_of_order_shards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe_set = _two_probe_set()
    rows = asyncio.run(run_probe_rows(probe_set, _scripted_rows))
    captured: list[tuple[ProbeRow, ...]] = []

    def build_report(*args: object, **kwargs: object) -> dict[str, object]:
        del kwargs
        captured.append(args[1])
        return {"report": "built"}

    monkeypatch.setattr(live_execution, "build_report", build_report)
    second = live_execution.MemoryShardResult((rows[1],), (), (), "nonce", (), True)
    first = live_execution.MemoryShardResult((rows[0],), (), (), "nonce", (), True)

    assert live_execution.build_memory_report(
        probe_set,
        (second, first),
        provider="provider",
        model="model",
        ran_at=datetime(2026, 8, 23),
    ) == {"report": "built"}
    assert [row.probe_id for row in captured[0]] == ["probe-1", "probe-2"]


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (("unknown",), "unknown probe"),
        (("probe-1", "probe-1"), "duplicate probe"),
        (("probe-1",), "missing probe"),
    ],
)
def test_build_memory_report_rejects_invalid_shard_probe_sets(
    rows: tuple[str, ...], message: str
) -> None:
    probe_set = _two_probe_set()
    valid_rows = asyncio.run(run_probe_rows(probe_set, _scripted_rows))
    by_id = {row.probe_id: row for row in valid_rows}
    shard = live_execution.MemoryShardResult(
        tuple(
            by_id[probe_id]
            if probe_id in by_id
            else ProbeRow(
                probe_id=probe_id,
                targets=MemoryType.SHORT_TERM,
                test=ProbeTest.RECALL,
                full=Outcome.PASS,
                ablated=Outcome.MISS,
                control=Outcome.MISS,
                certain=True,
                latency_ms=1,
            )
            for probe_id in rows
        ),
        (),
        (),
        "nonce",
        (),
        True,
    )

    with pytest.raises(ValueError, match=message):
        live_execution.build_memory_report(
            probe_set,
            (shard,),
            provider="provider",
            model="model",
            ran_at=datetime(2026, 8, 23),
        )


def test_build_memory_report_rejects_rows_without_all_three_arm_outcomes() -> None:
    probe_set = _live_probe_set()
    incomplete = ProbeRow(
        probe_id="probe-1",
        targets=MemoryType.SHORT_TERM,
        test=ProbeTest.RECALL,
        full=Outcome.PASS,
        ablated=Outcome.MISS,
        control=None,  # type: ignore[arg-type]
        certain=True,
        latency_ms=1,
    )
    shard = live_execution.MemoryShardResult((incomplete,), (), (), "nonce", (), True)

    with pytest.raises(ValueError, match="three arm outcomes"):
        live_execution.build_memory_report(
            probe_set,
            (shard,),
            provider="provider",
            model="model",
            ran_at=datetime(2026, 8, 23),
        )


def test_build_memory_report_requires_one_nonempty_shared_nonce() -> None:
    probe_set = _live_probe_set()
    row = asyncio.run(run_probe_rows(probe_set, _scripted_rows))[0]

    for nonces in (("",), ("first", "second")):
        shards = tuple(
            live_execution.MemoryShardResult((row,), (), (), nonce, (), True) for nonce in nonces
        )
        with pytest.raises(ValueError, match="nonce"):
            live_execution.build_memory_report(
                probe_set,
                shards,
                provider="provider",
                model="model",
                ran_at=datetime(2026, 8, 23),
            )


def test_build_memory_report_marks_an_aborted_partial_run_without_reclassifying_seed_failures(
) -> None:
    probe_set = _two_probe_set()
    row = asyncio.run(run_probe_rows(probe_set, _scripted_rows))[0]
    shard = live_execution.MemoryShardResult(
        (row,),
        ("semantic: seed failed",),
        ({"reply": "private partial reply"},),
        "nonce",
        ("aborted: provider unavailable",),
        True,
    )

    report = live_execution.build_memory_report(
        probe_set,
        (shard,),
        provider="provider",
        model="model",
        ran_at=datetime(2026, 8, 23),
    )

    assert report["aborted"] is True
    assert report["seed_failures"] == ["semantic: seed failed"]
    assert report["per_scope"]["long_term"]["probes"] == 0  # type: ignore[index]


async def _scripted_rows(
    probe: Probe, arm: Arm, masked: MemoryType | None
) -> tuple[str, int]:
    del masked
    return (probe.expect_any[0] if arm is Arm.FULL else "no match", 1)
