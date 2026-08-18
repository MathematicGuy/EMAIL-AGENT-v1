"""The three controller-driven seeding rituals (SPEC §6).

`long_term` already ships in `seeding.py` — it needs only the gateway. These
three need the controller, because the authorization step under test lives
there: a turn must be spoken to enter the buffer, and a task must be requested
and approved to become retrievable.

Every ritual returns a SeedOutcome instead of raising. A scope that fails to
seed is a finding about that scope (SPEC §6.1), and the other three still run.
"""

from __future__ import annotations

from cowork_agent.domain.chat_contracts import ChatMessageRequest, MemoryType

from ..controller import ChatController
from .live_controller import ask_once, collect_reply
from .probes import SeedSpec
from .seeding import SeedOutcome


async def seed_short_term(
    controller: ChatController,
    session_id: str,
    spec: SeedSpec,
    *,
    key_prefix: str,
) -> SeedOutcome:
    """Speak each declared line as its own turn so the buffer fills naturally.

    Writing turns straight into the buffer would skip `stream_message`, which is
    what actually decides a turn is worth keeping. It would also make the probe
    pass on a buffer state no conversation can produce.
    """

    if not spec.short_term:
        return SeedOutcome(MemoryType.SHORT_TERM, True, "nothing declared")
    try:
        for index, line in enumerate(spec.short_term):
            await ask_once(controller, session_id, line, f"{key_prefix}-st-{index}")
    except Exception as error:  # noqa: BLE001 - a seed failure is a finding
        return SeedOutcome(MemoryType.SHORT_TERM, False, f"{type(error).__name__}: {error}")
    return SeedOutcome(MemoryType.SHORT_TERM, True, f"seeded {len(spec.short_term)} turns")


async def seed_episodic(
    controller: ChatController,
    session_id: str,
    spec: SeedSpec,
    *,
    key_prefix: str,
) -> SeedOutcome:
    """Request each task, then approve it if the seed says so.

    Two steps, because our system takes two. `stream_message` writes the episode
    SYSTEM_GENERATED with retrieval_eligible=false; only the approval makes it
    readable. Seeding just the first step and probing for recall would report
    amnesia that is actually the eligibility gate working correctly.

    A request the phrasing policy rejects produces no episode at all. That is a
    finding about `is_explicit_task_request`, reported as one.
    """

    if not spec.episodic:
        return SeedOutcome(MemoryType.EPISODIC, True, "nothing declared")

    approved = 0
    try:
        for index, entry in enumerate(spec.episodic):
            request = ChatMessageRequest(session_id, entry.request, f"{key_prefix}-ep-{index}")
            events = [event async for event in controller.stream_message(request)]
            _, episode_ids = collect_reply(events)
            if not episode_ids:
                return SeedOutcome(
                    MemoryType.EPISODIC,
                    False,
                    f"no task episode created for seed {index}; "
                    "is_explicit_task_request rejected the phrasing",
                )
            if entry.approve:
                for episode_id in episode_ids:
                    await controller.approve_task_episode(episode_id)
                    approved += 1
    except Exception as error:  # noqa: BLE001 - a seed failure is a finding
        return SeedOutcome(MemoryType.EPISODIC, False, f"{type(error).__name__}: {error}")
    return SeedOutcome(
        MemoryType.EPISODIC, True, f"seeded {len(spec.episodic)} episodes, approved {approved}"
    )
