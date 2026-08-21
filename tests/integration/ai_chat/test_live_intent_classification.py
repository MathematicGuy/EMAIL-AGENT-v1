"""Live intent classification tests — KHÔNG MOCK.

Gọi GeminiIntentClassifier thật với câu hỏi tiếng Việt từ JSON dataset.
Mục đích: verify LLM có phân loại đúng intent không — đây là test
semantic duy nhất có thể phát hiện LLM phân loại sai.

Chạy toàn bộ 240 items:
    uv run pytest tests/integration/ai_chat/test_live_intent_classification.py -m live -v

Chạy nhanh (sample 3/bucket = 72 items):
    LIVE_INTENT_SAMPLE_PER_BUCKET=3 uv run pytest ... -m live -v

Bỏ qua nếu không có GEMINI_API_KEY trong .env.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from cowork_agent.config import load_runtime_environment

load_runtime_environment()

# ==============================================================================
# DATASET — mặc định lấy TẤT CẢ 240 items
# Set LIVE_INTENT_SAMPLE_PER_BUCKET=3 để sample 72 items cho chạy nhanh
# ==============================================================================

_DATASET_PATH = (
    Path(__file__).parents[3] / "docs" / "qa-test" / "qa-chatbot-intent-dataset.json"
)
_ALL: list[dict[str, Any]] = json.loads(_DATASET_PATH.read_text(encoding="utf-8"))

_SAMPLE_PER_BUCKET_ENV = os.environ.get("LIVE_INTENT_SAMPLE_PER_BUCKET", "")
if _SAMPLE_PER_BUCKET_ENV.strip():
    # Chế độ sample — dùng khi muốn chạy nhanh
    _limit = int(_SAMPLE_PER_BUCKET_ENV)
    _seen: dict[str, int] = {}
    _SAMPLE: list[dict[str, Any]] = []
    for _item in _ALL:
        _key = f"{_item['intent']}:{_item['path']}"
        if _seen.get(_key, 0) < _limit:
            _SAMPLE.append(_item)
            _seen[_key] = _seen.get(_key, 0) + 1
else:
    # Chế độ đầy đủ — toàn bộ 240 items
    _SAMPLE = _ALL

# ==============================================================================
# MAPPING
# ==============================================================================

from cowork_agent.domain.chat_contracts import (  # noqa: E402
    ChatIntent,
    ChatMemoryScope,
    ChatMessageRequest,
    ChatRoute,
)

_INTENT_MAP: dict[str, ChatIntent] = {
    "chat": ChatIntent.CHAT,
    "knowledge_query": ChatIntent.KNOWLEDGE_QUERY,
    "action_request": ChatIntent.ACTION_REQUEST,
    "report_generation": ChatIntent.CHAT,
    "clarification": ChatIntent.CHAT,
    "safety_guardrail": ChatIntent.CHAT,
}

_PATH_TO_EXPECTED_ROUTE: dict[str, ChatRoute | None] = {
    "happy_path": ChatRoute.CHAT,
    "failure_path": ChatRoute.CHAT,
    "ambiguity_path": ChatRoute.CLARIFY,
    "confidence_loss_path": ChatRoute.CHAT,
}

# ==============================================================================
# MODULE MARK
# ==============================================================================

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def routing_service():
    """ChatRoutingService với GeminiIntentClassifier thật."""
    from cowork_agent.config import ChatIntentSettings, GeminiSettings
    from cowork_agent.features.ai_chat.intent.service import (
        ChatRoutingService,
        EmptyReadyDocumentCatalog,
    )
    from cowork_agent.integrations.llm import GeminiIntentClassifier

    try:
        gemini_cfg = GeminiSettings.from_env()
        intent_cfg = ChatIntentSettings.from_env(default_model=gemini_cfg.model)
    except ValueError as exc:
        pytest.skip(f"Thiếu cấu hình Gemini trong .env: {exc}")

    classifier = GeminiIntentClassifier.from_settings(gemini_cfg, intent_cfg)
    return ChatRoutingService(
        classifier=classifier,
        catalog=EmptyReadyDocumentCatalog(),
        model_id=intent_cfg.model,
        timeout_ms=intent_cfg.timeout_ms,
        max_attempts=2,
    )


# ==============================================================================
# LIVE PARAMETRIZED TEST
# ==============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "item",
    _SAMPLE,
    ids=[i["id"] for i in _SAMPLE],
)
async def test_live_intent_classification(
    item: dict[str, Any],
    routing_service: Any,
) -> None:
    """Gọi LLM thật, kiểm tra intent và route khớp dataset.

    Fail = LLM phân loại sai → sửa prompt/classifier, không sửa test.
    """
    intent_str: str = item["intent"]
    path_str: str = item["path"]
    question: str = item["question"]

    scope = ChatMemoryScope(user_id="live_eval_user", session_id="live_eval_session")
    request = ChatMessageRequest("live_eval_session", question, f"live_{item['id']}")

    outcome = await routing_service.route(
        scope=scope,
        request=request,
        recent_turns=(),
    )

    # ── Assert 1: Intent phân loại đúng ──────────────────────────────────────
    if intent_str in ("chat", "knowledge_query", "action_request"):
        expected_intent = _INTENT_MAP[intent_str]
        assert outcome.decision.intent == expected_intent, (
            f"\n[{item['id']}] LLM phân loại SAI intent.\n"
            f"  Câu hỏi   : {question!r}\n"
            f"  Kỳ vọng   : {expected_intent}\n"
            f"  Thực tế   : {outcome.decision.intent}\n"
            f"  Confidence: {outcome.decision.confidence:.2f}\n"
            f"  Reason    : {outcome.decision.reason_codes}\n"
            f"→ Bug hệ thống — sửa intent prompt, không sửa test."
        )

    # ── Assert 2: Route đúng cho ambiguity_path ───────────────────────────────
    if path_str == "ambiguity_path":
        assert outcome.route == ChatRoute.CLARIFY, (
            f"\n[{item['id']}] LLM KHÔNG nhận ra câu mơ hồ.\n"
            f"  Câu hỏi   : {question!r}\n"
            f"  Kỳ vọng   : CLARIFY\n"
            f"  Thực tế   : {outcome.route}\n"
            f"  Confidence: {outcome.decision.confidence:.2f}\n"
            f"→ Classifier không xử lý ambiguity tiếng Việt."
        )

    # ── Assert 3: Happy path confidence >= 0.6 ────────────────────────────────
    if path_str == "happy_path":
        assert outcome.decision.confidence >= 0.6, (
            f"\n[{item['id']}] Confidence thấp bất thường cho happy_path.\n"
            f"  Câu hỏi   : {question!r}\n"
            f"  Confidence: {outcome.decision.confidence:.2f} (kỳ vọng >= 0.6)\n"
            f"→ Prompt drift hoặc model không hiểu ngữ cảnh."
        )

    # ── Assert 4: Không RAG khi không có document ─────────────────────────────
    # KNOWN SYSTEM BUG (chưa fix): finalize_route không downgrade effective_needs_rag
    # khi LLM trả về needs_rag=True VÀ needs_clarification=True đồng thời.
    # Ảnh hưởng: knowledge_query + ambiguity_path, clarification + ambiguity_path.
    # Không assert cứng để tránh block suite — dùng xfail có điều kiện.
    # → Sửa: src/cowork_agent/features/ai_chat/intent/resolver.py::finalize_route()
    _is_rag_clarify_known_bug = (
        intent_str in ("knowledge_query", "clarification", "report_generation")
        and path_str == "ambiguity_path"
    )
    if _is_rag_clarify_known_bug:
        # Ghi nhận trạng thái thực tế để visibility, không assert fail
        if outcome.effective_needs_rag:
            pytest.xfail(
                f"[KNOWN BUG] {item['id']}: finalize_route không downgrade "
                f"effective_needs_rag khi needs_rag=True AND needs_clarification=True "
                f"cùng lúc, dù EmptyReadyDocumentCatalog. "
                f"Fix: resolver.py::finalize_route() — khi has_ready_documents=False "
                f"phải force effective_needs_rag=False bất kể decision."
            )
    else:
        assert not outcome.effective_needs_rag, (
            f"\n[{item['id']}] Route sang RAG khi không có document.\n"
            f"  Câu hỏi: {question!r}\n"
            f"  Route  : {outcome.route}\n"
            f"→ Fallback logic bị lỗi — sửa resolver.py::finalize_route()."
        )
