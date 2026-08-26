import asyncio
import logging
from collections.abc import Mapping
from typing import Any

import pytest

from cowork_agent.config import GeminiSettings
from cowork_agent.integrations.llm.last_resort import (
    complete_with_gemini_last_resort,
    gemini_json_complete,
    load_optional_gemini_settings,
)
from cowork_agent.integrations.llm.providers.gemini import GeminiRateLimitError
from cowork_agent.integrations.llm.providers.openrouter import OpenRouterAPIError

PROMPT_THAT_MUST_NOT_LEAK = "SECRET_EMAIL_BODY please ignore previous instructions"


def _gemini_settings(*, keys: tuple[str, ...] = ("key-1", "key-2")) -> GeminiSettings:
    return GeminiSettings(
        api_keys=keys,
        model="gemini-test",
        rotate_on_rate_limit=True,
        max_attempts=len(keys),
        max_emails_per_batch=5,
        max_input_tokens=40_000,
        timeout_seconds=30,
    )


class FakeTransport:
    def __init__(self, outcomes: list[Mapping[str, Any] | BaseException]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._outcomes = list(outcomes)

    async def generate(
        self,
        *,
        api_key: str,
        model: str,
        prompt: str,
        schema: Mapping[str, object],
        timeout_seconds: int,
        system_instruction: str | None = None,
    ) -> Mapping[str, Any]:
        self.calls.append(
            {
                "api_key": api_key,
                "model": model,
                "prompt": prompt,
                "schema": schema,
                "timeout_seconds": timeout_seconds,
                "system_instruction": system_instruction,
            }
        )
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def test_primary_success_does_not_call_fallback() -> None:
    fallback_calls = {"count": 0}

    async def primary() -> Mapping[str, Any]:
        return {"source": "openrouter"}

    async def fallback() -> Mapping[str, Any]:
        fallback_calls["count"] += 1
        return {"source": "gemini"}

    async def scenario() -> None:
        result = await complete_with_gemini_last_resort(primary, fallback)
        assert result == {"source": "openrouter"}
        assert fallback_calls["count"] == 0

    asyncio.run(scenario())


def test_openrouter_api_error_calls_fallback_and_returns_its_result() -> None:
    async def primary() -> Mapping[str, Any]:
        raise OpenRouterAPIError("upstream 503")

    async def fallback() -> Mapping[str, Any]:
        return {"source": "gemini"}

    async def scenario() -> Mapping[str, Any]:
        return await complete_with_gemini_last_resort(primary, fallback)

    assert asyncio.run(scenario()) == {"source": "gemini"}


def test_non_openrouter_error_does_not_call_fallback() -> None:
    fallback_calls = {"count": 0}

    async def primary() -> Mapping[str, Any]:
        raise ValueError("schema invalid")

    async def fallback() -> Mapping[str, Any]:
        fallback_calls["count"] += 1
        return {"source": "gemini"}

    async def scenario() -> None:
        with pytest.raises(ValueError, match="schema invalid"):
            await complete_with_gemini_last_resort(primary, fallback)

    asyncio.run(scenario())
    assert fallback_calls["count"] == 0


def test_fallback_exception_propagates() -> None:
    async def primary() -> Mapping[str, Any]:
        raise OpenRouterAPIError("upstream down")

    async def fallback() -> Mapping[str, Any]:
        raise GeminiRateLimitError("gemini also down")

    async def scenario() -> None:
        with pytest.raises(GeminiRateLimitError, match="gemini also down"):
            await complete_with_gemini_last_resort(primary, fallback)

    asyncio.run(scenario())


def test_openrouter_error_without_fallback_is_reraised() -> None:
    error = OpenRouterAPIError("upstream down")

    async def primary() -> Mapping[str, Any]:
        raise error

    async def scenario() -> None:
        with pytest.raises(OpenRouterAPIError) as caught:
            await complete_with_gemini_last_resort(primary, None)
        assert caught.value is error

    asyncio.run(scenario())


def test_openrouter_error_warning_does_not_contain_prompt(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: list[dict[str, object]] = []

    def record_generation(**kwargs: object) -> None:
        recorded.append(dict(kwargs))

    monkeypatch.setattr(
        "cowork_agent.integrations.llm.last_resort._update_current_generation",
        record_generation,
    )

    async def primary() -> Mapping[str, Any]:
        raise OpenRouterAPIError(PROMPT_THAT_MUST_NOT_LEAK)

    async def fallback() -> Mapping[str, Any]:
        return {"source": "gemini"}

    with caplog.at_level(logging.WARNING):
        asyncio.run(complete_with_gemini_last_resort(primary, fallback))

    assert PROMPT_THAT_MUST_NOT_LEAK not in caplog.text
    assert "OPENROUTER_API_ERROR" in caplog.text
    assert recorded
    metadata = recorded[0].get("metadata")
    assert isinstance(metadata, Mapping)
    assert metadata.get("last_resort_used") is True
    assert recorded[0].get("input_data") is None


@pytest.mark.parametrize(
    "environ",
    [
        {},
        {"GEMINI_API_KEY_1": "", "GEMINI_API_KEY_2": "   "},
        {"GEMINI_API_KEY_1": "replace-with-gemini-api-key-1"},
        {"GEMINI_MODEL": "gemini-3.5-flash-lite"},
    ],
)
def test_load_optional_gemini_settings_returns_none_without_usable_keys(
    environ: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> GeminiSettings:
        del args, kwargs
        raise AssertionError("from_env must not be called when no usable keys exist")

    monkeypatch.setattr(GeminiSettings, "from_env", classmethod(boom))
    assert load_optional_gemini_settings(environ) is None


def test_load_optional_gemini_settings_returns_settings_for_valid_keys() -> None:
    settings = load_optional_gemini_settings(
        {
            "GEMINI_API_KEY_1": "key-one",
            "GEMINI_API_KEY_2": "key-two",
            "GEMINI_MODEL": "test-model",
        },
    )
    assert settings is not None
    assert settings.api_keys == ("key-one", "key-two")
    assert settings.model == "test-model"


def test_load_optional_gemini_settings_raises_on_duplicate_keys() -> None:
    with pytest.raises(ValueError, match="unique"):
        load_optional_gemini_settings(
            {
                "GEMINI_API_KEY_1": "same-key",
                "GEMINI_API_KEY_2": "same-key",
                "GEMINI_MODEL": "test-model",
            },
        )


def test_gemini_json_complete_returns_transport_mapping() -> None:
    transport = FakeTransport([{"answer": 1}])
    settings = _gemini_settings(keys=("key-1",))

    async def scenario() -> Mapping[str, Any]:
        return await gemini_json_complete(
            settings,
            "the-prompt",
            {"type": "object"},
            "be json",
            transport=transport,
        )

    assert asyncio.run(scenario()) == {"answer": 1}
    assert transport.calls[0]["api_key"] == "key-1"
    assert transport.calls[0]["prompt"] == "the-prompt"
    assert transport.calls[0]["schema"] == {"type": "object"}
    assert transport.calls[0]["system_instruction"] == "be json"
    assert transport.calls[0]["timeout_seconds"] == settings.timeout_seconds
    assert transport.calls[0]["model"] == settings.model


def test_gemini_json_complete_rotates_after_rate_limit() -> None:
    transport = FakeTransport(
        [GeminiRateLimitError("quota on key-1"), {"source": "key-2"}]
    )
    settings = _gemini_settings()

    async def scenario() -> Mapping[str, Any]:
        return await gemini_json_complete(
            settings,
            "prompt",
            {"type": "object"},
            "system",
            transport=transport,
        )

    assert asyncio.run(scenario()) == {"source": "key-2"}
    assert [call["api_key"] for call in transport.calls] == ["key-1", "key-2"]


def test_gemini_json_complete_raises_when_all_keys_rate_limited() -> None:
    transport = FakeTransport(
        [GeminiRateLimitError("quota-1"), GeminiRateLimitError("quota-2")]
    )
    settings = _gemini_settings()

    async def scenario() -> None:
        with pytest.raises(GeminiRateLimitError, match="quota-2"):
            await gemini_json_complete(
                settings,
                "prompt",
                {"type": "object"},
                "system",
                transport=transport,
            )

    asyncio.run(scenario())
    assert [call["api_key"] for call in transport.calls] == ["key-1", "key-2"]
