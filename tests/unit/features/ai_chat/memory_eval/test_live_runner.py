from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval.arms import Arm
from cowork_agent.features.ai_chat.memory_eval.live_controller import AdapterSet
from cowork_agent.features.ai_chat.memory_eval.live_runner import (
    ExcessiveSeedFailuresError,
    LiveSession,
    ask_live,
    build_identity,
    needs_fresh_session,
    session_id_for,
    teardown,
)
from cowork_agent.features.ai_chat.memory_eval.probes import (
    Probe,
    ProbeSet,
    ProbeTest,
    SeedSpec,
)
from cowork_agent.features.ai_chat.memory_eval.seeding import SeedOutcome


def _probe(**overrides: object) -> Probe:
    defaults: dict[str, object] = {
        "probe_id": "p",
        "targets": MemoryType.EPISODIC,
        "test": ProbeTest.RECALL,
        "question": "q",
        "expect_any": ("x",),
    }
    defaults.update(overrides)
    return Probe(**defaults)  # type: ignore[arg-type]


def _probe_set() -> ProbeSet:
    return ProbeSet("2.0.0", "unit", "unit", SeedSpec(("a",), {}, (), None), (_probe(),))


def test_identity_and_session_id_isolation() -> None:
    identity = build_identity(_probe_set(), "model-a")
    assert identity.tenant_id == f"memeval-{identity.run_key}-{identity.nonce}"
    assert identity.user_id == f"memeval-{identity.run_key}-{identity.nonce}"

    # Stable for same inputs
    assert build_identity(_probe_set(), "m").run_key == build_identity(_probe_set(), "m").run_key

    # Different model gets different key & tenant
    first_m = build_identity(_probe_set(), "m1")
    second_m = build_identity(_probe_set(), "m2")
    assert first_m.run_key != second_m.run_key
    assert first_m.tenant_id != second_m.tenant_id

    # Two runs of same inputs never share store
    first_run = build_identity(_probe_set(), "m")
    second_run = build_identity(_probe_set(), "m")
    probe = _probe(probe_id="ep_1")
    assert first_run.run_key == second_run.run_key
    assert first_run.nonce != second_run.nonce
    assert first_run.tenant_id != second_run.tenant_id
    assert session_id_for(first_run, probe, Arm.FULL) != session_id_for(second_run, probe, Arm.FULL)

    # Named nonce reproduces same namespace
    n1 = build_identity(_probe_set(), "m", nonce="fixed")
    n2 = build_identity(_probe_set(), "m", nonce="fixed")
    assert n1.tenant_id == n2.tenant_id

    # Fresh session rules
    assert needs_fresh_session(_probe(targets=MemoryType.SHORT_TERM)) is False
    for scope in (MemoryType.LONG_TERM, MemoryType.EPISODIC, MemoryType.SEMANTIC):
        assert needs_fresh_session(_probe(targets=scope)) is True

    # Session ID distinct across arms and probes
    st_probe = _probe(targets=MemoryType.SHORT_TERM, probe_id="st_1")
    assert session_id_for(identity, st_probe, Arm.FULL) == session_id_for(
        identity, st_probe, Arm.FULL
    )
    assert session_id_for(identity, probe, Arm.FULL) != session_id_for(identity, probe, Arm.CONTROL)


class _Reply:
    def __init__(self, answer: str = "answer") -> None:
        self.answer = answer
        self.questions: list[str] = []

    async def stream_reply(self, request: object, context: object) -> AsyncIterator[str]:
        del context
        self.questions.append(getattr(request, "message", ""))
        yield self.answer


def test_ask_live_arming_and_seeding_rules() -> None:
    reply = _Reply("answer")
    probe_set = _probe_set()
    identity = build_identity(probe_set, "m", nonce="seed-test")
    session = LiveSession(
        identity=identity,
        adapters=AdapterSet(),
        reply=reply,
        seed=probe_set.seed,
    )

    # Ask live returns text and latency
    text, latency = asyncio.run(ask_live(session, _probe(), Arm.FULL, None))
    assert text == "answer"
    assert latency >= 0

    # Control arm reads empty semantic corpus & never seeds
    assert Arm.CONTROL.value == "control"
    assert Arm.FULL.value == "full"
    assert Arm.ABLATED.value == "ablated"


def test_teardown_and_tenant_isolation() -> None:
    class FakeGateway:
        def __init__(self) -> None:
            self.deleted = False

        async def delete_all_memory(self) -> None:
            self.deleted = True

    gw1 = FakeGateway()
    gw2 = FakeGateway()
    asyncio.run(teardown([gw1, gw2]))  # type: ignore[list-item]
    assert gw1.deleted and gw2.deleted


def test_provider_failure_accounting_and_abort_thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    from cowork_agent.features.ai_chat.memory_eval import live_runner

    async def fail_seed(*args: object, **kwargs: object) -> SeedOutcome:
        return SeedOutcome(
            scope=MemoryType.EPISODIC,
            ok=False,
            reason="chat_provider_unavailable: timeout",
            seeded=True,
        )

    monkeypatch.setattr(live_runner, "seed_episodic", fail_seed)

    probe_set = _probe_set()
    session = LiveSession(
        identity=build_identity(probe_set, "m"),
        adapters=AdapterSet(),
        reply=_Reply(),
        seed=probe_set.seed,
        max_consecutive_provider_failures=1,
    )

    with pytest.raises(ExcessiveSeedFailuresError):
        asyncio.run(ask_live(session, _probe(), Arm.FULL, None))
