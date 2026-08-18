from __future__ import annotations

import asyncio

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval.arms import Arm
from cowork_agent.features.ai_chat.memory_eval.live_controller import AdapterSet
from cowork_agent.features.ai_chat.memory_eval.live_runner import (
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
    return ProbeSet("1.0.0", "unit", "unit", SeedSpec(("a",), {}, (), None), (_probe(),))


def test_identity_is_namespaced_by_the_run_key() -> None:
    identity = build_identity(_probe_set(), "model-a")
    assert identity.tenant_id == f"memeval-{identity.run_key}"
    assert identity.user_id == f"memeval-{identity.run_key}"


def test_identity_is_stable_for_the_same_inputs() -> None:
    assert build_identity(_probe_set(), "m").run_key == build_identity(_probe_set(), "m").run_key


def test_a_different_model_gets_a_different_tenant() -> None:
    first = build_identity(_probe_set(), "m1")
    second = build_identity(_probe_set(), "m2")
    assert first.tenant_id != second.tenant_id


def test_the_foreign_identity_differs_from_the_primary() -> None:
    # An isolation probe is meaningless if both identities collide.
    identity = build_identity(_probe_set(), "m")
    assert identity.foreign_tenant_id != identity.tenant_id
    assert identity.foreign_user_id != identity.user_id


def test_a_short_term_probe_keeps_its_session() -> None:
    assert needs_fresh_session(_probe(targets=MemoryType.SHORT_TERM)) is False


def test_every_other_scope_gets_a_fresh_session() -> None:
    for scope in (MemoryType.LONG_TERM, MemoryType.EPISODIC, MemoryType.SEMANTIC):
        assert needs_fresh_session(_probe(targets=scope)) is True


def test_a_short_term_probe_reuses_the_seeded_session_id_per_arm() -> None:
    identity = build_identity(_probe_set(), "m")
    probe = _probe(targets=MemoryType.SHORT_TERM, probe_id="st_1")
    assert session_id_for(identity, probe, Arm.FULL) == session_id_for(identity, probe, Arm.FULL)


def test_session_ids_differ_across_arms() -> None:
    # Sharing a session across arms would leak the full arm's turns into control.
    identity = build_identity(_probe_set(), "m")
    probe = _probe(probe_id="ep_1")
    assert session_id_for(identity, probe, Arm.FULL) != session_id_for(
        identity, probe, Arm.CONTROL
    )


def test_session_ids_differ_across_probes() -> None:
    identity = build_identity(_probe_set(), "m")
    first = session_id_for(identity, _probe(probe_id="a"), Arm.FULL)
    second = session_id_for(identity, _probe(probe_id="b"), Arm.FULL)
    assert first != second


class _Reply:
    def __init__(self) -> None:
        self.questions: list[str] = []

    async def stream_reply(self, request: object, context: object):  # noqa: ANN201 - structural
        del context
        self.questions.append(str(getattr(request, "user_message", "")))
        yield "an answer"


class _Gateway:
    def __init__(self, fail: bool = False) -> None:
        self.deleted = 0
        self._fail = fail

    async def delete_all_memory(self) -> object:
        if self._fail:
            raise RuntimeError("store gone")
        self.deleted += 1
        return object()


def _session(reply: object, seed: SeedSpec | None = None) -> LiveSession:
    return LiveSession(
        identity=build_identity(_probe_set(), "m"),
        adapters=AdapterSet(),
        reply=reply,
        seed=seed if seed is not None else SeedSpec((), {}, (), None),
    )


def test_ask_live_returns_text_and_latency() -> None:
    session = _session(_Reply())
    text, latency_ms = asyncio.run(ask_live(session, _probe(), Arm.FULL, None))
    assert text == "an answer"
    assert latency_ms >= 0


def test_ask_live_masks_the_named_scope_only_on_the_ablated_arm() -> None:
    session = _session(_Reply())
    asyncio.run(ask_live(session, _probe(), Arm.ABLATED, MemoryType.EPISODIC))
    assert session.last_gateway is not None
    assert session.last_gateway._masked_scope is MemoryType.EPISODIC


def test_ask_live_masks_nothing_on_the_control_arm() -> None:
    # control differs by having no seed, never by disabling a read.
    session = _session(_Reply())
    asyncio.run(ask_live(session, _probe(), Arm.CONTROL, None))
    assert session.last_gateway is not None
    assert session.last_gateway._masked_scope is None


def test_the_control_arm_is_never_seeded() -> None:
    # control differs by having no seed, never by disabling a read (SPEC 5.1).
    session = _session(_Reply(), SeedSpec(("a seeded line",), {}, (), None))
    asyncio.run(ask_live(session, _probe(), Arm.CONTROL, None))
    assert session.seeded == set()


def test_a_non_control_arm_seeds_exactly_once_per_session() -> None:
    session = _session(_Reply())
    probe = _probe()
    asyncio.run(ask_live(session, probe, Arm.FULL, None))
    asyncio.run(ask_live(session, probe, Arm.FULL, None))
    assert len(session.seeded) == 1


def test_a_non_short_term_probe_is_asked_in_a_session_that_was_never_seeded() -> None:
    # Seeding in the probing session would leave the answer in the recent-turn
    # window and the scope under test would never be read. SPEC 7 step 5.
    session = _session(_Reply(), SeedSpec(("a seeded line",), {}, (), None))
    asyncio.run(ask_live(session, _probe(targets=MemoryType.EPISODIC), Arm.FULL, None))
    assert session.last_gateway is not None
    turns = session.last_gateway._read_active_turns()
    assert not any("a seeded line" in (turn.user_message or "") for turn in turns)


def test_a_short_term_probe_is_asked_in_the_seeded_session() -> None:
    # The buffer lives on the gateway, so a short_term seed must run through the
    # same controller that answers the probe or the turns never reach it.
    session = _session(_Reply(), SeedSpec(("a seeded line",), {}, (), None))
    asyncio.run(ask_live(session, _probe(targets=MemoryType.SHORT_TERM), Arm.FULL, None))
    assert session.last_gateway is not None
    turns = session.last_gateway._read_active_turns()
    assert any("a seeded line" in (turn.user_message or "") for turn in turns)


def test_teardown_deletes_every_gateway_it_is_given() -> None:
    gateways = [_Gateway(), _Gateway()]
    assert asyncio.run(teardown(gateways)) == 2  # type: ignore[arg-type]
    assert all(gateway.deleted == 1 for gateway in gateways)


def test_teardown_keeps_going_when_one_store_is_already_gone() -> None:
    # A failed teardown must never mask the run's actual results.
    gateways = [_Gateway(fail=True), _Gateway()]
    assert asyncio.run(teardown(gateways)) == 1  # type: ignore[arg-type]
    assert gateways[1].deleted == 1
