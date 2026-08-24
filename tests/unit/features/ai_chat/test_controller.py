import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from cowork_agent.domain.chat_contracts import (
    ChatActivityCode,
    ChatActivityOutcome,
    ChatActivityStatus,
    ChatEventType,
    ChatMemoryScope,
    ChatMessageRequest,
    ChatMessageStreamEvent,
    ChatTurn,
    DeclarativeProfile,
    MemoryNamespace,
    MemoryType,
    TaskEpisode,
)
from cowork_agent.domain.project_documents import ProjectDocumentEvidence, ProjectDocumentResponse
from cowork_agent.domain.target_contracts import ValidationStatus
from cowork_agent.features.ai_chat.controller import (
    ChatController,
    ChatReplyUnavailable,
    ChatResponseInvalid,
    ChatScopeMismatch,
    ChatSessionAccessDenied,
    InMemoryChatSessionRegistry,
    _rag_evidence,
)
from cowork_agent.features.ai_chat.generation_context import (
    ContextSource,
    GenerationContext,
    LabeledSection,
)
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


class ActivityInspectingProfile(ProfileReader):
    def __init__(self, history: "HistoryWriter") -> None:
        super().__init__(_profile())
        self.history = history
        self.activity_status_during_read: ChatActivityStatus | None = None

    async def read_profile(self, namespace: MemoryNamespace) -> DeclarativeProfile | None:
        latest = self.history.updates[-1][1]
        self.activity_status_during_read = next(
            item.status
            for item in latest.activities
            if item.code is ChatActivityCode.REVIEWING_CONTEXT
        )
        return await super().read_profile(namespace)


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


class InvalidResponseReply:
    async def stream_reply(
        self, request: ChatMessageRequest, context: GenerationContext
    ) -> AsyncIterator[str]:
        del request, context
        raise ChatResponseInvalid("chat response failed validation")
        yield  # pragma: no cover - keeps this method an async iterator


class EpisodeWriter:
    def __init__(self) -> None:
        self.writes: list[TaskEpisode] = []

    async def read_episodes(self, namespace: object, query: object) -> tuple[TaskEpisode, ...]:
        # A task-creation turn now reads episodic memory so a revision can name
        # the episode it replaces; this double has nothing stored to return.
        del namespace, query
        return ()

    async def write_task_episode(
        self, namespace: object, episode: TaskEpisode, *, expires_at: object
    ) -> TaskEpisode:
        del namespace, expires_at
        self.writes.append(episode)
        return episode

    async def transition_task_episode(self, transition: object) -> TaskEpisode | None:
        from dataclasses import replace
        to_status = getattr(transition, "to_status", ValidationStatus.USER_APPROVED)
        eligible = to_status in (ValidationStatus.USER_APPROVED, ValidationStatus.COMPLETED)
        for idx, item in enumerate(self.writes):
            if item.episode_id == getattr(transition, "source_id", None) or len(self.writes) == 1:
                updated = replace(
                    item,
                    validation_status=to_status,
                    retrieval_eligible=eligible,
                )
                self.writes[idx] = updated
                return updated
        return None


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


class SemanticReader:
    def __init__(self, context: dict[str, object]) -> None:
        self.context = context

    async def read_semantic_context(
        self, namespace: MemoryNamespace, query: object
    ) -> dict[str, object]:
        del namespace, query
        return self.context


class HistoryWriter:
    def __init__(self) -> None:
        self.writes: list[tuple[ChatMemoryScope, ChatTurn, str]] = []
        self.begins: list[tuple[ChatMemoryScope, ChatTurn, str, str]] = []
        self.updates: list[tuple[ChatMemoryScope, ChatTurn, str | None]] = []

    async def write_turn(self, scope: ChatMemoryScope, turn: ChatTurn, *, title: str) -> None:
        self.writes.append((scope, turn, title))

    async def begin_turn(
        self,
        scope: ChatMemoryScope,
        turn: ChatTurn,
        *,
        idempotency_key: str,
        title: str,
    ) -> ChatTurn:
        self.begins.append((scope, turn, idempotency_key, title))
        return turn

    async def update_turn(
        self,
        scope: ChatMemoryScope,
        turn: ChatTurn,
        *,
        title: str | None = None,
    ) -> ChatTurn:
        self.updates.append((scope, turn, title))
        return turn


class CompletedHistory(HistoryWriter):
    async def begin_turn(
        self,
        scope: ChatMemoryScope,
        turn: ChatTurn,
        *,
        idempotency_key: str,
        title: str,
    ) -> ChatTurn:
        await super().begin_turn(
            scope, turn, idempotency_key=idempotency_key, title=title
        )
        return replace(
            turn,
            assistant_message="Previously completed answer",
            status="completed",
        )


class FailingUpdateHistory(HistoryWriter):
    async def update_turn(
        self,
        scope: ChatMemoryScope,
        turn: ChatTurn,
        *,
        title: str | None = None,
    ) -> ChatTurn:
        self.updates.append((scope, turn, title))
        raise RuntimeError("database unavailable")


def _scope(*, session_id: str = "session-1") -> ChatMemoryScope:
    return ChatMemoryScope(
        user_id="user@example.com",
        session_id=session_id,
    )


def _profile() -> DeclarativeProfile:
    return DeclarativeProfile(
        profile_id="profile-1",
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
    semantic: SemanticReader | None = None,
    history: HistoryWriter | None = None,
) -> tuple[ChatController, InMemoryChatSessionBuffer]:
    ids = iter(f"id-{number}" for number in range(1, 30))
    buffer = InMemoryChatSessionBuffer(max_turns=4, ttl_seconds=60)
    gateway = MemoryGateway(
        scope=_scope(),
        session_buffer=buffer,
        declarative_memory=profile,
        episodic_memory=episodes,
        semantic_memory=semantic,
    )
    return (
        ChatController(
            scope=_scope(),
            memory=gateway,
            reply=reply,
            new_id=lambda: next(ids),
            clock=lambda: NOW,
            history=history,
        ),
        buffer,
    )


async def _collect(controller: ChatController, request: ChatMessageRequest):
    return [event async for event in controller.stream_message(request)]


def _without_activity(
    events: list[ChatMessageStreamEvent],
) -> list[ChatMessageStreamEvent]:
    return [
        event
        for event in events
        if getattr(event, "event_type", None) is not ChatEventType.ACTIVITY
    ]


def test_controller_streams_deltas_then_completed_and_records_one_complete_turn() -> None:
    reply = FakeReply()
    profile = ProfileReader(_profile())
    controller, buffer = _controller(reply=reply, profile=profile)

    events = asyncio.run(_collect(controller, _request()))
    context = reply.calls[0][1]

    assert [event.event_type for event in _without_activity(events)] == [
        ChatEventType.STARTED,
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


def test_controller_emits_user_centric_dynamic_activity_snapshots() -> None:
    history = HistoryWriter()
    controller, _ = _controller(
        reply=FakeReply(("Answer",)), profile=ProfileReader(_profile()), history=history
    )

    events = asyncio.run(_collect(controller, _request()))
    snapshots = [event.activities for event in events if event.event_type is ChatEventType.ACTIVITY]

    assert [activity.code for activity in snapshots[-1]] == [
        ChatActivityCode.UNDERSTANDING_REQUEST,
        ChatActivityCode.REVIEWING_CONTEXT,
        ChatActivityCode.PREPARING_RESPONSE,
    ]
    assert all(
        activity.status is ChatActivityStatus.COMPLETED for activity in snapshots[-1]
    )
    assert history.updates[-1][1].activities == snapshots[-1]
    assert history.updates[-1][1].completed_at == NOW


def test_controller_marks_context_review_running_before_memory_read() -> None:
    history = HistoryWriter()
    profile = ActivityInspectingProfile(history)
    controller, _ = _controller(
        reply=FakeReply(("Answer",)), profile=profile, history=history
    )

    asyncio.run(_collect(controller, _request()))

    assert profile.activity_status_during_read is ChatActivityStatus.RUNNING


def test_controller_reports_retrieval_result_as_a_safe_aggregate() -> None:
    semantic = SemanticReader(
        {
            "source_label": "current_company_evidence",
            "retrieval_status": "no_results",
            "chunks": (),
            "citations": (),
            "scores": (),
        }
    )
    controller, _ = _controller(
        reply=FakeReply(("Answer",)),
        profile=ProfileReader(_profile()),
        semantic=semantic,
    )

    events = asyncio.run(
        _collect(controller, _request(user_message="What does company policy say?"))
    )
    final_snapshot = next(
        event.activities
        for event in reversed(events)
        if event.event_type is ChatEventType.ACTIVITY
    )
    search = next(
        item
        for item in final_snapshot
        if item.code is ChatActivityCode.SEARCHING_RELEVANT_INFORMATION
    )

    assert search.outcome is ChatActivityOutcome.NO_RESULTS
    assert search.detail is not None
    assert search.detail.to_dict() == {
        "kind": "documents_found",
        "current": 0,
        "total": None,
    }
    assert "provider" not in str(search.to_dict()).lower()


def test_controller_persists_partial_activity_history_when_generation_fails() -> None:
    history = HistoryWriter()
    controller, _ = _controller(
        reply=BrokenReply(), profile=ProfileReader(_profile()), history=history
    )

    events = asyncio.run(_collect(controller, _request()))
    terminal = next(
        event.activities
        for event in reversed(events)
        if event.event_type is ChatEventType.ACTIVITY
    )

    assert [item.status for item in terminal] == [
        ChatActivityStatus.COMPLETED,
        ChatActivityStatus.COMPLETED,
        ChatActivityStatus.FAILED,
    ]
    assert history.updates[-1][1].status.value == "failed"
    assert history.updates[-1][1].completed_at == NOW


def test_controller_persists_completed_turn_with_the_llm_generated_conversation_title() -> None:
    history = HistoryWriter()
    controller, _ = _controller(
        reply=FakeReply((ChatReplyChunk("Reply", conversation_title="Quarterly report plan"),)),
        profile=None,
        history=history,
    )

    asyncio.run(_collect(controller, _request()))

    assert len(history.begins) == 1
    begin_scope, pending, idempotency_key, temporary_title = history.begins[0]
    assert begin_scope == _scope()
    assert pending.assistant_message is None
    assert pending.status.value == "generating"
    assert idempotency_key == "idem-1"
    assert temporary_title == "Help me plan today."
    scope, turn, title = history.updates[-1]
    assert scope == _scope()
    assert turn.assistant_message == "Reply"
    assert turn.turn_id == pending.turn_id
    assert turn.status.value == "completed"
    assert title == "Quarterly report plan"


def test_controller_emits_started_after_the_user_turn_is_durable() -> None:
    history = HistoryWriter()
    controller, _ = _controller(
        reply=FakeReply(("Reply",)),
        profile=None,
        history=history,
    )

    events = asyncio.run(_collect(controller, _request()))

    assert history.begins
    assert events[0].event_type is ChatEventType.STARTED
    assert events[0].turn_id == history.begins[0][1].turn_id


def test_controller_marks_the_durable_turn_failed_when_the_provider_is_unavailable() -> None:
    history = HistoryWriter()
    controller, _ = _controller(
        reply=BrokenReply(),
        profile=ProfileReader(_profile()),
        history=history,
    )

    events = asyncio.run(_collect(controller, _request()))

    assert [event.event_type for event in _without_activity(events)] == [
        ChatEventType.STARTED,
        ChatEventType.ERROR,
    ]
    failed = history.updates[-1][1]
    assert failed.turn_id == history.begins[0][1].turn_id
    assert failed.user_message == "Help me plan today."
    assert failed.assistant_message is None
    assert failed.status.value == "failed"
    assert failed.error_code == "chat_provider_unavailable"


def test_controller_persists_and_completes_with_ranked_company_rag_evidence() -> None:
    first_content = "  First\ncompany evidence  " + "x" * 410
    semantic = SemanticReader(
        {
            "source_label": "current_company_evidence",
            "retrieval_status": "success",
            "chunks": (
                {
                    "chunk_id": "chunk-2",
                    "document_id": "policy-2",
                    "document_title": "Second Policy",
                    "section": "Section 2",
                    "text": "Second company evidence",
                    "source_url": "https://example.test/2",
                    "relevance_score": 0.82,
                    "rerank_score": 0.79,
                },
                {
                    "chunk_id": "chunk-1",
                    "document_id": "policy-1",
                    "document_title": "First Policy",
                    "section": "Section 1",
                    "text": first_content,
                    "source_url": "https://example.test/1",
                    "relevance_score": 0.91,
                    "rerank_score": 0.88,
                },
            ),
            "citations": (),
            "scores": (),
        }
    )
    controller, buffer = _controller(
        reply=FakeReply(("Company answer",)),
        profile=ProfileReader(_profile()),
        semantic=semantic,
    )

    events = asyncio.run(
        _collect(controller, _request(user_message="What does the company policy say?"))
    )

    completed = events[-1]
    stored = buffer.read(
        MemoryNamespace(
            scope=_scope(), memory_type=MemoryType.SHORT_TERM, record_id="session-1", source_id=None
        )
    )[0]
    assert [event.event_type for event in _without_activity(events)] == [
        ChatEventType.STARTED,
        ChatEventType.DELTA,
        ChatEventType.COMPLETED,
    ]
    assert completed.retrieval_status == stored.retrieval_status == "success"
    assert [item.chunk_id for item in completed.rag_evidence] == ["chunk-2", "chunk-1"]
    assert completed.rag_evidence == stored.rag_evidence
    assert completed.rag_evidence[0].relevance_score == 0.82
    assert completed.rag_evidence[0].rerank_score == 0.79
    assert completed.rag_evidence[1].content == first_content
    assert completed.rag_evidence[1].preview == ("First company evidence " + "x" * 377)
    assert len(completed.rag_evidence[1].preview) == 400


def test_controller_records_no_results_status_without_rag_evidence() -> None:
    semantic = SemanticReader(
        {
            "source_label": "current_company_evidence",
            "retrieval_status": "no_results",
            "chunks": (),
            "citations": (),
            "scores": (),
        }
    )
    controller, buffer = _controller(
        reply=FakeReply(("No company evidence found.",)),
        profile=ProfileReader(_profile()),
        semantic=semantic,
    )

    events = asyncio.run(
        _collect(controller, _request(user_message="What does the company policy say?"))
    )

    stored = buffer.read(
        MemoryNamespace(
            scope=_scope(), memory_type=MemoryType.SHORT_TERM, record_id="session-1", source_id=None
        )
    )[0]
    assert events[-1].retrieval_status == stored.retrieval_status == "no_results"
    assert events[-1].rag_evidence == stored.rag_evidence == ()


def test_project_retrieval_evidence_is_emitted_to_the_rag_panel_with_its_score() -> None:
    project_documents = ProjectDocumentResponse(
        evidence=(
            ProjectDocumentEvidence(
                citation_id="project:document-1:chunk-1",
                chunk_id="chunk-1",
                document_id="document-1",
                project_id="project-1",
                title="dang_ky_xe.pdf",
                text="Quy trình đăng ký xe gồm bước nộp hồ sơ và nhận biển số.",
                page_start=1,
                page_end=1,
                section="Quy trình thực hiện",
                score=0.93,
            ),
        )
    )

    evidence, status = _rag_evidence(
        GenerationContext(
            current_instruction=LabeledSection(ContextSource.CURRENT_INSTRUCTION, "question"),
            active_session_turns=None,
            current_company_evidence=None,
            stored_preference=None,
            advisory_episodes=None,
            conflict_precedence=(),
        ),
        project_documents,
    )

    assert status == "success"
    assert evidence[0].source == "project_document"
    assert evidence[0].document_title == "dang_ky_xe.pdf"
    assert evidence[0].relevance_score == 0.93
    assert evidence[0].preview == "Quy trình đăng ký xe gồm bước nộp hồ sơ và nhận biển số."


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
    visible_task_events = _without_activity(task_events)
    assert [event.event_type for event in visible_task_events] == [
        ChatEventType.STARTED,
        ChatEventType.DELTA,
        ChatEventType.MEMORY_CITATION,
        ChatEventType.TASK_PROPOSAL,
        ChatEventType.COMPLETED,
    ]
    assert visible_task_events[2].source_id == written.episode_id
    assert visible_task_events[3].proposal is not None
    assert visible_task_events[3].proposal["episode_id"] == written.episode_id
    proposal_index = next(
        index
        for index, event in enumerate(task_events)
        if event.event_type is ChatEventType.TASK_PROPOSAL
    )
    completed_activity_index = next(
        index
        for index, event in enumerate(task_events)
        if event.event_type is ChatEventType.ACTIVITY
        and event.activities[-1].code is ChatActivityCode.PREPARING_ACTION_PLAN
        and event.activities[-1].status is ChatActivityStatus.COMPLETED
    )
    assert completed_activity_index > proposal_index


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

    visible_events = _without_activity(events)
    assert [event.event_type for event in visible_events] == [
        ChatEventType.STARTED,
        ChatEventType.ERROR,
        ChatEventType.DELTA,
        ChatEventType.COMPLETED,
    ]
    assert visible_events[1].code == "optional_memory_degraded"
    assert visible_events[1].safe_message == "Một phần bộ nhớ tùy chọn hiện không khả dụng."
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
        started = await anext(stream)
        assert started.event_type is ChatEventType.STARTED
        first = await anext(stream)
        while first.event_type is ChatEventType.ACTIVITY:
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


def test_cancel_turn_stops_only_the_named_durable_turn() -> None:
    async def scenario() -> None:
        history = HistoryWriter()
        reply = FakeReply(("must not stream",))
        controller, buffer = _controller(
            reply=reply,
            profile=ProfileReader(_profile()),
            history=history,
        )
        stream = controller.stream_message(_request())
        started = await anext(stream)

        assert await controller.cancel_turn(started.turn_id) is True
        assert [event async for event in stream] == []
        assert reply.calls == []
        assert history.updates[-1][1].status.value == "cancelled"
        assert history.updates[-1][1].turn_id == started.turn_id
        assert buffer.read(
            MemoryNamespace(
                scope=_scope(),
                memory_type=MemoryType.SHORT_TERM,
                record_id="session-1",
                source_id=None,
            )
        ) == ()

    asyncio.run(scenario())


def test_cancel_turn_does_not_acknowledge_a_failed_durable_update() -> None:
    async def scenario() -> None:
        history = FailingUpdateHistory()
        controller, _ = _controller(
            reply=FakeReply(("must not stream",)),
            profile=ProfileReader(_profile()),
            history=history,
        )
        stream = controller.stream_message(_request())
        started = await anext(stream)

        with pytest.raises(RuntimeError, match="database unavailable"):
            await controller.cancel_turn(started.turn_id)

    asyncio.run(scenario())


def test_cancel_turn_by_idempotency_key_targets_the_pending_turn() -> None:
    async def scenario() -> None:
        history = HistoryWriter()
        controller, _ = _controller(
            reply=FakeReply(("must not stream",)),
            profile=ProfileReader(_profile()),
            history=history,
        )
        stream = controller.stream_message(_request())
        await anext(stream)

        assert await controller.cancel_turn_by_idempotency_key("idem-1") is True
        assert history.updates[-1][1].status.value == "cancelled"
        assert [event async for event in stream] == []

    asyncio.run(scenario())


def test_completed_event_is_not_emitted_when_durable_completion_fails() -> None:
    history = FailingUpdateHistory()
    controller, buffer = _controller(
        reply=FakeReply(("Generated answer",)),
        profile=ProfileReader(_profile()),
        history=history,
    )

    events = asyncio.run(_collect(controller, _request()))

    assert [event.event_type for event in _without_activity(events)] == [
        ChatEventType.STARTED,
        ChatEventType.DELTA,
        ChatEventType.ERROR,
    ]
    assert events[-1].code == "chat_history_unavailable"
    assert buffer.read(
        MemoryNamespace(
            scope=_scope(),
            memory_type=MemoryType.SHORT_TERM,
            record_id="session-1",
            source_id=None,
        )
    ) == ()


def test_reply_failure_emits_only_a_safe_error_and_does_not_append_the_turn() -> None:
    controller, buffer = _controller(
        reply=BrokenReply(), profile=ProfileReader(_profile())
    )

    events = asyncio.run(_collect(controller, _request()))

    visible_events = _without_activity(events)
    assert [event.event_type for event in visible_events] == [
        ChatEventType.STARTED,
        ChatEventType.ERROR,
    ]
    assert visible_events[1].code == "chat_provider_unavailable"
    assert "sensitive" not in visible_events[1].safe_message
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


def test_durable_completed_idempotent_request_replays_without_calling_the_provider() -> None:
    history = CompletedHistory()
    reply = FakeReply(("must not regenerate",))
    controller, _ = _controller(
        reply=reply,
        profile=ProfileReader(_profile()),
        history=history,
    )

    events = asyncio.run(_collect(controller, _request()))

    visible_events = _without_activity(events)
    assert [event.event_type for event in visible_events] == [
        ChatEventType.STARTED,
        ChatEventType.DELTA,
        ChatEventType.COMPLETED,
    ]
    assert visible_events[1].text == "Previously completed answer"
    assert reply.calls == []
    assert history.updates == []


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

    first_terminal_activity = next(
        event.activities[-1]
        for event in reversed(first)
        if event.event_type is ChatEventType.ACTIVITY
    )
    assert first_terminal_activity.code is ChatActivityCode.PREPARING_ACTION_PLAN
    assert first_terminal_activity.status is ChatActivityStatus.COMPLETED
    assert first_terminal_activity.outcome is ChatActivityOutcome.DEGRADED

    assert [event.event_type for event in _without_activity(first)] == [
        ChatEventType.STARTED,
        ChatEventType.DELTA,
        ChatEventType.ERROR,
        ChatEventType.COMPLETED,
    ]
    assert [event.event_type for event in _without_activity(retry)] == [
        ChatEventType.STARTED,
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
        scope = await registry.create(user_id="user@example.com")

        assert await registry.require(
            scope.session_id, user_id="user@example.com"
        ) == scope
        with pytest.raises(ChatSessionAccessDenied):
            await registry.require(
                scope.session_id, user_id="other@example.com"
            )

    asyncio.run(scenario())


def test_session_registry_deletes_only_the_verified_principals_session() -> None:
    registry = InMemoryChatSessionRegistry(new_id=lambda: "session-1")

    async def scenario() -> None:
        scope = await registry.create(user_id="user@example.com")

        assert not await registry.delete(scope.session_id, user_id="other@example.com")
        assert await registry.require(scope.session_id, user_id="user@example.com") == scope
        assert await registry.delete(scope.session_id, user_id="user@example.com")
        with pytest.raises(ChatSessionAccessDenied):
            await registry.require(scope.session_id, user_id="user@example.com")

    asyncio.run(scenario())


def test_explicit_task_request_emits_task_proposal_card_and_supports_approval() -> None:
    task_proposal = ChatTaskProposal(
        task_title="Nộp hồ sơ cấp lại CCCD",
        minimal_request_paraphrase="Các bước nộp hồ sơ xin cấp lại CCCD",
        action_plan=(
            "Bước 1: Đăng nhập VNeID",
            "Bước 2: Chọn Cấp lại CCCD",
            "Bước 3: Xác nhận thông tin và nộp",
        ),
        rag_citations=(),
        missing_information=(),
        model_id="gemini-3.5-flash-lite",
        prompt_version="chat-v2",
        confidence=0.95,
    )
    reply = FakeReply(
        (
            ChatReplyChunk(
                "Dưới đây là kế hoạch các bước nộp hồ sơ cấp lại CCCD.",
                task_proposal=task_proposal,
            ),
        )
    )
    episodes = EpisodeWriter()
    controller, _ = _controller(
        reply=reply,
        profile=ProfileReader(_profile()),
        episodes=episodes,
    )

    # 1. Câu hỏi thường không có từ khóa tạo task -> KHÔNG sinh event TASK_PROPOSAL
    normal_request = _request(
        idempotency_key="req-normal",
        user_message="Thủ tục cấp lại CCCD gồm giấy tờ gì?",
    )
    normal_events = asyncio.run(_collect(controller, normal_request))
    assert ChatEventType.TASK_PROPOSAL not in [e.event_type for e in normal_events]

    # 2. Câu lệnh có từ khóa "Tạo task" -> SINH event TASK_PROPOSAL (Card HITL)
    task_request = _request(
        idempotency_key="req-task",
        user_message="Tạo task các bước nộp hồ sơ xin cấp lại CCCD",
    )
    task_events = asyncio.run(_collect(controller, task_request))
    assert [e.event_type for e in _without_activity(task_events)] == [
        ChatEventType.STARTED,
        ChatEventType.DELTA,
        ChatEventType.MEMORY_CITATION,
        ChatEventType.TASK_PROPOSAL,
        ChatEventType.COMPLETED,
    ]

    proposal_event = next(e for e in task_events if e.event_type is ChatEventType.TASK_PROPOSAL)
    assert proposal_event.proposal is not None
    assert proposal_event.proposal["task_title"] == "Nộp hồ sơ cấp lại CCCD"
    assert len(proposal_event.proposal["action_plan"]) == 3
    assert proposal_event.proposal["validation_status"] == ValidationStatus.SYSTEM_GENERATED
    assert proposal_event.proposal["retrieval_eligible"] is False

    # 3. Người dùng Approve Card -> Transition sang USER_APPROVED, retrieval_eligible = True
    episode_id = str(proposal_event.proposal["episode_id"])
    approved_episode = asyncio.run(controller.approve_task_episode(episode_id))
    assert approved_episode is not None
    assert approved_episode.validation_status is ValidationStatus.USER_APPROVED
    assert approved_episode.retrieval_eligible is True



def test_a_broken_response_is_not_reported_as_a_provider_outage() -> None:
    """The memory evaluation counted these as dropouts and aborted runs over them."""

    history = HistoryWriter()
    controller, _ = _controller(
        reply=InvalidResponseReply(),
        profile=ProfileReader(_profile()),
        history=history,
    )

    events = asyncio.run(_collect(controller, _request()))

    assert events[-1].code == "chat_response_invalid"
    assert history.updates[-1][1].error_code == "chat_response_invalid"
    assert "validation" not in events[-1].safe_message
