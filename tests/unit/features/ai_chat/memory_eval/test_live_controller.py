from __future__ import annotations

import asyncio

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatMessageStreamEvent,
    MemoryCitationType,
    MemoryType,
)
from cowork_agent.features.ai_chat.memory_eval.live_controller import (
    AdapterSet,
    ask_once,
    build_arm_controller,
    collect_reply,
)


class _Reply:
    """Minimal ChatReplyPort stand-in that streams a fixed sentence."""

    def __init__(self, text: str = "the answer is Wednesday") -> None:
        self._text = text
        self.prompts: list[object] = []

    async def stream_reply(self, request: object, context: object):  # noqa: ANN201 - structural
        del context
        self.prompts.append(request)
        yield self._text


def _started() -> ChatMessageStreamEvent:
    return ChatMessageStreamEvent.started(event_id="e", session_id="s", turn_id="t")


def _delta(text: str) -> ChatMessageStreamEvent:
    return ChatMessageStreamEvent.delta(event_id="e", session_id="s", turn_id="t", text=text)


def _citation(memory_type: MemoryCitationType, source_id: str) -> ChatMessageStreamEvent:
    return ChatMessageStreamEvent.memory_citation(
        event_id="e",
        session_id="s",
        turn_id="t",
        memory_type=memory_type,
        source_id=source_id,
    )


def _completed() -> ChatMessageStreamEvent:
    return ChatMessageStreamEvent.completed(event_id="e", session_id="s", turn_id="t")


def test_collect_reply_concatenates_only_delta_text() -> None:
    text, episode_ids = collect_reply([_started(), _delta("Wed"), _delta("nesday"), _completed()])
    assert text == "Wednesday"
    assert episode_ids == ()


def test_collect_reply_captures_episodic_citation_source_ids() -> None:
    events = [_delta("ok"), _citation(MemoryCitationType.EPISODIC, "ep-1")]
    text, episode_ids = collect_reply(events)
    assert text == "ok"
    assert episode_ids == ("ep-1",)


def test_collect_reply_ignores_non_episodic_citations() -> None:
    assert collect_reply([_citation(MemoryCitationType.DECLARATIVE, "prof-1")])[1] == ()


def test_collect_reply_of_an_empty_stream_is_empty_not_a_crash() -> None:
    assert collect_reply([]) == ("", ())


def test_build_arm_controller_masks_the_named_scope() -> None:
    scope = ChatMemoryScope(tenant_id="t", user_id="u", session_id="s")
    _, gateway = build_arm_controller(
        scope, AdapterSet(), _Reply(), masked_scope=MemoryType.EPISODIC
    )
    assert gateway._masked_scope is MemoryType.EPISODIC


def test_build_arm_controller_masks_nothing_for_the_full_arm() -> None:
    scope = ChatMemoryScope(tenant_id="t", user_id="u", session_id="s")
    _, gateway = build_arm_controller(scope, AdapterSet(), _Reply(), masked_scope=None)
    assert gateway._masked_scope is None


def test_ask_once_returns_the_reply_text_and_a_latency() -> None:
    scope = ChatMemoryScope(tenant_id="t", user_id="u", session_id="s")
    controller, _ = build_arm_controller(scope, AdapterSet(), _Reply(), masked_scope=None)
    text, latency_ms = asyncio.run(ask_once(controller, "s", "which day?", "probe-1"))
    assert "Wednesday" in text
    assert latency_ms >= 0


def test_each_ask_uses_a_distinct_idempotency_key() -> None:
    # Reusing a key replays the cached turn instead of asking again, which would
    # make every arm after the first return the first arm's answer.
    scope = ChatMemoryScope(tenant_id="t", user_id="u", session_id="s")
    reply = _Reply()
    controller, _ = build_arm_controller(scope, AdapterSet(), reply, masked_scope=None)
    asyncio.run(ask_once(controller, "s", "q", "probe-1-full"))
    asyncio.run(ask_once(controller, "s", "q", "probe-1-control"))
    assert len(reply.prompts) == 2
