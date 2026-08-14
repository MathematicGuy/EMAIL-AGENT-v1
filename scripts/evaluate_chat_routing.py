"""Evaluate the V3-M4 chat classifier without persisting subject text."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from cowork_agent.config import ChatIntentSettings, FaucetSettings, GeminiSettings, GroqSettings
from cowork_agent.domain.chat_contracts import (
    ChatIntent,
    ChatMemoryScope,
    ChatMessageRequest,
    ChatTurn,
    IntentClassifierInput,
    IntentDecision,
    IntentReasonCode,
    ReadyDocumentRef,
)
from cowork_agent.features.ai_chat.intent.evaluation import (
    ChatRoutingEvalResult,
    compute_chat_routing_metrics,
)
from cowork_agent.features.ai_chat.intent.service import ChatRoutingService
from cowork_agent.integrations.llm.chat_intent import (
    FaucetIntentClassifier,
    GeminiIntentClassifier,
    GroqIntentClassifier,
)

DEFAULT_OUTPUT_DIR = Path("docs/evaluations/CHAT")
FIXTURE_LOADER = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "chat_routing"
    / "loader.py"
)


def _load_fixture_module():
    spec = importlib.util.spec_from_file_location("chat_routing_fixture_loader", FIXTURE_LOADER)
    if spec is None or spec.loader is None:
        raise RuntimeError("chat routing fixture loader is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_fixture_module = _load_fixture_module()
ChatRoutingCase = _fixture_module.ChatRoutingCase
load_chat_routing_cases = _fixture_module.load_chat_routing_cases


class FixtureCatalog:
    def __init__(self, titles: tuple[str, ...]) -> None:
        self._documents = tuple(
            ReadyDocumentRef(f"fixture-doc-{index}", title)
            for index, title in enumerate(titles, start=1)
        )

    async def list_ready(self, scope: object, *, at: object):
        del scope, at
        return self._documents


class DryRunClassifier:
    def __init__(self, cases: tuple[ChatRoutingCase, ...]) -> None:
        self._labels = {case.current_message: case.labels for case in cases}

    async def classify(self, classifier_input: IntentClassifierInput) -> IntentDecision:
        labels = self._labels[classifier_input.current_message]
        return IntentDecision(
            intent=(ChatIntent.KNOWLEDGE_QUERY if labels.expected_needs_rag else ChatIntent.CHAT),
            needs_rag=labels.expected_needs_rag,
            needs_tool=labels.expected_needs_tool,
            tool_name="disabled_tool" if labels.expected_needs_tool else None,
            needs_clarification=labels.expected_needs_clarification,
            retrieval_query=(
                classifier_input.current_message if labels.expected_needs_rag else None
            ),
            confidence=1.0,
            reason_codes=(
                IntentReasonCode.USER_DOCUMENT_REQUIRED
                if labels.expected_needs_rag
                else IntentReasonCode.GENERAL_CHAT,
            ),
        )


async def evaluate(
    cases: tuple[ChatRoutingCase, ...], classifier: object, model_id: str
) -> tuple[ChatRoutingEvalResult, ...]:
    results: list[ChatRoutingEvalResult] = []
    for case in cases:
        service = ChatRoutingService(
            classifier=classifier,  # type: ignore[arg-type]
            catalog=FixtureCatalog(case.ready_document_titles),
            model_id=model_id,
        )
        scope = ChatMemoryScope(user_id="fixture-user", session_id=f"session-{case.id}")
        turns = tuple(
            ChatTurn(
                f"{case.id}-turn-{index}",
                scope.session_id,
                turn.user,
                turn.assistant,
                datetime(2026, 8, 12, tzinfo=UTC),
            )
            for index, turn in enumerate(case.recent_turns, start=1)
        )
        started = monotonic()
        outcome = await service.route(
            scope=scope,
            request=ChatMessageRequest(scope.session_id, case.current_message, case.id),
            recent_turns=turns,
        )
        results.append(
            ChatRoutingEvalResult(
                case_id=case.id,
                expected_needs_rag=case.labels.expected_needs_rag,
                predicted_needs_rag=outcome.effective_needs_rag,
                expected_route=case.labels.expected_route.value,
                predicted_route=outcome.route.value,
                latency_ms=max(0, int((monotonic() - started) * 1000)),
                reason_codes=tuple(code.value for code in outcome.reason_codes),
                classifier_retried=outcome.classifier_retried,
                fallback_used=outcome.fallback_used,
            )
        )
    return tuple(results)


def build_live_classifier():
    provider_name = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    if provider_name == "gemini":
        provider = GeminiSettings.from_env()
        intent = ChatIntentSettings.from_env(default_model=provider.model)
        return GeminiIntentClassifier.from_settings(provider, intent), intent.model
    if provider_name == "groq":
        provider = GroqSettings.from_env()
        intent = ChatIntentSettings.from_env(default_model=provider.model)
        return GroqIntentClassifier.from_settings(provider, intent), intent.model
    if provider_name == "faucet":
        provider = FaucetSettings.from_env()
        intent = ChatIntentSettings.from_env(default_model=provider.model)
        return FaucetIntentClassifier.from_settings(provider, intent), intent.model
    raise ValueError("LLM_PROVIDER must be gemini, groq, or faucet")


def build_report(
    results: tuple[ChatRoutingEvalResult, ...], model_id: str
) -> dict[str, object]:
    metrics = compute_chat_routing_metrics(results)
    return {
        "date": datetime.now(UTC).date().isoformat(),
        "model": model_id,
        "case_count": len(results),
        "prompt_version": "chat-intent-v1",
        "metrics": asdict(metrics),
        "passed": metrics.passed,
        "per_case": [asdict(result) for result in results],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    cases = load_chat_routing_cases()
    if args.dry_run:
        classifier, model_id = DryRunClassifier(cases), "dry-run-fake"
    else:
        classifier, model_id = build_live_classifier()
    results = asyncio.run(evaluate(cases, classifier, model_id))
    report = build_report(results, model_id)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    target = args.output_dir / f"chat-routing-eval-{report['date']}.json"
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"Evaluated {len(results)} chat-routing cases; "
        f"passed={report['passed']}; report={target}"
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
