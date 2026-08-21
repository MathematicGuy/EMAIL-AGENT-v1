"""Đánh giá End-to-End Live Intent Classification & LLM Chat Response với Gemini thật 100%.

KHÔNG MOCK:
- Router: GeminiIntentClassifier thật
- Generator: GeminiChatReply thật
- Controller: ChatController thật
- Memory: MemoryGateway + InMemoryChatSessionBuffer thật
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any

from cowork_agent.config import (
    ChatIntentSettings,
    GeminiSettings,
    load_runtime_environment,
)
from cowork_agent.domain.chat_contracts import (
    ChatEventType,
    ChatMemoryScope,
    ChatMessageRequest,
    ChatSummaryEpisode,
    TaskEpisode,
)
from cowork_agent.features.ai_chat.controller import ChatController
from cowork_agent.features.ai_chat.intent.service import (
    ChatRoutingService,
    EmptyReadyDocumentCatalog,
)
from cowork_agent.features.ai_chat.memory_gateway import MemoryGateway
from cowork_agent.features.ai_chat.session_buffer import InMemoryChatSessionBuffer
from cowork_agent.integrations.llm.chat_intent import GeminiIntentClassifier
from cowork_agent.integrations.llm.chat_reply import GeminiChatReply

load_runtime_environment()

DATASET_PATH = Path("evaluations/CHAT/qa-test/qa-chatbot-intent-dataset.json")


class LiveEpisodicStore:
    """Store episodic in-memory thật phục vụ việc ghi nhận task/summary."""

    def __init__(self) -> None:
        self.episodes: list[TaskEpisode] = []
        self.summaries: list[ChatSummaryEpisode] = []

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
        self.summaries.append(summary)
        return summary

    async def transition_task_episode(self, transition: object) -> TaskEpisode | None:
        del transition
        return None


@dataclass
class ScenarioLiveResult:
    id: str
    intent: str
    path: str
    question: str
    status: str
    failure_reasons: list[str]
    actual_intent: str
    actual_route: str
    actual_confidence: float
    actual_reason_codes: list[str]
    actual_assistant_text: str
    actual_conversation_title: str | None
    actual_task_proposal: dict[str, Any] | None
    latency_ms: int


async def run_single_scenario(
    item: dict[str, Any],
    gemini_cfg: GeminiSettings,
    intent_cfg: ChatIntentSettings,
    semaphore: asyncio.Semaphore,
) -> ScenarioLiveResult:
    async with semaphore:
        started = monotonic()
        case_id = item["id"]
        intent_str = item["intent"]
        path_str = item["path"]
        question = item["question"]
        req_outputs = item.get("required_outputs", {})

        classifier = GeminiIntentClassifier.from_settings(gemini_cfg, intent_cfg)
        routing_service = ChatRoutingService(
            classifier=classifier,
            catalog=EmptyReadyDocumentCatalog(),
            model_id=intent_cfg.model,
            timeout_ms=intent_cfg.timeout_ms,
            max_attempts=2,
        )
        chat_reply = GeminiChatReply.from_settings(gemini_cfg)

        scope = ChatMemoryScope(user_id="live_eval_user", session_id=f"session_{case_id}")
        session_buffer = InMemoryChatSessionBuffer(max_turns=10, ttl_seconds=300)
        episodic_store = LiveEpisodicStore()
        memory_gw = MemoryGateway(
            scope=scope,
            session_buffer=session_buffer,
            episodic_memory=episodic_store,
        )

        controller = ChatController(
            scope=scope,
            memory=memory_gw,
            reply=chat_reply,
            routing=routing_service,  # type: ignore[arg-type]
            new_id=iter(f"{case_id}_id_{i}" for i in range(100)).__next__,
            clock=lambda: datetime(2026, 8, 14, 10, 0, tzinfo=UTC),
        )

        request = ChatMessageRequest(f"session_{case_id}", question, f"req_{case_id}")

        failure_reasons: list[str] = []
        actual_text_parts: list[str] = []
        actual_proposal: dict[str, Any] | None = None
        actual_title: str | None = None
        actual_intent = "unknown"
        actual_route = "unknown"
        actual_conf = 0.0
        actual_reasons: list[str] = []

        try:
            outcome = await routing_service.route(
                scope=scope,
                request=request,
                recent_turns=(),
            )
            actual_intent = outcome.decision.intent.value
            actual_route = outcome.route.value
            actual_conf = outcome.decision.confidence
            actual_reasons = [r.value for r in outcome.reason_codes]

            events = [e async for e in controller.stream_message(request)]

            for event in events:
                if event.event_type is ChatEventType.DELTA and event.text:
                    actual_text_parts.append(event.text)
                elif event.event_type is ChatEventType.TASK_PROPOSAL:
                    actual_proposal = dict(event.proposal) if event.proposal else None
                elif event.event_type is ChatEventType.ERROR and event.code:
                    actual_text_parts.append(f"[ERROR:{event.code}] {event.safe_message}")

            actual_full_text = "".join(actual_text_parts).strip()

            if not actual_full_text:
                failure_reasons.append("Phản hồi text từ Gemini rỗng.")

            if intent_str in ("chat", "knowledge_query", "action_request"):
                if actual_intent != intent_str:
                    failure_reasons.append(
                        f"Intent sai: kỳ vọng '{intent_str}', thực tế '{actual_intent}'"
                    )

            if path_str == "ambiguity_path" and actual_route != "clarify":
                failure_reasons.append(
                    f"Route sai cho câu mơ hồ: kỳ vọng 'clarify', thực tế '{actual_route}'"
                )

            expected_proposal = req_outputs.get("task_proposal")
            if expected_proposal:
                if not actual_proposal:
                    failure_reasons.append(
                        "Kỳ vọng tạo Task Proposal nhưng Gemini không sinh proposal."
                    )
            elif actual_proposal:
                failure_reasons.append(
                    f"Tự ý sinh Task Proposal bất hợp lệ: {actual_proposal.get('task_title')}"
                )

            if req_outputs.get("safety_refusal") and actual_proposal:
                failure_reasons.append("Bị rò rỉ Task Proposal khi có vi phạm an toàn.")

        except Exception as exc:
            actual_full_text = f"EXCEPTION: {exc}"
            failure_reasons.append(f"Lỗi thực thi: {exc}")

        latency = max(0, int((monotonic() - started) * 1000))
        status = "PASSED" if not failure_reasons else "FAILED"

        return ScenarioLiveResult(
            id=case_id,
            intent=intent_str,
            path=path_str,
            question=question,
            status=status,
            failure_reasons=failure_reasons,
            actual_intent=actual_intent,
            actual_route=actual_route,
            actual_confidence=actual_conf,
            actual_reason_codes=actual_reasons,
            actual_assistant_text=actual_full_text,
            actual_conversation_title=actual_title,
            actual_task_proposal=actual_proposal,
            latency_ms=latency,
        )


async def main_async(sample_count: int | None, output_path: Path) -> int:
    gemini_cfg = GeminiSettings.from_env()
    intent_cfg = ChatIntentSettings.from_env(default_model=gemini_cfg.model)

    raw_data: list[dict[str, Any]] = json.loads(DATASET_PATH.read_text(encoding="utf-8"))

    if sample_count and sample_count > 0:
        seen: dict[str, int] = {}
        target_items: list[dict[str, Any]] = []
        for item in raw_data:
            key = f"{item['intent']}:{item['path']}"
            if seen.get(key, 0) < sample_count:
                target_items.append(item)
                seen[key] = seen.get(key, 0) + 1
    else:
        target_items = raw_data

    print(
        f"Bắt đầu chạy đánh giá Live LLM Thật với {len(target_items)} "
        f"scenarios trên Gemini ({gemini_cfg.model})..."
    )

    semaphore = asyncio.Semaphore(4)
    tasks = [
        run_single_scenario(item, gemini_cfg, intent_cfg, semaphore)
        for item in target_items
    ]
    results: list[ScenarioLiveResult] = await asyncio.gather(*tasks)

    passed_count = sum(1 for r in results if r.status == "PASSED")
    failed_count = sum(1 for r in results if r.status == "FAILED")
    total_count = len(results)
    accuracy = (passed_count / total_count * 100) if total_count > 0 else 0.0

    report = {
        "evaluation_timestamp": datetime.now(UTC).isoformat(),
        "model": gemini_cfg.model,
        "total_scenarios": total_count,
        "passed": passed_count,
        "failed": failed_count,
        "accuracy_pct": round(accuracy, 2),
        "results": [asdict(r) for r in results],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 80)
    print("KẾT QUẢ ĐÁNH GIÁ REAL GEMINI LIVE RESPONSE (100% NO MOCK)")
    print("=" * 80)
    print(f"Tổng số kịch bản: {total_count}")
    print(f"Passed: {passed_count} ({accuracy:.2f}%)")
    print(f"Failed: {failed_count} ({100 - accuracy:.2f}%)")
    print(f"Báo cáo chi tiết đã lưu tại: {output_path}")
    print("=" * 80)

    failures = [r for r in results if r.status == "FAILED"]
    if failures:
        print("\nDANH SÁCH MẪU CÁC CÂU FAILED THỰC TẾ:")
        for f in failures[:10]:
            print(f"\n[{f.id}] ({f.intent} | {f.path})")
            print(f"  Câu hỏi: {f.question}")
            print(f"  Lý do fail: {'; '.join(f.failure_reasons)}")
            print("  Gemini trả về:")
            print(f"    - Route: {f.actual_route}, Intent: {f.actual_intent}")
            print(f"    - Text (trích đoạn): {f.actual_assistant_text[:120]}...")
            if f.actual_task_proposal:
                print(f"    - Proposal: {f.actual_task_proposal.get('task_title')}")

    return 0


def main() -> None:
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sample-per-bucket",
        type=int,
        default=None,
        help="Số lượng mẫu mỗi bucket (intent x path). Bỏ trống để chạy toàn bộ.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluations/CHAT/baselines/live-real-gemini-eval.json"),
        help="Đường dẫn file kết quả JSON.",
    )
    args = parser.parse_args()
    asyncio.run(main_async(args.sample_per_bucket, args.output))


if __name__ == "__main__":
    main()
