"""Live-tier orchestration: identity, session policy, teardown (SPEC §7).

Every rule here exists because breaking it makes the harness measure something
other than memory, and each one names what it prevents.
"""

from __future__ import annotations

from dataclasses import dataclass

from cowork_agent.domain.chat_contracts import MemoryType

from .arms import Arm
from .probes import Probe, ProbeSet
from .runner import run_key


@dataclass(frozen=True, slots=True)
class RunIdentity:
    """Throwaway identities for one run, plus the foreign one isolation uses."""

    run_key: str
    tenant_id: str
    user_id: str
    foreign_tenant_id: str
    foreign_user_id: str


def build_identity(probe_set: ProbeSet, model: str) -> RunIdentity:
    """Derive namespaced identities from the probe set, model and seed.

    Two properties matter. A run can never collide with another run or touch a
    real user's memory. And because the seed is part of the key, changing the
    seed addresses a different tenant — so a run can never quietly probe a store
    that was seeded for a different question.
    """

    key = run_key(probe_set.probe_set_id, model, probe_set.seed)
    return RunIdentity(
        run_key=key,
        tenant_id=f"memeval-{key}",
        user_id=f"memeval-{key}",
        foreign_tenant_id=f"memeval-foreign-{key}",
        foreign_user_id=f"memeval-foreign-{key}",
    )


def needs_fresh_session(probe: Probe) -> bool:
    """Whether this probe must be asked in a session that has no seeded turns.

    The buffer feeds recent turns into the prompt. For any scope other than
    short_term, probing in the seeded session puts the answer in the context
    window and the scope under test is never consulted — the probe would pass
    while proving nothing. For a short_term probe the buffer IS the subject, so
    the session is deliberately kept.
    """

    return probe.targets is not MemoryType.SHORT_TERM


def session_id_for(identity: RunIdentity, probe: Probe, arm: Arm) -> str:
    """A session id unique per (run, probe, arm).

    Arms never share a session. Sharing one would carry the full arm's turns
    into the control arm, and control would stop being a clean-store baseline.
    """

    return f"memeval-{identity.run_key}-{probe.probe_id}-{arm.value}"
