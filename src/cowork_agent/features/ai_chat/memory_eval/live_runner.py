"""Live-tier orchestration: identity, session policy, teardown (SPEC §7).

Every rule here exists because breaking it makes the harness measure something
other than memory, and each one names what it prevents.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from cowork_agent.domain.chat_contracts import ChatMemoryScope, MemoryType

from ..controller import ChatController
from .arms import Arm, ArmScopedMemoryGateway
from .live_controller import AdapterSet, ask_once, build_arm_controller
from .live_seeding import seed_episodic, seed_short_term, verify_seed
from .probes import Probe, ProbeSet, SeedSpec
from .runner import run_key
from .seeding import seed_long_term


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


@dataclass
class LiveSession:
    """Everything one live run needs to ask a question under any arm.

    Mutable and not slotted, unlike the rest of this package: `last_gateway`
    records the gateway built for the most recent ask so teardown and tests can
    reach it without threading a return value through `AskProbe`, whose shape is
    fixed by the offline runner.
    """

    identity: RunIdentity
    adapters: AdapterSet
    reply: object
    seed: SeedSpec = field(default_factory=lambda: SeedSpec((), {}, (), None))
    company_rag_enabled: bool = True
    last_gateway: ArmScopedMemoryGateway | None = None
    gateways: list[ArmScopedMemoryGateway] = field(default_factory=list)
    seeded: set[str] = field(default_factory=set)
    seed_failures: list[str] = field(default_factory=list)


async def _seed_for(
    session: LiveSession,
    probe: Probe,
    scope: ChatMemoryScope,
    probe_controller: ChatController,
    probe_gateway: ArmScopedMemoryGateway,
) -> None:
    """Seed one arm's store before the probe is asked.

    Where each ritual runs matters, and for two different reasons.

    `short_term` runs through the PROBE controller. The buffer lives on the
    gateway instance, so seeding through a second controller would fill a
    different buffer and the turns would never reach the probe.

    `episodic` runs through a SEPARATE controller, because requesting a task is
    itself a chat turn. Running it in the probing session would leave the task
    text in the recent-turn window and an episodic probe could answer from the
    prompt without episodic memory being read at all (SPEC §7 step 5). Episodes
    are keyed by tenant and user, not by session, so a foreign seeding session
    is still readable — verified against read_episodes' WHERE clause.

    `long_term` needs only a gateway; the profile is per-user.
    """

    outcomes = [
        await seed_long_term(
            probe_gateway,
            scope,
            session.seed,
            now=datetime.now(UTC),
            profile_id=session.identity.run_key,
        )
    ]

    if needs_fresh_session(probe):
        seed_session_id = f"{scope.session_id}-seed"
        seed_scope = ChatMemoryScope(
            tenant_id=scope.tenant_id, user_id=scope.user_id, session_id=seed_session_id
        )
        seed_controller, seed_gateway = build_arm_controller(
            seed_scope,
            session.adapters,
            session.reply,
            masked_scope=None,
            company_rag_enabled=session.company_rag_enabled,
        )
        session.gateways.append(seed_gateway)
        outcomes.append(
            await seed_episodic(
                seed_controller, seed_session_id, session.seed, key_prefix=seed_session_id
            )
        )
    else:
        outcomes.append(
            await seed_short_term(
                probe_controller, scope.session_id, session.seed, key_prefix=scope.session_id
            )
        )
        outcomes.append(
            await seed_episodic(
                probe_controller, scope.session_id, session.seed, key_prefix=scope.session_id
            )
        )

    session.seed_failures.extend(
        f"{outcome.scope.value}: {outcome.reason}" for outcome in outcomes if not outcome.ok
    )
    # A scope that declared nothing was never seeded, so verifying it would
    # report a failure for memory nobody asked for.
    landed = tuple(
        outcome.scope
        for outcome in outcomes
        if outcome.ok and outcome.reason != "nothing declared"
    )
    session.seed_failures.extend(
        finding.reason for finding in await verify_seed(probe_gateway, scope, landed)
    )


async def ask_live(
    session: LiveSession,
    probe: Probe,
    arm: Arm,
    masked: MemoryType | None,
) -> tuple[str, int]:
    """The live `AskProbe`: seed the arm if needed, then ask through it.

    `masked` is supplied by the offline runner and is already `None` for
    everything except the ablated arm. It is passed through rather than
    recomputed so the two runners can never disagree about what an arm means.

    The control arm is never seeded. That is the whole of what makes it a
    control: it differs by having no seed, never by disabling a read (SPEC
    §5.1). Seeding it would turn it into a fourth ablation arm and destroy the
    leak signal.
    """

    scope = ChatMemoryScope(
        tenant_id=session.identity.tenant_id,
        user_id=session.identity.user_id,
        session_id=session_id_for(session.identity, probe, arm),
    )
    controller, gateway = build_arm_controller(
        scope,
        session.adapters,
        session.reply,
        masked_scope=masked,
        company_rag_enabled=session.company_rag_enabled,
    )
    session.last_gateway = gateway
    session.gateways.append(gateway)

    if arm is not Arm.CONTROL and scope.session_id not in session.seeded:
        session.seeded.add(scope.session_id)
        await _seed_for(session, probe, scope, controller, gateway)

    return await ask_once(
        controller, scope.session_id, probe.question, f"{probe.probe_id}-{arm.value}"
    )


async def teardown(gateways: Sequence[ArmScopedMemoryGateway]) -> int:
    """Delete every store this run created. Returns how many succeeded.

    The gateway method is `delete_all_memory`, not the `delete_all_for_user`
    named in SPEC §7 step 12 — that is the episodic port's method, called
    internally. Company RAG is never touched, by design.

    One failure never stops the rest: a run that produced real findings must
    still report them even if cleanup is partial.
    """

    deleted = 0
    for gateway in gateways:
        try:
            await gateway.delete_all_memory()
        except Exception:  # noqa: BLE001 - cleanup must not mask results
            continue
        deleted += 1
    return deleted
