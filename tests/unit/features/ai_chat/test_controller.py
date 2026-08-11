import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from cowork_agent.domain.chat_contracts import (
    ChatEventType,
    ChatMemoryScope,
    ChatMessageRequest,
    ChatToolChoice,
    DeclarativeProfile,
    MemoryNamespace,
    MemoryType,
)
from cowork_agent.features.ai_chat.controller import (
    ChatController,
    ChatReplyUnavailable,
    ChatScopeMismatch,
    ChatSessionAccessDenied,
    InMemoryChatSessionRegistry,
)
from cowork_agent.features.ai_chat.memory_gateway import MemoryGateway
from cowork_agent.features.ai_chat.retrieval_policy import select_memory_reads
from cowork_agent.features.ai_chat.session_buffer import InMemoryChatSessionBuffer

NOW = datetime(2026, 8, 10, 12, tzinfo=UTC)


class ProfileReader:
    def __init__(self, profile: DeclarativeProfile | None) -> None:
        self.profile = profile
        self.reads: list[MemoryNamespace] = []

    async def read_profile(self, namespace: MemoryNamespace) -> DeclarativeProfile | None:
        self.reads.append(namespace)
        return self.profile

    async def write_profile(
        self, namespace: MemoryNamespace, profile: DeclarativeProfile
    ) -> DeclarativeProfile:
        del namespace
        self.profile = profile
        return profile

    async def delete_profile(self, namespace: MemoryNamespace) -> bool:
        del namespace
        existed = self.profile is not None
        self.profile = None
        return existed


class FakeReply:
    def __init__(self, chunks: tuple[str, ...] = ("Hello", " there")) -> None:
        self.chunks = chunks
        self.calls: list[tuple[ChatMessageRequest, object]] = []

    async def stream_reply(
        self, request: ChatMessageRequest, context: object
    ) -> AsyncIterator[str]:
        self.calls.append((request, context))
        for chunk in self.chunks:
            yield chunk


class BrokenReply:
    async def stream_reply(
        self, request: ChatMessageRequest, context: object
    ) -> AsyncIterator[str]:
        del request, context
        raise ChatReplyUnavailable("sensitive provider detail")
        yield  # pragma: no cover - keeps this method an async iterator


def _scope(*, session_id: str = "session-1") -> ChatMemoryScope:
    return ChatMemoryScope(
        tenant_id="tenant-1",
        user_id="user@example.com",
        session_id=session_id,
    )


def _profile() -> DeclarativeProfile:
    return DeclarativeProfile(
        profile_id="profile-1",
        tenant_id="tenant-1",
        user_id="user@example.com",
        language="en",
        timezone=None,
        assistant_persona="Concise",
        response_tone=None,
        created_at=NOW,
        updated_at=NOW,
    )


def _request(
    *,
    session_id: str = "session-1",
    idempotency_key: str = "idem-1",
    tool_choices: tuple[ChatToolChoice, ...] = (),
    user_message: str = "Help me plan today.",
) -> ChatMessageRequest:
    return ChatMessageRequest(
        session_id=session_id,
        user_message=user_message,
        tool_choices=tool_choices,
        idempotency_key=idempotency_key,
    )


def _controller(
    *,
    reply: FakeReply | BrokenReply,
    profile: ProfileReader | None,
) -> tuple[ChatController, InMemoryChatSessionBuffer]:
    ids = iter(f"id-{number}" for number in range(1, 30))
    buffer = InMemoryChatSessionBuffer(max_turns=4, ttl_seconds=60)
    gateway = MemoryGateway(
        scope=_scope(),
        session_buffer=buffer,
        declarative_memory=profile,
    )
    return (
        ChatController(
            scope=_scope(),
            memory=gateway,
            reply=reply,
            new_id=lambda: next(ids),
            clock=lambda: NOW,
        ),
        buffer,
    )


async def _collect(controller: ChatController, request: ChatMessageRequest):
    return [event async for event in controller.stream_message(request)]


def test_controller_streams_deltas_then_completed_and_records_one_complete_turn() -> None:
    reply = FakeReply()
    profile = ProfileReader(_profile())
    controller, buffer = _controller(reply=reply, profile=profile)

    events = asyncio.run(_collect(controller, _request()))
    context = reply.calls[0][1]

    assert [event.event_type for event in events] == [
        ChatEventType.DELTA,
        ChatEventType.DELTA,
        ChatEventType.COMPLETED,
    ]
    assert context.profile == _profile()
    assert context.episodes == ()
    assert context.semantic_context is None
    assert [turn.assistant_message for turn in context.turns] == []
    stored = buffer.read(
        MemoryNamespace(
            scope=_scope(),
            memory_type=MemoryType.SHORT_TERM,
            record_id="session-1",
            source_id=None,
        )
    )
    assert len(stored) == 1
    assert stored[0].assistant_message == "Hello there"
    assert len(profile.reads) == 1


@pytest.mark.parametrize(
    "user_message",
    [
        "Help me plan today.",
        "Find my previous task about payroll.",
        "What does the company policy say about travel?",
        "Compare my prior task with the company procedure.",
    ],
)
def test_controller_context_request_delegates_retrieval_selection_to_policy(
    user_message: str,
) -> None:
    controller, _ = _controller(reply=FakeReply(), profile=ProfileReader(_profile()))
    request = _request(user_message=user_message)

    context_request = controller._context_request(request)

    assert context_request.reads == select_memory_reads(request)


def test_controller_emits_a_safe_degraded_warning_and_continues_without_profile() -> None:
    reply = FakeReply(("Fallback response",))
    controller, _ = _controller(reply=reply, profile=None)

    events = asyncio.run(_collect(controller, _request()))

    assert [event.event_type for event in events] == [
        ChatEventType.ERROR,
        ChatEventType.DELTA,
        ChatEventType.COMPLETED,
    ]
    assert events[0].code == "optional_memory_degraded"
    assert events[0].safe_message == "Some optional memory was unavailable."
    assert reply.calls[0][1].profile is None


def test_controller_rejects_a_foreign_session_before_any_memory_or_reply_access() -> None:
    reply = FakeReply()
    profile = ProfileReader(_profile())
    controller, _ = _controller(reply=reply, profile=profile)

    with pytest.raises(ChatScopeMismatch):
        asyncio.run(_collect(controller, _request(session_id="session-2")))

    assert profile.reads == []
    assert reply.calls == []


def test_disconnect_after_a_delta_does_not_append_a_partial_turn() -> None:
    async def scenario() -> None:
        reply = FakeReply(("first", "second"))
        controller, buffer = _controller(reply=reply, profile=ProfileReader(_profile()))
        disconnected = False

        async def is_cancelled() -> bool:
            return disconnected

        stream = controller.stream_message(_request(), is_cancelled=is_cancelled)
        first = await anext(stream)
        assert first.event_type is ChatEventType.DELTA
        disconnected = True
        assert [event async for event in stream] == []
        assert buffer.read(
            MemoryNamespace(
                scope=_scope(),
                memory_type=MemoryType.SHORT_TERM,
                record_id="session-1",
                source_id=None,
            )
        ) == ()

    asyncio.run(scenario())


def test_reply_failure_emits_only_a_safe_error_and_does_not_append_the_turn() -> None:
    controller, buffer = _controller(
        reply=BrokenReply(), profile=ProfileReader(_profile())
    )

    events = asyncio.run(_collect(controller, _request()))

    assert [event.event_type for event in events] == [ChatEventType.ERROR]
    assert events[0].code == "chat_provider_unavailable"
    assert "sensitive" not in events[0].safe_message
    assert buffer.read(
        MemoryNamespace(
            scope=_scope(),
            memory_type=MemoryType.SHORT_TERM,
            record_id="session-1",
            source_id=None,
        )
    ) == ()


def test_email_tool_choice_is_explicitly_unavailable_in_v2_m4a() -> None:
    reply = FakeReply()
    profile = ProfileReader(_profile())
    controller, _ = _controller(reply=reply, profile=profile)

    events = asyncio.run(
        _collect(controller, _request(tool_choices=(ChatToolChoice.EMAIL,)))
    )

    assert [event.event_type for event in events] == [ChatEventType.ERROR]
    assert events[0].code == "tool_not_available"
    assert profile.reads == []
    assert reply.calls == []


def test_completed_idempotent_request_replays_events_without_a_second_turn() -> None:
    reply = FakeReply(("One response",))
    controller, buffer = _controller(reply=reply, profile=ProfileReader(_profile()))

    first = asyncio.run(_collect(controller, _request()))
    replay = asyncio.run(_collect(controller, _request()))

    assert replay == first
    assert len(reply.calls) == 1
    assert len(
        buffer.read(
            MemoryNamespace(
                scope=_scope(),
                memory_type=MemoryType.SHORT_TERM,
                record_id="session-1",
                source_id=None,
            )
        )
    ) == 1


def test_session_registry_binds_sessions_to_the_verified_principal() -> None:
    ids = iter(("session-1", "session-2"))
    registry = InMemoryChatSessionRegistry(new_id=lambda: next(ids))
    scope = registry.create(tenant_id="tenant-1", user_id="user@example.com")

    assert registry.require(
        scope.session_id, tenant_id="tenant-1", user_id="user@example.com"
    ) == scope
    with pytest.raises(ChatSessionAccessDenied):
        registry.require(
            scope.session_id, tenant_id="tenant-1", user_id="other@example.com"
        )
