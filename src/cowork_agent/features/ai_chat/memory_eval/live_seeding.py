"""The three controller-driven seeding rituals (SPEC §6).

`long_term` already ships in `seeding.py` — it needs only the gateway. These
three need the controller, because the authorization step under test lives
there: a turn must be spoken to enter the buffer, and a task must be requested
and approved to become retrievable.

Every ritual returns a SeedOutcome instead of raising. A scope that fails to
seed is a finding about that scope (SPEC §6.1), and the other three still run.
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from pathlib import Path

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatMessageRequest,
    EpisodicMemoryQuery,
    MemoryContextRequest,
    MemoryReadOptions,
    MemoryType,
    SemanticMemoryQuery,
)
from cowork_agent.integrations.rag.chat_memory import SemanticChatMemoryAdapter
from cowork_agent.integrations.rag.knowledge_base import load_corpus
from cowork_agent.integrations.rag.memory import InRepoSemanticMemory

from ..controller import ChatController
from ..memory_gateway import MemoryGateway
from .live_controller import ask_once, collect_reply
from .live_env import ScopeAvailability
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


async def seed_semantic(
    spec: SeedSpec,
    embedder: object,
    *,
    corpus_root: Path,
) -> tuple[SeedOutcome, object | None]:
    """Index the declared corpus and return a read adapter for it.

    Semantic memory has no write path through the gateway — it is retrieval-only
    over a corpus someone else publishes. "Seeding" it therefore means building
    the index the read will hit, which is why this returns an adapter instead of
    mutating a store.

    The probe questions must carry a cue phrase such as "company policy" or the
    retrieval policy never fires and the probe measures nothing (SPEC §6).
    """

    if not spec.semantic_corpus_dir:
        return SeedOutcome(MemoryType.SEMANTIC, True, "nothing declared"), None
    try:
        documents = load_corpus(corpus_root / spec.semantic_corpus_dir)
        with warnings.catch_warnings():
            # InRepoSemanticMemory is deprecated for production and retained
            # explicitly for offline evaluation harnesses. This is one.
            warnings.simplefilter("ignore", DeprecationWarning)
            index = InRepoSemanticMemory(documents, embedder)  # type: ignore[arg-type]
        await index.build_index()
    except Exception as error:  # noqa: BLE001 - a seed failure is a finding
        return (
            SeedOutcome(MemoryType.SEMANTIC, False, f"{type(error).__name__}: {error}"),
            None,
        )
    return (
        SeedOutcome(MemoryType.SEMANTIC, True, f"indexed {len(documents)} documents"),
        SemanticChatMemoryAdapter(index),
    )


def _verification_reads() -> MemoryReadOptions:
    """Read every scope, masked by nothing. Verification must see the truth."""

    return MemoryReadOptions(
        short_term=True,
        long_term=True,
        episodic=EpisodicMemoryQuery(
            query="previous task", max_items=5, min_score=0.0, timeout_ms=2000
        ),
        semantic=SemanticMemoryQuery(
            query="company policy", max_items=5, min_score=0.0, timeout_ms=2000
        ),
    )


async def verify_seed(
    gateway: MemoryGateway,
    scope: ChatMemoryScope,
    expected_scopes: Sequence[MemoryType],
) -> tuple[ScopeAvailability, ...]:
    """Confirm each seeded scope actually reads back non-empty.

    Called on the `full` and `<target>_off` arms only. The `control` arm has no
    seed by definition, so verifying it would fail every scope every run.

    Our writes are transactional, so this is a single check rather than a poll.
    """

    if not expected_scopes:
        return ()
    request = MemoryContextRequest(
        session_id=scope.session_id, scope=scope, reads=_verification_reads()
    )
    try:
        response = await gateway.read_context(request)
    except Exception as error:  # noqa: BLE001 - an unreadable store is a finding
        return tuple(
            ScopeAvailability(item, False, f"verification read failed: {error}")
            for item in expected_scopes
        )

    populated = {
        MemoryType.SHORT_TERM: bool(response.turns),
        MemoryType.LONG_TERM: response.profile is not None,
        MemoryType.EPISODIC: bool(response.episodes),
        MemoryType.SEMANTIC: response.semantic_context is not None,
    }
    return tuple(
        ScopeAvailability(item, False, f"{item.value} seed did not land: read back empty")
        for item in expected_scopes
        if not populated[item]
    )
