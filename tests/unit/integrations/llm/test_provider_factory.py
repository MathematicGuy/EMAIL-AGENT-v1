"""Provider factory name normalization and composed classifier capabilities."""

import asyncio
from collections.abc import Mapping

import pytest

from cowork_agent.domain.chat_contracts import IntentClassifierInput
from cowork_agent.features.ai_chat.tools import Tool, ToolResult
from cowork_agent.integrations.llm.provider_factory import (
    normalize_llm_provider,
    resolve_chat_providers,
)


def test_normalize_maps_mimo_and_rejects_unknown() -> None:
    assert normalize_llm_provider("Gemini") == "gemini"
    assert normalize_llm_provider("MIMO") == "mimo"
    assert normalize_llm_provider("mimo") == "mimo"
    with pytest.raises(ValueError, match="LLM_PROVIDER must be"):
        normalize_llm_provider("unknown")


def test_resolved_classifier_prompt_lists_the_tools_composed_for_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dropping the tools argument makes a flag-on classifier blind to its actions."""
    prompts: list[str] = []

    class FakeTransport:
        async def generate(self, **kwargs: object) -> Mapping[str, object]:
            prompts.append(str(kwargs["prompt"]))
            return {
                "intent": "action_request",
                "needs_rag": False,
                "needs_tool": True,
                "tool_name": "create_calendar_event",
                "needs_clarification": False,
                "retrieval_query": None,
                "confidence": 0.95,
                "reason_codes": ["external_action_requested"],
            }

    async def unused_handler(_: Mapping[str, object]) -> ToolResult:
        return ToolResult(ok=True, text="unused")

    monkeypatch.setattr(
        "cowork_agent.integrations.llm.providers.gemini.GoogleGenAITransport",
        FakeTransport,
    )
    monkeypatch.setenv("GEMINI_API_KEY_1", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
    monkeypatch.setenv("CHAT_INTENT_CLASSIFIER_MODEL", "gemini-test")
    tool = Tool(
        name="create_calendar_event",
        description="create an event on the user's calendar",
        parameters={"type": "object"},
        handler=unused_handler,
    )

    classifier = resolve_chat_providers("gemini", tools=(tool,)).intent_classifier
    asyncio.run(classifier.classify(IntentClassifierInput("Create a meeting", (), ())))

    assert len(prompts) == 1
    assert "TIER 4.5 — AVAILABLE ACTIONS" in prompts[0]
    assert "- create_calendar_event: create an event" in prompts[0]
