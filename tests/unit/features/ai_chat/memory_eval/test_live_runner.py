from __future__ import annotations

import asyncio

import pytest

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval.arms import Arm
from cowork_agent.features.ai_chat.memory_eval.live_controller import AdapterSet
from cowork_agent.features.ai_chat.memory_eval.live_runner import (
    ExcessiveSeedFailuresError,
    LiveSession,
    ask_live,
    build_identity,
    identity_for,
    needs_fresh_session,
    session_id_for,
    teardown,
)
from cowork_agent.features.ai_chat.memory_eval.probes import (
    EpisodeSeed,
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


def test_identity_is_namespaced_by_the_run_key_and_the_nonce() -> None:
    identity = build_identity(_probe_set(), "model-a")
    assert identity.tenant_id == f"memeval-{identity.run_key}-{identity.nonce}"
    assert identity.user_id == f"memeval-{identity.run_key}-{identity.nonce}"


def test_identity_is_stable_for_the_same_inputs() -> None:
    assert build_identity(_probe_set(), "m").run_key == build_identity(_probe_set(), "m").run_key


def test_a_different_model_gets_a_different_run_key_and_tenant() -> None:
    first = build_identity(_probe_set(), "m1")
    second = build_identity(_probe_set(), "m2")
    assert first.run_key != second.run_key
    assert first.tenant_id != second.tenant_id


def test_two_runs_of_the_same_inputs_never_share_a_store() -> None:
    # Two runs started at once derived identical tenants, users and session ids,
    # because every one of them came from `run_key` and nothing else. They wrote
    # into each other's stores, and whichever finished first deleted the other's
    # in teardown. The report still keys on `run_key`, so the nonce namespaces
    # the store without changing what the report calls the run.
    first = build_identity(_probe_set(), "m")
    second = build_identity(_probe_set(), "m")
    probe = _probe(probe_id="ep_1")
    assert first.run_key == second.run_key
    assert first.nonce != second.nonce
    assert first.tenant_id != second.tenant_id
    assert first.user_id != second.user_id
    assert session_id_for(first, probe, Arm.FULL) != session_id_for(second, probe, Arm.FULL)


def test_arm_identities_of_two_runs_never_collide_either() -> None:
    first = identity_for(build_identity(_probe_set(), "m"), _probe(probe_id="ep_1"), Arm.FULL)
    second = identity_for(build_identity(_probe_set(), "m"), _probe(probe_id="ep_1"), Arm.FULL)
    assert first.tenant_id != second.tenant_id
    assert first.user_id != second.user_id


def test_a_named_nonce_reproduces_the_same_namespace() -> None:
    # A caller that needs to address a run it created earlier can name the
    # nonce; only the default is fresh per process.
    first = build_identity(_probe_set(), "m", nonce="fixed")
    second = build_identity(_probe_set(), "m", nonce="fixed")
    assert first.tenant_id == second.tenant_id
    assert first.user_id == second.user_id


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
    assert session_id_for(identity, probe, Arm.FULL) != session_id_for(identity, probe, Arm.CONTROL)


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


def test_a_long_term_probe_still_calls_seed_episodic_in_a_foreign_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions: list[str] = []

    async def fake_episodic(controller, session_id, spec, *, key_prefix):
        del controller, spec, key_prefix
        sessions.append(session_id)
        return SeedOutcome(MemoryType.EPISODIC, True, "ok")

    monkeypatch.setattr(
        "cowork_agent.features.ai_chat.memory_eval.live_runner.seed_episodic",
        fake_episodic,
    )
    session = _session(
        _Reply(),
        SeedSpec((), {"language": "vi"}, (EpisodeSeed("Tạo một tác vụ.", True),), None),
    )
    asyncio.run(ask_live(session, _probe(targets=MemoryType.LONG_TERM), Arm.FULL, None))
    assert sessions
    assert all(item.endswith("-seed") for item in sessions)


def test_control_still_never_calls_seed_episodic(monkeypatch: pytest.MonkeyPatch) -> None:
    called = []

    async def fake_episodic(controller, session_id, spec, *, key_prefix):
        called.append(session_id)
        return SeedOutcome(MemoryType.EPISODIC, True, "ok")

    monkeypatch.setattr(
        "cowork_agent.features.ai_chat.memory_eval.live_runner.seed_episodic",
        fake_episodic,
    )
    session = _session(_Reply(), SeedSpec((), {}, (EpisodeSeed("Tạo một tác vụ.", True),), None))
    asyncio.run(ask_live(session, _probe(targets=MemoryType.LONG_TERM), Arm.CONTROL, None))
    assert called == []


def test_short_term_probe_buffer_does_not_contain_episodic_seed_text() -> None:
    seed = SeedSpec(
        ("a seeded line",),
        {},
        (EpisodeSeed("Tạo một tác vụ gia hạn CCCD cho văn phòng Đà Nẵng.", True),),
        None,
    )
    session = _session(_Reply(), seed)
    asyncio.run(ask_live(session, _probe(targets=MemoryType.SHORT_TERM), Arm.FULL, None))
    assert session.last_gateway is not None
    turns = session.last_gateway._read_active_turns()
    assert any("a seeded line" in (turn.user_message or "") for turn in turns)
    assert not any("Tạo một tác vụ" in (turn.user_message or "") for turn in turns)


def test_teardown_deletes_every_gateway_it_is_given() -> None:
    gateways = [_Gateway(), _Gateway()]
    assert asyncio.run(teardown(gateways)) == 2  # type: ignore[arg-type]
    assert all(gateway.deleted == 1 for gateway in gateways)


def test_teardown_keeps_going_when_one_store_is_already_gone() -> None:
    # A failed teardown must never mask the run's actual results.
    gateways = [_Gateway(fail=True), _Gateway()]
    assert asyncio.run(teardown(gateways)) == 1  # type: ignore[arg-type]
    assert gateways[1].deleted == 1


def test_each_arm_gets_its_own_tenant_and_user() -> None:
    # long_term is keyed by tenant and user, and episodic is read across
    # sessions on purpose. A run-wide tenant let the control arm read back what
    # the full arm had just seeded, which turns the leak signal upside down.
    identity = build_identity(_probe_set(), "m")
    probe = _probe(probe_id="lt_1", targets=MemoryType.LONG_TERM)
    full = identity_for(identity, probe, Arm.FULL)
    control = identity_for(identity, probe, Arm.CONTROL)
    assert full.tenant_id != control.tenant_id
    assert full.user_id != control.user_id
    assert full.run_key == control.run_key == identity.run_key


def test_probes_do_not_share_a_tenant_either() -> None:
    identity = build_identity(_probe_set(), "m")
    first = identity_for(identity, _probe(probe_id="a"), Arm.FULL)
    second = identity_for(identity, _probe(probe_id="b"), Arm.FULL)
    assert first.tenant_id != second.tenant_id


def test_the_control_arm_reads_an_empty_semantic_corpus() -> None:
    # The corpus index is built once per run and has no tenant partition, so
    # per-arm identities cannot empty it. Control must be handed a store that
    # answers with nothing — not no store at all, which would be a disabled
    # read wearing the control arm's name.
    session = _session(_Reply())
    session.adapters = AdapterSet(None, None, object())
    control = session.adapters_for(Arm.CONTROL)
    assert control.semantic_memory is not session.adapters.semantic_memory
    assert control.semantic_memory is not None
    assert session.adapters_for(Arm.FULL) is session.adapters


def test_an_arm_with_no_semantic_adapter_is_left_alone() -> None:
    session = _session(_Reply())
    assert session.adapters_for(Arm.CONTROL) is session.adapters


def test_leftover_seed_failure_strings_do_not_abort() -> None:
    session = _session(_Reply())
    session.seed_failures = ["err1", "err2", "err3", "err4"]
    text, _ = asyncio.run(ask_live(session, _probe(targets=MemoryType.EPISODIC), Arm.FULL, None))
    assert text == "an answer"


def test_control_does_not_reset_consecutive_provider_failures() -> None:
    session = _session(_Reply())
    session.consecutive_provider_failures = 2
    asyncio.run(ask_live(session, _probe(), Arm.CONTROL, None))
    assert session.consecutive_provider_failures == 2


def test_consecutive_provider_seed_failures_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail_ep(controller, session_id, spec, *, key_prefix):
        del controller, session_id, spec, key_prefix
        return SeedOutcome(
            MemoryType.EPISODIC,
            False,
            "no task episode was created for seed 0 (chat_provider_unavailable: down)",
        )

    monkeypatch.setattr(
        "cowork_agent.features.ai_chat.memory_eval.live_runner.seed_episodic",
        fail_ep,
    )
    session = _session(_Reply(), SeedSpec((), {}, (EpisodeSeed("Tạo một tác vụ.", True),), None))
    session.max_consecutive_provider_failures = 3
    probe = _probe(targets=MemoryType.EPISODIC)
    with pytest.raises(ExcessiveSeedFailuresError):
        for _ in range(3):
            session.seeded.clear()
            asyncio.run(ask_live(session, probe, Arm.FULL, None))


def test_isolated_success_between_provider_failures_does_not_abort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def maybe_fail_ep(controller, session_id, spec, *, key_prefix):
        del controller, session_id, key_prefix
        if spec.episodic:
            return SeedOutcome(
                MemoryType.EPISODIC,
                False,
                "no task episode was created for seed 0 (chat_provider_unavailable: down)",
            )
        return SeedOutcome(MemoryType.EPISODIC, True, "ok", seeded=False)

    monkeypatch.setattr(
        "cowork_agent.features.ai_chat.memory_eval.live_runner.seed_episodic",
        maybe_fail_ep,
    )
    fail_seed = SeedSpec((), {}, (EpisodeSeed("Tạo một tác vụ.", True),), None)
    session = _session(_Reply(), fail_seed)
    session.max_consecutive_provider_failures = 3
    probe = _probe(targets=MemoryType.EPISODIC)
    for _ in range(2):
        session.seeded.clear()
        text, _ = asyncio.run(ask_live(session, probe, Arm.FULL, None))
        assert text == ""
    session.seed = SeedSpec((), {}, (), None)
    session.seeded.clear()
    text, _ = asyncio.run(ask_live(session, probe, Arm.FULL, None))
    assert text == "an answer"
    session.seed = fail_seed
    for _ in range(2):
        session.seeded.clear()
        text, _ = asyncio.run(ask_live(session, probe, Arm.FULL, None))
        assert text == ""
    assert session.consecutive_provider_failures == 2


def test_provider_class_seed_failure_skips_ask_under_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_ep(controller, session_id, spec, *, key_prefix):
        del controller, session_id, spec, key_prefix
        return SeedOutcome(
            MemoryType.EPISODIC,
            False,
            "no task episode was created for seed 0 (chat_provider_unavailable: down)",
        )

    monkeypatch.setattr(
        "cowork_agent.features.ai_chat.memory_eval.live_runner.seed_episodic",
        fail_ep,
    )
    reply = _Reply()
    session = _session(reply, SeedSpec((), {}, (EpisodeSeed("Tạo một tác vụ.", True),), None))
    session.max_consecutive_provider_failures = 3
    text, latency_ms = asyncio.run(
        ask_live(session, _probe(targets=MemoryType.EPISODIC), Arm.FULL, None)
    )
    assert text == ""
    assert latency_ms == 0
    assert reply.questions == []
    assert session.consecutive_provider_failures == 1
    assert session.ask_errors
    assert any("chat_provider_unavailable" in str(item["errors"]) for item in session.ask_errors)


def test_verify_seed_findings_do_not_increment_consecutive_provider_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_verify(gateway, scope, landed):
        del gateway, scope, landed
        return (type("Finding", (), {"reason": "the verification read came back empty"})(),)

    monkeypatch.setattr(
        "cowork_agent.features.ai_chat.memory_eval.live_runner.verify_seed",
        fake_verify,
    )
    session = _session(_Reply())
    asyncio.run(ask_live(session, _probe(targets=MemoryType.EPISODIC), Arm.FULL, None))
    assert any("verification read came back empty" in line for line in session.seed_failures)
    assert session.consecutive_provider_failures == 0


def test_consecutive_ask_once_provider_failures_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail_ask(controller, session_id, question, idempotency_key):
        del controller, session_id, question, idempotency_key
        return "", 0, ("chat_provider_unavailable: down",)

    monkeypatch.setattr(
        "cowork_agent.features.ai_chat.memory_eval.live_runner.ask_once",
        fail_ask,
    )
    session = _session(_Reply())
    session.max_consecutive_provider_failures = 3
    with pytest.raises(ExcessiveSeedFailuresError):
        for index in range(3):
            asyncio.run(ask_live(session, _probe(probe_id=f"p{index}"), Arm.FULL, None))

