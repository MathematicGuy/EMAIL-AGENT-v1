"""Unit tests for the parallel memory-eval runner's empty-reply classifier."""

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
    probe_set = ProbeSet(
        "2.0.0", "unit", "unit", SeedSpec(("a",), {}, (), None), (_probe(),)
    )
    return LiveSession(
        identity=build_identity(probe_set, "m", nonce="abcd1234"),
        adapters=AdapterSet(),
        reply=object(),
        seed=probe_set.seed,
    )


async def _run_with_errors(
    monkeypatch: pytest.MonkeyPatch, error_lines: tuple[str, ...]
) -> list[FailedCall]:
    from scripts import evaluate_memory_parallel as parallel

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


@pytest.mark.parametrize(
    ("error_lines", "expected"),
    [
        (("chat_response_invalid: citation_ids must match",), "contract"),
        (("chat_provider_unavailable: gateway timeout",), "transient"),
        (("empty_chat_response: Câu trả lời trả về rỗng.",), "transient"),
        (("exception: ConnectionResetError",), "transient"),
        ((), "transient"),
        (
            (
                "chat_provider_unavailable: timeout",
                "chat_response_invalid: citation_ids must match",
            ),
            "contract",
        ),
    ],
)
def test_classify_empty_failure(error_lines: tuple[str, ...], expected: str) -> None:
    assert classify_empty_failure(error_lines) == expected


def test_contract_invalid_empty_is_not_queued_for_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed = asyncio.run(
        _run_with_errors(monkeypatch, ("chat_response_invalid: citation_ids must match",))
    )
    assert failed == []


def test_provider_unavailable_empty_is_queued_for_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed = asyncio.run(
        _run_with_errors(monkeypatch, ("chat_provider_unavailable: gateway timeout",))
    )
    assert len(failed) == 3
    assert all(item.error_reason == "chat_provider_unavailable" for item in failed)


def test_uncoded_empty_is_queued_for_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    failed = asyncio.run(_run_with_errors(monkeypatch, ()))
    assert len(failed) == 3
    assert all(item.error_reason == "empty_reply" for item in failed)


def test_unrecovered_manifest_records_that_recovery_ran() -> None:
    call = FailedCall(
        probe=_probe(),
        arm=Arm.FULL,
        masked=None,
        error_reason="chat_provider_unavailable",
    )
    assert unrecovered_manifest_entry(call) == {
        "probe": "st_recall_01",
        "arm": "full",
        "error": "chat_provider_unavailable",
        "retried": True,
    }
