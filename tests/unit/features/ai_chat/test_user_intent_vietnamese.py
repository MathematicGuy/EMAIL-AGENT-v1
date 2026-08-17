"""Bộ kiểm thử QA tự động: Thực thi 240 Kịch bản Đánh giá Ý định Người dùng từ JSON Dataset."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from cowork_agent.domain.chat_contracts import (
    ChatEventType,
    ChatIntent,
    ChatMemoryScope,
    ChatMessageRequest,
    ChatMessageStreamEvent,
    ChatRoute,
    ChatSummaryEpisode,
    EpisodeSourceType,
    IntentDecision,
    IntentReasonCode,
    MemoryNamespace,
    MemoryType,
    RoutingOutcome,
    TaskEpisode,
)
from cowork_agent.domain.target_contracts import ValidationStatus
from cowork_agent.features.ai_chat.controller import ChatController, ChatReplyUnavailable
from cowork_agent.features.ai_chat.episode_policy import authorize_chat_summary_write
from cowork_agent.features.ai_chat.generation_context import (
    ChatResponseMode,
    GenerationContext,
)
from cowork_agent.features.ai_chat.memory_gateway import MemoryGateway
from cowork_agent.features.ai_chat.ports import ChatReplyChunk, ChatTaskProposal
from cowork_agent.features.ai_chat.session_buffer import InMemoryChatSessionBuffer

DATASET_PATH = Path(__file__).parents[4] / "docs" / "qa-test" / "qa-chatbot-intent-dataset.json"
if not DATASET_PATH.exists():
    DATASET_PATH = Path(__file__).parents[4] / "docs" / "qa-chatbot-intent-dataset.json"
DATASET: list[dict[str, Any]] = json.loads(DATASET_PATH.read_text(encoding="utf-8"))

_MOCK_REPLY_GENERIC = "Đây là phản hồi mẫu kiểm thử từ hệ thống."


class MockRoutingService:
    def __init__(self, outcome: RoutingOutcome) -> None:
        self.outcome = outcome
        self.call_count = 0

    async def route(self, **kwargs: object) -> RoutingOutcome:
        del kwargs
        self.call_count += 1
        return self.outcome


class MockStreamReply:
    def __init__(
        self,
        chunks: tuple[str | ChatReplyChunk, ...],
        raise_error: bool = False,
    ) -> None:
        self.chunks = chunks
        self.raise_error = raise_error
        self.contexts: list[GenerationContext] = []

    async def stream_reply(
        self,
        request: ChatMessageRequest,
        context: GenerationContext,
    ) -> AsyncIterator[str | ChatReplyChunk]:
        del request
        self.contexts.append(context)
        if self.raise_error:
            raise ChatReplyUnavailable("Dịch vụ LLM tạm thời không khả dụng")
        for chunk in self.chunks:
            yield chunk


class MockEpisodicStore:
    def __init__(self) -> None:
        self.episodes: list[TaskEpisode] = []
        self.summaries: list[ChatSummaryEpisode] = []

    async def write_task_episode(
        self, namespace: object, episode: TaskEpisode, *, expires_at: object
    ) -> TaskEpisode:
        del namespace, expires_at
        self.episodes.append(episode)
        return episode

    async def write_chat_summary(
        self, namespace: object, summary: ChatSummaryEpisode
    ) -> ChatSummaryEpisode:
        del namespace
        self.summaries.append(summary)
        return summary

    async def read_task_episode(
        self, namespace: object, *, episode_id: str
    ) -> TaskEpisode | None:
        del namespace
        return next((ep for ep in self.episodes if ep.episode_id == episode_id), None)

    async def transition_task_episode(self, transition: object) -> TaskEpisode | None:
        del transition
        return None


def _build_routing_outcome(
    intent: ChatIntent,
    route: ChatRoute,
    reason: IntentReasonCode,
    confidence: float = 0.95,
) -> RoutingOutcome:
    needs_clarify = route is ChatRoute.CLARIFY
    decision = IntentDecision(
        intent=intent,
        needs_rag=route is ChatRoute.RAG,
        needs_tool=False,
        tool_name=None,
        needs_clarification=needs_clarify,
        retrieval_query="tra cứu tài liệu" if route is ChatRoute.RAG else None,
        confidence=confidence,
        reason_codes=(reason,),
    )
    return RoutingOutcome(
        decision=decision,
        route=route,
        effective_needs_rag=route is ChatRoute.RAG,
        effective_needs_tool=False,
        effective_needs_clarification=needs_clarify,
        retrieval_query="tra cứu tài liệu" if route is ChatRoute.RAG else None,
        reason_codes=decision.reason_codes,
        classifier_retried=False,
        fallback_used=False,
        prompt_version="vi-eval-v1",
    )


def _build_controller(
    route: ChatRoute,
    chunks: tuple[str | ChatReplyChunk, ...],
    intent: ChatIntent = ChatIntent.CHAT,
    reason: IntentReasonCode = IntentReasonCode.GENERAL_CHAT,
    episodic_store: MockEpisodicStore | None = None,
    raise_error: bool = False,
) -> tuple[ChatController, MockRoutingService, MockStreamReply]:
    scope = ChatMemoryScope(user_id="user_viet_nam", session_id="session_eval_01")
    buffer = InMemoryChatSessionBuffer(max_turns=10, ttl_seconds=300)
    store = episodic_store or MockEpisodicStore()
    memory = MemoryGateway(scope=scope, session_buffer=buffer, episodic_memory=store)
    routing = MockRoutingService(_build_routing_outcome(intent, route, reason))
    reply = MockStreamReply(chunks, raise_error=raise_error)
    controller = ChatController(
        scope=scope,
        memory=memory,
        reply=reply,
        routing=routing,  # type: ignore[arg-type]
        new_id=iter(f"eval_id_{i}" for i in range(500)).__next__,
        clock=lambda: datetime(2026, 8, 14, 10, 0, tzinfo=UTC),
    )
    return controller, routing, reply


def _extract_text(events: list[ChatMessageStreamEvent]) -> str:
    return "".join(e.text for e in events if e.event_type is ChatEventType.DELTA and e.text)


@pytest.mark.parametrize("item", DATASET, ids=[item["id"] for item in DATASET])
def test_user_intent_dataset_scenario(item: dict[str, Any]) -> None:
    intent_str = item["intent"]
    path_str = item["path"]
    question = item["question"]
    req_outputs = item["required_outputs"]

    if intent_str == "knowledge_query":
        intent_enum = ChatIntent.KNOWLEDGE_QUERY
        reason_enum = IntentReasonCode.USER_DOCUMENT_REQUIRED
    elif intent_str == "action_request":
        intent_enum = ChatIntent.ACTION_REQUEST
        reason_enum = IntentReasonCode.EXTERNAL_ACTION_REQUESTED
    else:
        intent_enum = ChatIntent.CHAT
        reason_enum = IntentReasonCode.GENERAL_CHAT

    if path_str == "ambiguity_path" or req_outputs.get("response_mode") == "clarify":
        route_enum = ChatRoute.CLARIFY
        reason_enum = IntentReasonCode.MISSING_INFORMATION
    else:
        route_enum = ChatRoute.CHAT

    chunks: list[str | ChatReplyChunk] = []
    task_prop = req_outputs.get("task_proposal")
    if task_prop:
        proposal_obj = ChatTaskProposal(
            task_title=task_prop["task_title"],
            minimal_request_paraphrase=task_prop["minimal_request_paraphrase"],
            action_plan=tuple(task_prop["action_plan"]),
            rag_citations=(),
            missing_information=(),
            model_id="gemini-2.5-flash",
            prompt_version="vi-task-v1",
            confidence=task_prop.get("confidence", 0.98),
        )
        chunks.append(ChatReplyChunk(_MOCK_REPLY_GENERIC, proposal_obj))
    else:
        chunks.append(_MOCK_REPLY_GENERIC)

    controller, routing, reply = _build_controller(
        route=route_enum,
        chunks=tuple(chunks),
        intent=intent_enum,
        reason=reason_enum,
    )
    request = ChatMessageRequest("session_eval_01", question, f"k_eval_{item['id']}")

    async def eval_fn() -> None:
        events = [e async for e in controller.stream_message(request)]

        assert routing.call_count == 1, (
            f"[{item['id']}] Router phải được gọi đúng 1 lần, got {routing.call_count}"
        )

        assert len(reply.contexts) == 1, (
            f"[{item['id']}] LLM provider phải được gọi đúng 1 lần"
        )
        actual_mode = reply.contexts[0].response_mode
        expected_mode = (
            ChatResponseMode.CLARIFY
            if route_enum is ChatRoute.CLARIFY
            else ChatResponseMode.NORMAL
        )
        assert actual_mode is expected_mode, (
            f"[{item['id']}] Kỳ vọng {expected_mode}, nhận {actual_mode}"
        )

        prop_events = [e for e in events if e.event_type is ChatEventType.TASK_PROPOSAL]
        if task_prop:
            assert len(prop_events) == 1, (
                f"[{item['id']}] Kỳ vọng 1 TASK_PROPOSAL event, got {len(prop_events)}"
            )
            assert prop_events[0].proposal["task_title"] == task_prop["task_title"]
            assert len(prop_events[0].proposal["action_plan"]) == len(task_prop["action_plan"])
        else:
            assert len(prop_events) == 0, (
                f"[{item['id']}] Controller tự sinh TASK_PROPOSAL bất hợp lệ"
            )

        if req_outputs.get("safety_refusal"):
            assert len(prop_events) == 0, (
                f"[{item['id']}] safety_refusal nhưng controller vẫn emit TASK_PROPOSAL"
            )

        full_text = _extract_text(events)
        assert len(full_text) > 0, f"[{item['id']}] Stream không trả về text nào"
        assert _MOCK_REPLY_GENERIC in full_text

        report_data = req_outputs.get("report")
        if report_data:
            scope = ChatMemoryScope(user_id="user_vn", session_id="session_vn")
            namespace = MemoryNamespace(
                scope=scope,
                memory_type=MemoryType.EPISODIC,
                record_id=f"rec_{item['id']}",
                source_id="t1",
            )
            summary_obj = ChatSummaryEpisode(
                episode_id=f"ep_{item['id']}",
                record_id=f"rec_{item['id']}",
                user_id="user_vn",
                chat_session_id="session_vn",
                chat_turn_id="t1",
                summary=report_data["summary"],
                validation_status=ValidationStatus.SYSTEM_GENERATED,
                retrieval_eligible=report_data["retrieval_eligible"],
                source_type=EpisodeSourceType.SYSTEM_GENERATED_CHAT_SUMMARY,
                created_at=datetime(2026, 8, 14, tzinfo=UTC),
                updated_at=datetime(2026, 8, 14, tzinfo=UTC),
                expires_at=None,
                pipeline_version="v1",
                model_id="gemini-2.5-flash",
                prompt_version="vi-sum-v1",
                confidence=0.98,
            )
            authorize_chat_summary_write(namespace, summary_obj)
            assert len(summary_obj.summary) > 0

        completed_events = [e for e in events if e.event_type is ChatEventType.COMPLETED]
        assert len(completed_events) >= 1

    asyncio.run(eval_fn())
