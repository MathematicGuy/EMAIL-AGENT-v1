from __future__ import annotations

import asyncio

import pytest

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval.arms import Arm
from cowork_agent.features.ai_chat.memory_eval.live_controller import AdapterSet
from cowork_agent.features.ai_chat.memory_eval.live_runner import LiveSession, build_identity
from cowork_agent.features.ai_chat.memory_eval.probes import Probe, ProbeSet, ProbeTest, SeedSpec
from scripts.evaluate_memory_parallel import (
    FailedCall,
    classify_empty_failure,
    run_probe_task,
    unrecovered_manifest_entry,
)


def _probe() -> Probe:
    return Probe(
        probe_id="st_recall_01",
        targets=MemoryType.SHORT_TERM,
        test=ProbeTest.RECALL,
        question="what did I say?",
        expect_any=("a turn",),
    )


def _session() -> LiveSession:
    probe_set = ProbeSet("2.0.0", "unit", "unit", SeedSpec(("a",), {}, (), None), (_probe(),))
    return LiveSession(
        identity=build_identity(probe_set, "m", nonce="abcd1234"),
        adapters=AdapterSet(),
        reply=object(),
        seed=probe_set.seed,
    )


def test_classify_empty_failure_matrix() -> None:
    assert classify_empty_failure(("chat_response_invalid: citation_ids must match",)) == "contract"
    assert classify_empty_failure(("chat_provider_unavailable: gateway timeout",)) == "transient"
    assert classify_empty_failure(("empty_chat_response: Câu trả lời trả về rỗng.",)) == "transient"
    assert classify_empty_failure(("exception: ConnectionResetError",)) == "transient"
    assert classify_empty_failure(()) == "transient"
    assert (
        classify_empty_failure(
            (
                "chat_provider_unavailable: timeout",
                "chat_response_invalid: citation_ids must match",
            )
        )
        == "contract"
    )


def test_run_probe_task_recovery_queuing(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import evaluate_memory_parallel as parallel

    async def _run(error_lines: tuple[str, ...]) -> list[FailedCall]:
        async def fake_ask(
            session: LiveSession, probe: Probe, arm: Arm, masked: object
        ) -> tuple[str, int]:
            del masked
            session.ask_errors.append(
                {"probe": probe.probe_id, "arm": arm.value, "errors": list(error_lines)}
            )
            return "", 1

        monkeypatch.setattr(parallel, "ask_live", fake_ask)
        failed: list[FailedCall] = []
        await run_probe_task(
            probe=_probe(),
            session=_session(),
            semaphore=asyncio.Semaphore(1),
            progress={"completed": 0},
            total_calls=3,
            recorded=[],
            failed_calls=failed,
            lock=asyncio.Lock(),
        )
        return failed

    # Contract invalid empty -> not queued
    assert asyncio.run(_run(("chat_response_invalid: citation_ids must match",))) == []

    # Transient empty -> queued for recovery
    transient_failed = asyncio.run(_run(("chat_provider_unavailable: gateway timeout",)))
    assert len(transient_failed) == 3


def test_unrecovered_manifest_entry_formatting() -> None:
    call = FailedCall(
        probe=_probe(), arm=Arm.FULL, masked=None, error_reason="transient", attempt=2
    )
    entry = unrecovered_manifest_entry(call)
    assert entry == {
        "probe": "st_recall_01",
        "arm": "full",
        "error": "transient",
        "retried": True,
    }
