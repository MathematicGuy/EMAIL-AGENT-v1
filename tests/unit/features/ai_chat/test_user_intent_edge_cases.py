"""Kiểm thử QA đánh giá Ý định Người dùng với các Trường hợp Biên & Ngoại lệ (Edge Cases).

Bao phủ:
1. Chặn câu hỏi rỗng, chỉ gồm khoảng trắng.
2. Chặn câu hỏi vượt quá độ dài tối đa 4000 ký tự.
3. Câu hỏi biên hợp lệ: emoji, ký tự đặc biệt, tiếng Việt không dấu, code switching, code snippets.
4. Kiểm tra ghi nhận SLA độ trễ phân loại.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from cowork_agent.domain.chat_contracts import (
    MAX_CHAT_MESSAGE_LENGTH,
    ChatIntent,
    ChatMemoryScope,
    ChatMessageRequest,
    ChatRoute,
    IntentClassifierInput,
    IntentDecision,
    IntentReasonCode,
    ReadyDocumentRef,
)
from cowork_agent.features.ai_chat.generation_context import GenerationContext
from cowork_agent.features.ai_chat.intent.observability import RecordingIntentRoutingSink
from cowork_agent.features.ai_chat.intent.prompt import build_intent_prompt
from cowork_agent.features.ai_chat.intent.service import (
    ChatRoutingService,
    EmptyReadyDocumentCatalog,
)


class DirectClassifier:
    def __init__(self, decision: IntentDecision) -> None:
        self.decision = decision
        self.received_inputs: list[IntentClassifierInput] = []

    async def classify(self, classifier_input: IntentClassifierInput) -> IntentDecision:
        self.received_inputs.append(classifier_input)
        return self.decision


class MockReply:
    async def stream_reply(self, request: ChatMessageRequest, context: GenerationContext):
        del request, context
        yield "Phản hồi chuẩn từ hệ thống."


VALID_EDGE_QUERIES = [
    ("edge-emoji-only", "👋 😊 🤖 ❓ 🚀", ChatIntent.CHAT),
    ("edge-special-chars", "!@#$%^&*()_+{}|:\"<>?~`-=[]\\;',./", ChatIntent.CHAT),
    (
        "edge-unaccented-vi",
        "huong dan su dung he thong va cach nop bao cao",
        ChatIntent.KNOWLEDGE_QUERY,
    ),
    (
        "edge-mixed-code-switch",
        "cho minh hoi policy ve remote work va VPN access nhe",
        ChatIntent.KNOWLEDGE_QUERY,
    ),
    (
        "edge-code-snippet",
        "```python\ndef calculate_kpi(score: float):\n    return score * 1.2\n```\n"
        "Đoạn code trên tính KPI đúng theo quy chế công ty không?",
        ChatIntent.KNOWLEDGE_QUERY,
    ),
    (
        "edge-max-boundary-len",
        "Quy chế làm việc: " + ("A" * (MAX_CHAT_MESSAGE_LENGTH - 30)),
        ChatIntent.CHAT,
    ),
]


@pytest.mark.parametrize("invalid_query", ["", "   ", "\t\n   ", "   \r\n  "])
def test_empty_or_whitespace_query_raises_validation_error(invalid_query: str) -> None:
    """Câu hỏi rỗng hoặc chỉ toàn khoảng trắng bị chặn ngay từ domain contract."""
    with pytest.raises(ValueError, match="must be a non-empty string"):
        ChatMessageRequest("session-edge", invalid_query, "idem-edge")

    with pytest.raises(ValueError, match="must be a non-empty string"):
        IntentClassifierInput(
            current_message=invalid_query,
            recent_turns=(),
            ready_documents=(),
        )


def test_overlength_query_raises_validation_error() -> None:
    """Câu hỏi vượt quá 4000 ký tự bị chặn với thông báo lỗi rõ ràng."""
    too_long = "X" * (MAX_CHAT_MESSAGE_LENGTH + 1)
    with pytest.raises(ValueError, match="must not exceed 4000 characters"):
        ChatMessageRequest("session-edge", too_long, "idem-edge")


@pytest.mark.parametrize("case_id,query_text,intent", VALID_EDGE_QUERIES)
def test_valid_edge_cases_prompt_rendering_does_not_crash(
    case_id: str,
    query_text: str,
    intent: ChatIntent,
) -> None:
    """Prompt builder mã hóa an toàn mọi loại câu hỏi biên (dài, emoji, code)."""
    classifier_input = IntentClassifierInput(
        current_message=query_text,
        recent_turns=(),
        ready_documents=(ReadyDocumentRef("doc-edge", "HuongDan.pdf"),),
    )
    prompt = build_intent_prompt(classifier_input)
    assert "TIER 1" in prompt
    assert "TIER 5" in prompt
    assert "HuongDan.pdf" in prompt


@pytest.mark.parametrize("case_id,query_text,intent", VALID_EDGE_QUERIES)
def test_valid_edge_cases_routing_service_execution(
    case_id: str,
    query_text: str,
    intent: ChatIntent,
) -> None:
    """Routing Service điều phối thành công các câu hỏi biên mà không phát sinh ngoại lệ."""
    sink = RecordingIntentRoutingSink()
    decision = IntentDecision(
        intent=intent,
        needs_rag=False,
        needs_tool=False,
        tool_name=None,
        needs_clarification=False,
        retrieval_query=None,
        confidence=0.9,
        reason_codes=(IntentReasonCode.GENERAL_CHAT,),
    )
    classifier = DirectClassifier(decision)
    service = ChatRoutingService(
        classifier=classifier,
        catalog=EmptyReadyDocumentCatalog(),
        model_id="test-model",
        sink=sink,
        clock=lambda: datetime(2026, 8, 14, tzinfo=UTC),
    )
    scope = ChatMemoryScope(user_id="edge-user", session_id=f"session-{case_id}")
    req = ChatMessageRequest(scope.session_id, query_text, f"idem-{case_id}")

    outcome = asyncio.run(service.route(scope=scope, request=req, recent_turns=()))
    assert outcome.route is ChatRoute.CHAT
    assert len(sink.events) > 0
    assert sink.events[-1].latency_ms >= 0


def test_routing_service_latency_sla_recorded_in_sink() -> None:
    """Kiểm tra đo đạc độ trễ phân loại (Latency SLA measurement) được sink ghi lại đầy đủ."""
    sink = RecordingIntentRoutingSink()
    decision = IntentDecision(
        intent=ChatIntent.CHAT,
        needs_rag=False,
        needs_tool=False,
        tool_name=None,
        needs_clarification=False,
        retrieval_query=None,
        confidence=0.99,
        reason_codes=(IntentReasonCode.GENERAL_CHAT,),
    )
    service = ChatRoutingService(
        classifier=DirectClassifier(decision),
        catalog=EmptyReadyDocumentCatalog(),
        model_id="sla-model",
        sink=sink,
        clock=lambda: datetime(2026, 8, 14, tzinfo=UTC),
    )
    scope = ChatMemoryScope(user_id="sla-user", session_id="session-sla")
    req = ChatMessageRequest(scope.session_id, "Xin chào", "idem-sla")

    outcome = asyncio.run(service.route(scope=scope, request=req, recent_turns=()))
    assert outcome.route is ChatRoute.CHAT

    decided_events = [e for e in sink.events if e.name == "chat.route.decided"]
    assert len(decided_events) == 1
    assert decided_events[0].latency_ms is not None
    assert decided_events[0].latency_ms >= 0
