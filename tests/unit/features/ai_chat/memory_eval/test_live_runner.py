from __future__ import annotations

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval.arms import Arm
from cowork_agent.features.ai_chat.memory_eval.live_runner import (
    build_identity,
    needs_fresh_session,
    session_id_for,
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
