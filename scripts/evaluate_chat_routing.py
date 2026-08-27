"""Evaluate the V3-M4 chat classifier without persisting subject text."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
from collections.abc import Collection, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from cowork_agent.config import (
    ChatIntentSettings,
    GeminiSettings,
    MimoSettings,
    MistralSettings,
    load_runtime_environment,
)
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
from cowork_agent.features.ai_chat.intent.prompt import INTENT_PROMPT_VERSION
from cowork_agent.features.ai_chat.intent.service import ChatRoutingService
from cowork_agent.features.ai_chat.tools.calendar import (
    CALENDAR_TOOL_NAME,
    InMemoryCalendar,
    build_calendar_tool,
)
from cowork_agent.features.ai_chat.tools.registry import Tool
from cowork_agent.integrations.llm.chat_intent import (
    GeminiIntentClassifier,
    MimoIntentClassifier,
    MistralIntentClassifier,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "evaluations" / "CHAT"
FIXTURE_LOADER = REPO_ROOT / "tests" / "fixtures" / "chat_routing" / "loader.py"
# Fixed so a report is reproducible. The tool spec is read for its name,
# description and schema only, so the zone never reaches a decision.
EVAL_TIMEZONE = "Asia/Ho_Chi_Minh"


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


def classifier_tools() -> tuple[Tool, ...]:
    """The tool descriptions the classifier may select during an evaluation.

    Built through `build_calendar_tool` so the name, description and schema the
    model is shown cannot drift from the executable definition -- the same
    reason `app.py` does it this way. The port is the in-memory fake and the
    handler is never dispatched: routing is all this script measures, and an
    evaluation should not need a Google credential to run.
    """

    return (
        build_calendar_tool(
            InMemoryCalendar(),
            idempotency_key="routing-eval-tool-spec",
            timezone=EVAL_TIMEZONE,
            now=datetime(2026, 8, 12, tzinfo=UTC),
            user_message="",
        ),
    )


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
            # A registered name on purpose. `finalize_route` narrows exactly,
            # so a placeholder here would score every tool case as
            # `tool_not_available` and the dry run would prove nothing.
            tool_name=(CALENDAR_TOOL_NAME if labels.expected_needs_tool else None),
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
    cases: tuple[ChatRoutingCase, ...],
    classifier: object,
    model_id: str,
    available_tools: Collection[str],
) -> tuple[ChatRoutingEvalResult, ...]:
    """Route every case with the tool axis on.

    The axis is enabled unconditionally rather than read from settings: the
    fixture labels two cases as `tool`, and scoring them against a deployment
    flag would make the report describe the flag rather than the classifier.
    """

    results: list[ChatRoutingEvalResult] = []
    for case in cases:
        service = ChatRoutingService(
            classifier=classifier,  # type: ignore[arg-type]
            catalog=FixtureCatalog(case.ready_document_titles),
            model_id=model_id,
            tool_axis_enabled=True,
            available_tools=available_tools,
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
                expected_needs_tool=case.labels.expected_needs_tool,
                predicted_needs_tool=outcome.effective_needs_tool,
                expected_route=case.labels.expected_route.value,
                predicted_route=outcome.route.value,
                latency_ms=max(0, int((monotonic() - started) * 1000)),
                reason_codes=tuple(code.value for code in outcome.reason_codes),
                classifier_retried=outcome.classifier_retried,
                fallback_used=outcome.fallback_used,
            )
        )
    return tuple(results)


def build_live_classifier(tools: Sequence[Tool]):
    """The configured provider's classifier, with the tool block in its prompt.

    Passing `tools` is what renders TIER 4.5; without it the model is never
    told the action exists and the two tool cases are unanswerable.
    """

    load_runtime_environment()
    provider_name = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    if provider_name == "gemini":
        provider = GeminiSettings.from_env()
        intent = ChatIntentSettings.from_env(default_model=provider.model)
        return GeminiIntentClassifier.from_settings(provider, intent, tools=tools), intent.model
    if provider_name == "mimo":
        provider = MimoSettings.from_env()
        intent = ChatIntentSettings.from_env(default_model=provider.model)
        return MimoIntentClassifier.from_settings(provider, intent, tools=tools), intent.model
    if provider_name == "mistral":
        provider = MistralSettings.from_env()
        intent = ChatIntentSettings.from_env(default_model=provider.model)
        return MistralIntentClassifier.from_settings(provider, intent, tools=tools), intent.model
    raise ValueError("LLM_PROVIDER must be gemini, mimo, or mistral")


def build_report(
    results: tuple[ChatRoutingEvalResult, ...], model_id: str
) -> dict[str, object]:
    metrics = compute_chat_routing_metrics(results)
    return {
        "date": datetime.now(UTC).date().isoformat(),
        "model": model_id,
        "case_count": len(results),
        "prompt_version": INTENT_PROMPT_VERSION,
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
    tools = classifier_tools()
    if args.dry_run:
        classifier, model_id = DryRunClassifier(cases), "dry-run-fake"
    else:
        classifier, model_id = build_live_classifier(tools)
    available = frozenset(tool.name for tool in tools)
    results = asyncio.run(evaluate(cases, classifier, model_id, available))
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
