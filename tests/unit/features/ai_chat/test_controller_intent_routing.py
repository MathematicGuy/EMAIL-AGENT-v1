import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from cowork_agent.domain.chat_contracts import (
    ChatEventType,
    ChatIntent,
    ChatMemoryScope,
    ChatMessageRequest,
    ChatRoute,
    IntentDecision,
    IntentReasonCode,
    RoutingOutcome,
)
from cowork_agent.features.ai_chat.controller import ChatController
from cowork_agent.features.ai_chat.generation_context import (
    ChatResponseMode,
    GenerationContext,
)
from cowork_agent.features.ai_chat.memory_gateway import MemoryGateway
from cowork_agent.features.ai_chat.session_buffer import InMemoryChatSessionBuffer


class Routing:
    def __init__(self, outcome: RoutingOutcome) -> None:
        self.outcome = outcome
        self.calls = 0

    async def route(self, **kwargs: object) -> RoutingOutcome:
        del kwargs
        self.calls += 1
        return self.outcome


class Reply:
    def __init__(self, chunks: tuple[str, ...]) -> None:
        self.chunks = chunks
        self.contexts: list[GenerationContext] = []

    async def stream_reply(
        self, request: ChatMessageRequest, context: GenerationContext
    ) -> AsyncIterator[str]:
        del request
        self.contexts.append(context)
        for chunk in self.chunks:
            yield chunk


def _outcome(route: ChatRoute) -> RoutingOutcome:
    clarify = route is ChatRoute.CLARIFY
    decision = IntentDecision(
        ChatIntent.CHAT,
        False,
        False,
        None,
        clarify,
        None,
        0.8,
        (IntentReasonCode.MISSING_INFORMATION if clarify else IntentReasonCode.GENERAL_CHAT,),
    )
    return RoutingOutcome(
        decision,
        route,
        False,
        False,
        clarify,
        None,
        decision.reason_codes,
        False,
        False,
        "v1",
    )


def _controller(route: ChatRoute, chunks: tuple[str, ...] = ("Could you clarify?",)):
    scope = ChatMemoryScope("tenant-1", "user-1", "session-1")
    buffer = InMemoryChatSessionBuffer(max_turns=8, ttl_seconds=60)
    reply = Reply(chunks)
    routing = Routing(_outcome(route))
    controller = ChatController(
        scope=scope,
        memory=MemoryGateway(scope=scope, session_buffer=buffer),
        reply=reply,
        routing=routing,  # type: ignore[arg-type]
        new_id=iter(f"id-{index}" for index in range(20)).__next__,
        clock=lambda: datetime(2026, 8, 12, tzinfo=UTC),
    )
    return controller, routing, reply


def _request() -> ChatMessageRequest:
    return ChatMessageRequest("session-1", "Do it", "idem-1")


def test_clarify_uses_non_retrieval_reply_mode_and_no_task_proposal() -> None:
    controller, routing, reply = _controller(ChatRoute.CLARIFY)

    events = asyncio.run(_collect(controller))

    assert routing.calls == 1
    assert reply.contexts[0].response_mode is ChatResponseMode.CLARIFY
    assert [event.event_type for event in events] == [
        ChatEventType.ERROR,
        ChatEventType.DELTA,
        ChatEventType.COMPLETED,
    ]
    assert all(event.event_type is not ChatEventType.TASK_PROPOSAL for event in events)


def test_cancelled_retry_reuses_the_same_routing_outcome() -> None:
    controller, routing, _ = _controller(ChatRoute.CHAT, ("first", "second"))

    async def scenario() -> None:
        cancelled = False

        async def is_cancelled() -> bool:
            return cancelled

        stream = controller.stream_message(_request(), is_cancelled=is_cancelled)
        first = await anext(stream)
        assert first.event_type is ChatEventType.ERROR  # missing optional profile
        second = await anext(stream)
        assert second.event_type is ChatEventType.DELTA
        cancelled = True
        assert [event async for event in stream] == []
        await _collect(controller)

    asyncio.run(scenario())
    assert routing.calls == 1


async def _collect(controller: ChatController):
    return [event async for event in controller.stream_message(_request())]
