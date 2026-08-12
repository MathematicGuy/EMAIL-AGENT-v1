import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from cowork_agent.domain.chat_contracts import (
    ChatEventType,
    ChatMemoryScope,
    ChatMessageRequest,
    DeclarativeProfile,
    MemoryNamespace,
    MemoryType,
    TaskEpisode,
)
from cowork_agent.domain.target_contracts import ValidationStatus
from cowork_agent.features.ai_chat.controller import (
    ChatController,
    ChatReplyUnavailable,
    ChatScopeMismatch,
    ChatSessionAccessDenied,
    InMemoryChatSessionRegistry,
)
from cowork_agent.features.ai_chat.generation_context import GenerationContext
from cowork_agent.features.ai_chat.memory_gateway import (
    MemoryGateway,
    MemorySourceUnavailableError,
)
from cowork_agent.features.ai_chat.ports import ChatReplyChunk, ChatTaskProposal
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
    def __init__(
        self, chunks: tuple[str | ChatReplyChunk, ...] = ("Hello", " there")
    ) -> None:
        self.chunks = chunks
        self.calls: list[tuple[ChatMessageRequest, GenerationContext]] = []

    async def stream_reply(
        self, request: ChatMessageRequest, context: GenerationContext
    ) -> AsyncIterator[str | ChatReplyChunk]:
        self.calls.append((request, context))
        for chunk in self.chunks:
            yield chunk


class BrokenReply:
    async def stream_reply(
        self, request: ChatMessageRequest, context: GenerationContext
    ) -> AsyncIterator[str]:
        del request, context
        raise ChatReplyUnavailable("sensitive provider detail")
        yield  # pragma: no cover - keeps this method an async iterator


class EpisodeWriter:
    def __init__(self) -> None:
        self.writes: list[TaskEpisode] = []

    async def write_task_episode(
        self, namespace: object, episode: TaskEpisode, *, expires_at: object
    ) -> TaskEpisode:
        del namespace, expires_at
        self.writes.append(episode)
        return episode


class RetryableEpisodeWriter(EpisodeWriter):
    def __init__(self) -> None:
        super().__init__()
        self.attempts: list[TaskEpisode] = []

    async def write_task_episode(
        self, namespace: object, episode: TaskEpisode, *, expires_at: object
    ) -> TaskEpisode:
        del namespace, expires_at
        self.attempts.append(episode)
        if len(self.attempts) == 1:
            raise MemorySourceUnavailableError("temporary database outage")
        self.writes.append(episode)
        return episode


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
    user_message: str = "Help me plan today.",
) -> ChatMessageRequest:
    return ChatMessageRequest(
        session_id=session_id,
        user_message=user_message,
        idempotency_key=idempotency_key,
    )


def _controller(
    *,
    reply: FakeReply | BrokenReply,
    profile: ProfileReader | None,
    episodes: EpisodeWriter | None = None,
) -> tuple[ChatController, InMemoryChatSessionBuffer]:
    ids = iter(f"id-{number}" for number in range(1, 30))
    buffer = InMemoryChatSessionBuffer(max_turns=4, ttl_seconds=60)
    gateway = MemoryGateway(
        scope=_scope(),
        session_buffer=buffer,
        declarative_memory=profile,
        episodic_memory=episodes,
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
    assert context.stored_preference is not None
    assert context.stored_preference.value == _profile()
    assert context.advisory_episodes is None
    assert context.current_company_evidence is None
    assert context.active_session_turns is None
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


def test_controller_persists_one_body_free_episode_only_for_an_explicit_task_request() -> None:
    proposal = ChatTaskProposal(
        task_title="Submit report",
        minimal_request_paraphrase="Prepare the report",
        action_plan=("Draft the report", "Send it for review"),
        rag_citations=(),
        missing_information=("Confirm the due date",),
        model_id="configured-model",
        prompt_version="chat-v2",
        confidence=0.8,
    )
    reply = FakeReply((ChatReplyChunk("Here is the task proposal.", proposal),))
    episodes = EpisodeWriter()
    controller, _ = _controller(reply=reply, profile=ProfileReader(_profile()), episodes=episodes)

    task_events = asyncio.run(
        _collect(controller, _request(user_message="Please create a task for this."))
    )
    ordinary_request = _request(
        idempotency_key="idem-2", user_message="Help me plan today."
    )
    asyncio.run(_collect(controller, ordinary_request))

    assert len(episodes.writes) == 1
    written = episodes.writes[0]
    assert written.validation_status is ValidationStatus.SYSTEM_GENERATED
    assert written.retrieval_eligible is False
    assert written.chat_session_id == "session-1"
    assert written.creation_reason == "explicit_user_task_request"
    assert written.source_type.value == "system_generated_chat_task"
    assert written.task_title == "Submit report"
    assert written.minimal_request_paraphrase == "Prepare the report"
    assert written.action_plan == ("Draft the report", "Send it for review")
    assert written.record_id
    assert "Please create" not in written.record_id
    assert [event.event_type for event in task_events] == [
        ChatEventType.DELTA,
        ChatEventType.MEMORY_CITATION,
        ChatEventType.TASK_PROPOSAL,
        ChatEventType.COMPLETED,
    ]
    assert task_events[1].source_id == written.episode_id
    assert task_events[2].proposal is not None
    assert task_events[2].proposal["episode_id"] == written.episode_id


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
    assert reply.calls[0][1].stored_preference is None


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


def test_transient_task_episode_failure_retries_the_same_pending_write_without_a_second_reply(
) -> None:
    proposal = ChatTaskProposal(
        task_title="Submit report",
        minimal_request_paraphrase="Prepare the report",
        action_plan=("Draft the report",),
        rag_citations=(),
        missing_information=(),
        model_id="configured-model",
        prompt_version="chat-v2",
        confidence=0.8,
    )
    reply = FakeReply((ChatReplyChunk("Here is the task proposal.", proposal),))
    episodes = RetryableEpisodeWriter()
    controller, buffer = _controller(
        reply=reply,
        profile=ProfileReader(_profile()),
        episodes=episodes,
    )
    request = _request(user_message="Please create a task for this.")

    first = asyncio.run(_collect(controller, request))
    retry = asyncio.run(_collect(controller, request))
    replay = asyncio.run(_collect(controller, request))

    assert [event.event_type for event in first] == [
        ChatEventType.DELTA,
        ChatEventType.ERROR,
        ChatEventType.COMPLETED,
    ]
    assert [event.event_type for event in retry] == [
        ChatEventType.DELTA,
        ChatEventType.MEMORY_CITATION,
        ChatEventType.TASK_PROPOSAL,
        ChatEventType.COMPLETED,
    ]
    assert replay == retry
    assert len(reply.calls) == 1
    assert len(episodes.attempts) == 2
    assert episodes.attempts[0] == episodes.attempts[1]
    assert episodes.attempts[0].episode_id == episodes.attempts[1].episode_id
    assert episodes.attempts[0].record_id == episodes.attempts[1].record_id
    assert episodes.attempts[0].chat_session_id == episodes.attempts[1].chat_session_id
    assert episodes.attempts[0].chat_turn_id == episodes.attempts[1].chat_turn_id
    assert episodes.attempts[0].action_plan == episodes.attempts[1].action_plan
    assert episodes.writes == [episodes.attempts[0]]
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

    async def scenario() -> None:
        scope = await registry.create(tenant_id="tenant-1", user_id="user@example.com")

        assert await registry.require(
            scope.session_id, tenant_id="tenant-1", user_id="user@example.com"
        ) == scope
        with pytest.raises(ChatSessionAccessDenied):
            await registry.require(
                scope.session_id, tenant_id="tenant-1", user_id="other@example.com"
            )

    asyncio.run(scenario())
