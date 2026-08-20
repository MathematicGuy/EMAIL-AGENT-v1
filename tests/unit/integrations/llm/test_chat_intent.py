import asyncio

import pytest

from cowork_agent.config import ChatIntentSettings, GeminiSettings
from cowork_agent.domain.chat_contracts import IntentClassifierInput
from cowork_agent.features.ai_chat.intent.service import IntentClassifierInvalidOutput
from cowork_agent.integrations.llm.chat_intent import (
    ConfiguredIntentClassifier,
    GeminiIntentClassifier,
)
from cowork_agent.integrations.llm.providers.gemini import GeminiRateLimitError

pytestmark = pytest.mark.extended


def test_configured_classifier_parses_strict_response() -> None:
    async def complete(prompt: str):
        assert "TIER 5" in prompt
        return {
            "intent": "chat",
            "needs_rag": False,
            "needs_tool": False,
            "tool_name": None,
            "needs_clarification": False,
            "retrieval_query": None,
            "confidence": 0.95,
            "reason_codes": ["general_chat"],
        }

    decision = asyncio.run(
        ConfiguredIntentClassifier(complete).classify(IntentClassifierInput("Hi", (), ()))
    )

    assert decision.needs_rag is False


def test_configured_classifier_maps_schema_errors_to_retryable_error() -> None:
    async def complete(prompt: str):
        del prompt
        return {"needs_rag": True}

    with pytest.raises(IntentClassifierInvalidOutput):
        asyncio.run(
            ConfiguredIntentClassifier(complete).classify(
                IntentClassifierInput("Hi", (), ())
            )
        )


def test_gemini_classifier_rotates_key_after_rate_limit(monkeypatch) -> None:
    attempted_keys: list[str] = []

    class FakeTransport:
        async def generate(self, **kwargs):
            api_key = kwargs["api_key"]
            attempted_keys.append(api_key)
            if api_key == "key-a":
                raise GeminiRateLimitError("limited")
            return {
                "intent": "chat",
                "needs_rag": False,
                "needs_tool": False,
                "tool_name": None,
                "needs_clarification": False,
                "retrieval_query": None,
                "confidence": 1.0,
                "reason_codes": ["general_chat"],
            }

    monkeypatch.setattr(
        "cowork_agent.integrations.llm.providers.gemini.GoogleGenAITransport",
        FakeTransport,
    )
    provider = GeminiSettings(
        api_keys=("key-a", "key-b"),
        model="gemini-test",
        rotate_on_rate_limit=True,
        max_attempts=2,
        max_emails_per_batch=5,
        max_input_tokens=40_000,
        timeout_seconds=60,
    )
    intent = ChatIntentSettings(
        enabled=True,
        model="gemini-test",
        timeout_ms=10_000,
        max_attempts=2,
        tool_axis_enabled=False,
        company_rag_enabled=False,
    )

    decision = asyncio.run(
        GeminiIntentClassifier.from_settings(provider, intent).classify(
            IntentClassifierInput("Hi", (), ())
        )
    )

    assert decision.needs_rag is False
    assert attempted_keys == ["key-a", "key-b"]
