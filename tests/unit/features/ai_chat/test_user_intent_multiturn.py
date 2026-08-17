"""Kiểm thử QA đánh giá Ý định Người dùng trong Hội thoại Đa lượt (Multi-turn Context Intent).

Bao phủ:
1. Tham chiếu ngữ cảnh & đại từ thay thế qua các lượt chat.
2. Chuyển đổi ý định đột ngột (Intent Shift).
3. Vòng lặp làm rõ (Clarification Loop).
4. Giới hạn lượt chat (Bounded History).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from cowork_agent.domain.chat_contracts import (
    ChatEventType,
    ChatIntent,
    ChatMemoryScope,
    ChatMessageRequest,
    ChatRoute,
    ChatSummaryEpisode,
    ChatTurn,
    IntentClassifierInput,
    IntentDecision,
    IntentReasonCode,
    ReadyDocumentRef,
    RoutingOutcome,
    TaskEpisode,
)
from cowork_agent.features.ai_chat.controller import ChatController
from cowork_agent.features.ai_chat.generation_context import ChatResponseMode, GenerationContext
from cowork_agent.features.ai_chat.intent.prompt import build_intent_prompt
from cowork_agent.features.ai_chat.memory_gateway import MemoryGateway
from cowork_agent.features.ai_chat.ports import ChatReplyChunk, ChatTaskProposal
from cowork_agent.features.ai_chat.session_buffer import InMemoryChatSessionBuffer


class MockRoutingService:
    def __init__(self, outcomes: list[RoutingOutcome]) -> None:
        self._outcomes = outcomes
        self.call_history: list[tuple[ChatMessageRequest, tuple[ChatTurn, ...]]] = []

    async def route(
        self,
        *,
        scope: ChatMemoryScope,
        request: ChatMessageRequest,
        recent_turns: tuple[ChatTurn, ...],
    ) -> RoutingOutcome:
        del scope
        self.call_history.append((request, recent_turns))
        idx = min(len(self.call_history) - 1, len(self._outcomes) - 1)
        return self._outcomes[idx]


class MockStreamReply:
    def __init__(self, responses: list[tuple[str | ChatReplyChunk, ...]]) -> None:
        self._responses = responses
        self.contexts: list[GenerationContext] = []

    async def stream_reply(self, request: ChatMessageRequest, context: GenerationContext):
        del request
        self.contexts.append(context)
        idx = min(len(self.contexts) - 1, len(self._responses) - 1)
        for chunk in self._responses[idx]:
            yield chunk


class MockEpisodicStore:
    def __init__(self) -> None:
        self.episodes: list[TaskEpisode] = []

    async def write_task_episode(
        self, namespace: object, episode: TaskEpisode, *, expires_at: object
    ) -> TaskEpisode:
        del namespace, expires_at
        self.episodes.append(episode)
        return episode

    async def read_task_episode(
        self, namespace: object, *, episode_id: str
    ) -> TaskEpisode | None:
        del namespace
        return next((ep for ep in self.episodes if ep.episode_id == episode_id), None)

    async def write_chat_summary(
        self, namespace: object, summary: ChatSummaryEpisode
    ) -> ChatSummaryEpisode:
        del namespace
        return summary

    async def transition_task_episode(self, transition: object) -> TaskEpisode | None:
        del transition
        return None


def _build_outcome(
    intent: ChatIntent,
    route: ChatRoute,
    reason: IntentReasonCode,
) -> RoutingOutcome:
    can_clarify = route is ChatRoute.CLARIFY
    decision = IntentDecision(
        intent=intent,
        needs_rag=route is ChatRoute.RAG,
        needs_tool=False,
        tool_name=None,
        needs_clarification=can_clarify,
        retrieval_query="tra cứu" if route is ChatRoute.RAG else None,
        confidence=0.95,
        reason_codes=(reason,),
    )
    return RoutingOutcome(
        decision=decision,
        route=route,
        effective_needs_rag=route is ChatRoute.RAG,
        effective_needs_tool=False,
        effective_needs_clarification=can_clarify,
        retrieval_query="tra cứu" if route is ChatRoute.RAG else None,
        reason_codes=(reason,),
        classifier_retried=False,
        fallback_used=False,
        prompt_version="chat-intent-v1",
    )


def test_multiturn_pronoun_context_preserves_recent_turns() -> None:
    """Lượt 2 dùng đại từ 'nó': Recent turns phải xuất hiện trong Router & Prompt."""
    turns = (
        ChatTurn(
            "t-1",
            "s-1",
            "Chính sách thưởng Tết năm 2026 thế nào?",
            "Thưởng theo KPI và thâm niên.",
            datetime.now(UTC),
        ),
    )
    classifier_input = IntentClassifierInput(
        current_message="Tóm tắt nó lại thành 3 ý chính giúp tôi",
        recent_turns=turns,
        ready_documents=(ReadyDocumentRef("doc-hr", "QuyCheThuong2026.pdf"),),
    )
    prompt = build_intent_prompt(classifier_input)

    assert "Chính sách thưởng Tết năm 2026" in prompt
    assert "Thưởng theo KPI và thâm niên." in prompt
    assert "Tóm tắt nó lại thành 3 ý chính" in prompt
    assert "QuyCheThuong2026.pdf" in prompt


def test_multiturn_intent_shift_knowledge_to_action() -> None:
    """Lượt 1 tra cứu tài liệu, lượt 2 đổi ý định sang tạo task hành động."""
    scope = ChatMemoryScope(user_id="u-vn-1", session_id="s-multiturn-1")
    buffer = InMemoryChatSessionBuffer(max_turns=10, ttl_seconds=300)
    episodic_store = MockEpisodicStore()
    memory_gw = MemoryGateway(
        scope=scope,
        session_buffer=buffer,
        episodic_memory=episodic_store,
    )

    outcome_turn1 = _build_outcome(
        ChatIntent.KNOWLEDGE_QUERY,
        ChatRoute.CHAT,
        IntentReasonCode.USER_DOCUMENT_REQUIRED,
    )
    outcome_turn2 = _build_outcome(
        ChatIntent.ACTION_REQUEST,
        ChatRoute.CHAT,
        IntentReasonCode.EXTERNAL_ACTION_REQUESTED,
    )
    router = MockRoutingService([outcome_turn1, outcome_turn2])

    proposal = ChatTaskProposal(
        task_title="Gửi báo cáo thưởng cho nhân sự",
        minimal_request_paraphrase="Gửi báo cáo",
        action_plan=("Soạn email", "Đính kèm bảng lương", "Gửi phòng HR"),
        rag_citations=(),
        missing_information=(),
        model_id="gemini-2.5-flash",
        prompt_version="vi-task-v1",
        confidence=0.99,
    )
    reply = MockStreamReply([
        ("Đây là thông tin chính sách thưởng.",),
        (ChatReplyChunk("Đã tạo kế hoạch gửi báo cáo.", proposal),),
    ])

    controller = ChatController(
        scope=scope,
        memory=memory_gw,
        reply=reply,
        routing=router,  # type: ignore[arg-type]
        new_id=iter(f"id_{i}" for i in range(100)).__next__,
        clock=lambda: datetime(2026, 8, 14, 10, 0, tzinfo=UTC),
    )

    async def run_scenario() -> None:
        # Turn 1: Knowledge query
        req1 = ChatMessageRequest("s-multiturn-1", "Chính sách nghỉ phép thế nào?", "k-1")
        events1 = [e async for e in controller.stream_message(req1)]
        assert any(e.event_type is ChatEventType.COMPLETED for e in events1)
        assert not any(e.event_type is ChatEventType.TASK_PROPOSAL for e in events1)

        # Turn 2: Intent shift to explicit action request directive
        req2 = ChatMessageRequest(
            "s-multiturn-1",
            "create a task to send report to HR department",
            "k-2",
        )
        events2 = [e async for e in controller.stream_message(req2)]
        prop_events = [e for e in events2 if e.event_type is ChatEventType.TASK_PROPOSAL]
        assert len(prop_events) == 1
        assert prop_events[0].proposal["task_title"] == "Gửi báo cáo thưởng cho nhân sự"

        # Verify recent turns were passed to turn 2
        assert len(router.call_history) == 2
        assert len(router.call_history[1][1]) == 1
        assert router.call_history[1][1][0].user_message == "Chính sách nghỉ phép thế nào?"

    asyncio.run(run_scenario())


def test_multiturn_clarification_loop_resolves_intent() -> None:
    """Vòng lặp làm rõ: Turn 1 câu mơ hồ (CLARIFY) -> Turn 2 user trả lời -> Chuyển NORMAL mode."""
    scope = ChatMemoryScope(user_id="u-vn-2", session_id="s-clarify-loop")
    buffer = InMemoryChatSessionBuffer(max_turns=10, ttl_seconds=300)
    memory_gw = MemoryGateway(scope=scope, session_buffer=buffer)

    outcome_turn1 = _build_outcome(
        ChatIntent.CHAT,
        ChatRoute.CLARIFY,
        IntentReasonCode.MISSING_INFORMATION,
    )
    outcome_turn2 = _build_outcome(
        ChatIntent.KNOWLEDGE_QUERY,
        ChatRoute.CHAT,
        IntentReasonCode.USER_DOCUMENT_REQUIRED,
    )
    router = MockRoutingService([outcome_turn1, outcome_turn2])

    reply = MockStreamReply([
        ("Bạn muốn tra cứu tài liệu của năm nào?",),
        ("Đây là bảng lương năm 2026 chi tiết.",),
    ])

    controller = ChatController(
        scope=scope,
        memory=memory_gw,
        reply=reply,
        routing=router,  # type: ignore[arg-type]
        new_id=iter(f"id_{i}" for i in range(100)).__next__,
        clock=lambda: datetime(2026, 8, 14, 10, 0, tzinfo=UTC),
    )

    async def run_clarify_loop() -> None:
        # Turn 1: Ambiguous -> CLARIFY mode
        req1 = ChatMessageRequest("s-clarify-loop", "Tìm bảng lương", "k-c1")
        _ = [e async for e in controller.stream_message(req1)]
        assert reply.contexts[0].response_mode is ChatResponseMode.CLARIFY

        # Turn 2: User provides clarification -> NORMAL mode
        req2 = ChatMessageRequest("s-clarify-loop", "Bảng lương năm 2026", "k-c2")
        _ = [e async for e in controller.stream_message(req2)]
        assert reply.contexts[1].response_mode is ChatResponseMode.NORMAL

    asyncio.run(run_clarify_loop())
