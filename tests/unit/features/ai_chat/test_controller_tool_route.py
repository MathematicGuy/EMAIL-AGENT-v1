import asyncio
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime

from cowork_agent.domain.chat_contracts import (
    ChatEventType,
    ChatIntent,
    ChatMemoryScope,
    ChatMessageRequest,
    ChatMessageStreamEvent,
    ChatRoute,
    IntentDecision,
    IntentReasonCode,
    RoutingOutcome,
)
from cowork_agent.features.ai_chat.controller import ChatController
from cowork_agent.features.ai_chat.generation_context import GenerationContext
from cowork_agent.features.ai_chat.memory_gateway import MemoryGateway
from cowork_agent.features.ai_chat.session_buffer import InMemoryChatSessionBuffer
from cowork_agent.features.ai_chat.tools import (
    CALENDAR_TOOL_NAME,
    InMemoryCalendar,
    Tool,
    build_calendar_tool,
)
from cowork_agent.features.ai_chat.tools.runner import ChatToolRunner, ToolTurnContext

NOW = datetime(2026, 8, 25, 10, 0, tzinfo=UTC)
ARGUMENTS: Mapping[str, object] = {
    "title": "Họp team",
    "start": "2026-08-26T15:00:00+00:00",
    "end": "2026-08-26T15:30:00+00:00",
}


class Routing:
    def __init__(self, outcome: RoutingOutcome) -> None:
        self.outcome = outcome

    async def route(self, **kwargs: object) -> RoutingOutcome:
        del kwargs
        return self.outcome


class Reply:
    def __init__(self) -> None:
        self.contexts: list[GenerationContext] = []

    async def stream_reply(
        self, request: ChatMessageRequest, context: GenerationContext
    ) -> AsyncIterator[str]:
        del request
        self.contexts.append(context)
        yield "done"


def _outcome(tool_name: str) -> RoutingOutcome:
    decision = IntentDecision(
        ChatIntent.ACTION_REQUEST,
        False,
        True,
        tool_name,
        False,
        None,
        0.9,
        (IntentReasonCode.EXTERNAL_ACTION_REQUESTED,),
    )
    return RoutingOutcome(
        decision,
        ChatRoute.TOOL,
        False,
        True,
        False,
        None,
        decision.reason_codes,
        False,
        False,
        "chat-intent-v4",
    )


def _runner(
    calendar: InMemoryCalendar, payload: object = ARGUMENTS
) -> tuple[ChatToolRunner, list[str]]:
    prompts: list[str] = []

    async def complete(prompt: str, schema: Mapping[str, object]) -> Mapping[str, object]:
        del schema
        prompts.append(prompt)
        if isinstance(payload, Exception):
            raise payload
        return payload  # type: ignore[return-value]

    async def bind(context: ToolTurnContext) -> Tool:
        return build_calendar_tool(
            calendar, idempotency_key=context.idempotency_key, timezone="UTC", now=context.now
        )

    runner = ChatToolRunner({CALENDAR_TOOL_NAME: bind}, complete=complete)
    return runner, prompts


def _controller(runner: ChatToolRunner | None, tool_name: str = CALENDAR_TOOL_NAME):
    scope = ChatMemoryScope(user_id="user-1", session_id="session-1")
    reply = Reply()
    controller = ChatController(
        scope=scope,
        memory=MemoryGateway(
            scope=scope, session_buffer=InMemoryChatSessionBuffer(max_turns=8, ttl_seconds=60)
        ),
        reply=reply,
        routing=Routing(_outcome(tool_name)),  # type: ignore[arg-type]
        new_id=iter(f"id-{index}" for index in range(50)).__next__,
        clock=lambda: NOW,
        tools=runner,
    )
    return controller, reply


def _run(controller: ChatController) -> list[ChatMessageStreamEvent]:
    async def collect() -> list[ChatMessageStreamEvent]:
        request = ChatMessageRequest("session-1", "Tạo todo họp team ngày mai", "idem-1")
        return [event async for event in controller.stream_message(request)]

    return asyncio.run(collect())


def test_the_tool_route_runs_the_tool_and_hands_the_result_to_the_reply() -> None:
    calendar = InMemoryCalendar()
    runner, prompts = _runner(calendar)
    controller, reply = _controller(runner)

    events = _run(controller)

    assert len(calendar.events) == 1
    assert len(prompts) == 1
    context = reply.contexts[0]
    assert context.tool_result is not None
    assert "Họp team" in context.tool_result
    assert events[-1].event_type is ChatEventType.COMPLETED


def test_a_tool_failure_degrades_the_turn_without_failing_it() -> None:
    runner, _ = _runner(InMemoryCalendar(fail_with="403 accessNotConfigured"))
    controller, reply = _controller(runner)

    events = _run(controller)

    context = reply.contexts[0]
    assert context.tool_result is not None
    assert "403 accessNotConfigured" in context.tool_result
    assert events[-1].event_type is ChatEventType.COMPLETED


def test_arguments_that_cannot_be_determined_are_reported_not_guessed() -> None:
    calendar = InMemoryCalendar()
    runner, _ = _runner(calendar, {"error": "no date was given"})
    controller, reply = _controller(runner)

    _run(controller)

    assert calendar.events == {}
    assert reply.contexts[0].tool_result == "no date was given"


def test_a_tool_name_the_runner_does_not_have_is_reported() -> None:
    """The router narrows unknown names first, so reaching here means they disagree."""

    runner, _ = _runner(InMemoryCalendar())
    controller, reply = _controller(runner, tool_name="send_email")

    _run(controller)

    assert reply.contexts[0].tool_result == "No tool named 'send_email' is available."


def test_without_a_runner_the_turn_is_an_ordinary_reply() -> None:
    controller, reply = _controller(None)

    events = _run(controller)

    assert reply.contexts[0].tool_result is None
    assert events[-1].event_type is ChatEventType.COMPLETED


def test_a_retried_turn_does_not_create_a_second_event() -> None:
    calendar = InMemoryCalendar()
    runner, _ = _runner(calendar)
    controller, _ = _controller(runner)

    _run(controller)
    _run(controller)

    assert len(calendar.events) == 1
