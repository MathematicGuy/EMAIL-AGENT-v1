"""Driving the real ChatController under one arm (SPEC §7 step 6).

PLAN.md Task 8 defined `AskProbe` and left the live implementation out. This is
it. Nothing here judges anything — it asks a question and returns text, and the
already-tested scoring layer decides what the text means.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from time import monotonic

from cowork_agent.domain.chat_contracts import (
    ChatEventType,
    ChatMemoryScope,
    ChatMessageRequest,
    ChatMessageStreamEvent,
    MemoryCitationType,
    MemoryType,
)

from ..controller import ChatController
from ..session_buffer import InMemoryChatSessionBuffer
from .arms import ArmScopedMemoryGateway

#: Matches the production chat session buffer defaults.
_BUFFER_MAX_TURNS = 20
_BUFFER_TTL_SECONDS = 1800


@dataclass(frozen=True, slots=True)
class AdapterSet:
    """The memory adapters a run has managed to build.

    Every field is optional because each one depends on an external service that
    may be absent. A gateway built without an adapter fails closed for that
    scope, which is exactly what an unavailable scope should look like.
    """

    declarative_memory: object | None = None
    episodic_memory: object | None = None
    semantic_memory: object | None = None


def collect_reply(
    events: Iterable[ChatMessageStreamEvent],
) -> tuple[str, tuple[str, ...]]:
    """Reduce one event stream to its reply text and any episode ids it cited.

    Both are read in a single pass because the stream is consumed once. The
    episode ids are how episodic seeding learns what to approve (SPEC §6).
    """

    chunks: list[str] = []
    episode_ids: list[str] = []
    for event in events:
        if event.event_type is ChatEventType.DELTA and event.text:
            chunks.append(event.text)
        elif (
            event.event_type is ChatEventType.MEMORY_CITATION
            and event.memory_type is MemoryCitationType.EPISODIC
            and event.source_id
        ):
            episode_ids.append(event.source_id)
    return "".join(chunks), tuple(episode_ids)


def build_arm_controller(
    scope: ChatMemoryScope,
    adapters: AdapterSet,
    reply: object,
    *,
    masked_scope: MemoryType | None,
    company_rag_enabled: bool = True,
) -> tuple[ChatController, ArmScopedMemoryGateway]:
    """Build a controller whose gateway reports `masked_scope` as unavailable.

    The gateway is returned alongside the controller because seeding, seed
    verification and teardown all address it directly.
    """

    gateway = ArmScopedMemoryGateway(
        masked_scope=masked_scope,
        scope=scope,
        session_buffer=InMemoryChatSessionBuffer(
            max_turns=_BUFFER_MAX_TURNS, ttl_seconds=_BUFFER_TTL_SECONDS
        ),
        declarative_memory=adapters.declarative_memory,
        episodic_memory=adapters.episodic_memory,
        semantic_memory=adapters.semantic_memory,
    )
    controller = ChatController(
        scope=scope,
        memory=gateway,
        reply=reply,  # type: ignore[arg-type]
        company_rag_enabled=company_rag_enabled,
    )
    return controller, gateway


async def ask_once(
    controller: ChatController,
    session_id: str,
    question: str,
    idempotency_key: str,
) -> tuple[str, int]:
    """Ask one question and return (reply text, latency in ms).

    `idempotency_key` must be unique per (probe, arm). The controller caches a
    completed turn by that key and replays it verbatim, so a shared key would
    hand every later arm the first arm's answer and silently produce a run in
    which ablation never changed anything.
    """

    request = ChatMessageRequest(session_id, question, idempotency_key)
    started = monotonic()
    events = [event async for event in controller.stream_message(request)]
    latency_ms = int((monotonic() - started) * 1000)
    text, _ = collect_reply(events)
    return text, latency_ms


def episode_ids_from(events: Sequence[ChatMessageStreamEvent]) -> tuple[str, ...]:
    """The episodic citation ids in `events`. Used by episodic seeding."""

    return collect_reply(events)[1]
