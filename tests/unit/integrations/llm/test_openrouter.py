"""OpenRouter chat-completions request body: native models[] fallbacks."""

import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest

from cowork_agent.config import GeminiSettings, OpenRouterSettings
from cowork_agent.domain.target_contracts import (
    BodyFormat,
    EphemeralEmailEnvelope,
    FetchStatus,
    Route,
)
from cowork_agent.integrations.llm.providers.openrouter import (
    OpenRouterActionPlanGenerator,
    OpenRouterAPIError,
    OpenRouterRouteClassifier,
    execute_chat_completion,
)
from cowork_agent.integrations.llm.providers.prompts import FALLBACK_ROUTE_DECISION

_SCHEMA: Mapping[str, object] = {"type": "object"}


def test_execute_chat_completion_sends_fallback_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []

    def fake_post_json(
        url: str, api_key: str, body: dict[str, object], timeout_seconds: int
    ) -> dict[str, object]:
        del url, api_key, timeout_seconds
        captured.append(body)
        return {"choices": [{"message": {"content": json.dumps({"emails": []})}}]}

    monkeypatch.setattr(
        "cowork_agent.integrations.llm.providers.openrouter._post_json",
        fake_post_json,
    )

    async def scenario() -> None:
        payload = await execute_chat_completion(
            "test-key",
            "deepseek/x",
            "sys",
            "prompt",
            _SCHEMA,
            128,
            12,
            fallback_models=("openai/gpt",),
        )
        assert payload == {"emails": []}

    asyncio.run(scenario())

    assert len(captured) == 1
    assert captured[0]["model"] == "deepseek/x"
    assert captured[0]["models"] == ["openai/gpt"]


def test_classifier_complete_sends_settings_fallback_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []

    def fake_post_json(
        url: str, api_key: str, body: dict[str, object], timeout_seconds: int
    ) -> dict[str, object]:
        del url, api_key, timeout_seconds
        captured.append(body)
        return {"choices": [{"message": {"content": json.dumps({"emails": []})}}]}

    monkeypatch.setattr(
        "cowork_agent.integrations.llm.providers.openrouter._post_json",
        fake_post_json,
    )
    settings = OpenRouterSettings.from_env(
        {
            "OPENROUTER_API_KEY": "test-key",
            "OPENROUTER_MODEL": "deepseek/x",
            "OPENROUTER_ALLOWED_MODELS": '["openai/gpt", "deepseek/x"]',
        },
    )

    async def scenario() -> None:
        await OpenRouterRouteClassifier(settings)._complete("prompt")

    asyncio.run(scenario())

    assert len(captured) == 1
    body = captured[0]
    assert body["model"] == "deepseek/x"
    assert body["models"] == ["openai/gpt"]


def envelope(message_id: str) -> EphemeralEmailEnvelope:
    return EphemeralEmailEnvelope(
        run_id="run-1",
        user_id="user-1",
        gmail_message_id=message_id,
        gmail_thread_id=f"thread-{message_id}",
        gmail_url=f"https://mail.example.com/{message_id}",
        sender_name="Sender",
        sender_email="sender@example.com",
        recipients=("user@example.com",),
        subject=f"Subject {message_id}",
        received_at=datetime(2026, 1, 1, tzinfo=UTC),
        labels=("INBOX",),
        normalized_body=f"body-{message_id}",
        body_format=BodyFormat.TEXT,
        attachments_present=False,
        fetch_status=FetchStatus.COMPLETE,
    )


def decision_payload(message_id: str, **overrides: object) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "providerMessageId": message_id,
        "actionability": "action_required",
        "candidateActionItem": f"Handle {message_id}",
        "emailIsSufficient": True,
        "knowledgeGaps": [],
        "retrievalQuery": None,
        "expectedDocumentTypes": [],
        "reasonCodes": ["email_self_contained"],
        "confidence": 0.9,
    }
    payload.update(overrides)
    return payload


def _openrouter_settings() -> OpenRouterSettings:
    return OpenRouterSettings.from_env(
        {
            "OPENROUTER_API_KEY": "test-key",
            "OPENROUTER_MODEL": "test-model",
        },
    )


def _gemini_settings() -> GeminiSettings:
    return GeminiSettings(
        api_keys=("key-1",),
        model="gemini-test",
        rotate_on_rate_limit=True,
        max_attempts=1,
        max_emails_per_batch=5,
        max_input_tokens=40_000,
        timeout_seconds=30,
    )


def test_classifier_hops_to_gemini_on_openrouter_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gemini_calls: list[object] = []

    def fail_post_json(
        url: str, api_key: str, body: dict[str, object], timeout_seconds: int
    ) -> dict[str, object]:
        del url, api_key, body, timeout_seconds
        raise OpenRouterAPIError("upstream down")

    async def fake_gemini_json_complete(*args: object, **kwargs: object) -> Mapping[str, Any]:
        gemini_calls.append((args, kwargs))
        return {"emails": [decision_payload("msg")]}

    monkeypatch.setattr(
        "cowork_agent.integrations.llm.providers.openrouter._post_json",
        fail_post_json,
    )
    monkeypatch.setattr(
        "cowork_agent.integrations.llm.last_resort.gemini_json_complete",
        fake_gemini_json_complete,
    )

    async def scenario() -> None:
        result = await OpenRouterRouteClassifier(
            _openrouter_settings(), last_resort=_gemini_settings()
        ).classify("UTC", datetime.now(UTC), (envelope("msg"),))
        assert result.decisions[0].is_fallback is False
        assert result.decisions[0].decision is not FALLBACK_ROUTE_DECISION
        assert result.decisions[0].decision.candidate_action_item == "Handle msg"

    asyncio.run(scenario())
    assert gemini_calls


def test_classifier_does_not_hop_on_schema_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gemini_calls: list[object] = []

    def empty_emails_post_json(
        url: str, api_key: str, body: dict[str, object], timeout_seconds: int
    ) -> dict[str, object]:
        del url, api_key, body, timeout_seconds
        return {"choices": [{"message": {"content": json.dumps({"emails": []})}}]}

    async def fake_gemini_json_complete(*args: object, **kwargs: object) -> Mapping[str, Any]:
        gemini_calls.append((args, kwargs))
        raise AssertionError("gemini last-resort must not run on schema-invalid JSON")

    monkeypatch.setattr(
        "cowork_agent.integrations.llm.providers.openrouter._post_json",
        empty_emails_post_json,
    )
    monkeypatch.setattr(
        "cowork_agent.integrations.llm.last_resort.gemini_json_complete",
        fake_gemini_json_complete,
    )

    async def scenario() -> None:
        result = await OpenRouterRouteClassifier(
            _openrouter_settings(), last_resort=_gemini_settings()
        ).classify("UTC", datetime.now(UTC), (envelope("msg"),))
        assert result.decisions[0].is_fallback is True
        assert result.decisions[0].decision == FALLBACK_ROUTE_DECISION
        assert result.decisions[0].decision.route is Route.RETRIEVE_RAG

    asyncio.run(scenario())
    assert gemini_calls == []


def test_classifier_without_last_resort_still_conservative_on_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_post_json(
        url: str, api_key: str, body: dict[str, object], timeout_seconds: int
    ) -> dict[str, object]:
        del url, api_key, body, timeout_seconds
        raise OpenRouterAPIError("upstream down")

    monkeypatch.setattr(
        "cowork_agent.integrations.llm.providers.openrouter._post_json",
        fail_post_json,
    )

    async def scenario() -> None:
        result = await OpenRouterRouteClassifier(_openrouter_settings()).classify(
            "UTC", datetime.now(UTC), (envelope("msg"),)
        )
        assert result.decisions[0].is_fallback is True
        assert result.decisions[0].decision == FALLBACK_ROUTE_DECISION

    asyncio.run(scenario())


def test_generator_hops_to_gemini_on_openrouter_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gemini_payload: Mapping[str, Any] = {"task": {"title": "from-gemini"}}
    gemini_calls: list[object] = []

    async def fail_execute(*args: object, **kwargs: object) -> Mapping[str, Any]:
        del args, kwargs
        raise OpenRouterAPIError("upstream down")

    async def fake_gemini_json_complete(*args: object, **kwargs: object) -> Mapping[str, Any]:
        gemini_calls.append((args, kwargs))
        return gemini_payload

    monkeypatch.setattr(
        "cowork_agent.integrations.llm.providers.openrouter.execute_chat_completion",
        fail_execute,
    )
    monkeypatch.setattr(
        "cowork_agent.integrations.llm.last_resort.gemini_json_complete",
        fake_gemini_json_complete,
    )

    async def scenario() -> Mapping[str, Any]:
        generator = OpenRouterActionPlanGenerator(
            _openrouter_settings(), last_resort=_gemini_settings()
        )
        return await generator._complete("p")

    assert asyncio.run(scenario()) == gemini_payload
    assert gemini_calls
