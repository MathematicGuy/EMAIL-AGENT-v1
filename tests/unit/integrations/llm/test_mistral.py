"""Tests for the Mistral OpenAI-compatible adapter without external calls."""

import asyncio
import json
from email.message import Message
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from cowork_agent.config import MistralSettings
from cowork_agent.integrations.llm.providers.mistral import (
    MISTRAL_CHAT_COMPLETIONS_URL,
    MistralActionPlanGenerator,
    MistralAPIError,
    _post_json,
    _request_body,
)


def test_mistral_settings_require_configured_key_and_model_without_exposing_key() -> None:
    with pytest.raises(ValueError, match="MISTRAL_API_KEY"):
        MistralSettings.from_env({"MISTRAL_MODEL": "test-model"}, load_env_file=False)
    with pytest.raises(ValueError, match="MISTRAL_MODEL"):
        MistralSettings.from_env(
            {"MISTRAL_API_KEY": "test-key", "MISTRAL_MODEL": "replace-with-mistral-model"},
            load_env_file=False,
        )

    settings = MistralSettings.from_env(
        {"MISTRAL_API_KEY": "test-key", "MISTRAL_MODEL": "mistral-small-2603"},
        load_env_file=False,
    )

    assert settings.model == "mistral-small-2603"
    assert "test-key" not in repr(settings)


def test_mistral_settings_reject_values_above_resource_bounds() -> None:
    base = {"MISTRAL_API_KEY": "test-key", "MISTRAL_MODEL": "test-model"}
    with pytest.raises(ValueError, match="MISTRAL_MAX_OUTPUT_TOKENS must not exceed 4096"):
        MistralSettings.from_env({**base, "MISTRAL_MAX_OUTPUT_TOKENS": "4097"}, load_env_file=False)
    with pytest.raises(ValueError, match="MISTRAL_TIMEOUT_SECONDS must not exceed 120"):
        MistralSettings.from_env({**base, "MISTRAL_TIMEOUT_SECONDS": "121"}, load_env_file=False)


def test_mistral_reasoning_request_sets_high_effort_and_top_p() -> None:
    body = _request_body(
        "mistral-medium-3-5",
        "system",
        "prompt",
        {"type": "object"},
        4096,
        reasoning_effort="high",
    )

    assert body["reasoning_effort"] == "high"
    assert body["top_p"] == 1.0


def test_mistral_completion_uses_fixed_url_and_bounded_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"choices": [{"message": {"content": "{}"}}]}'

    def fake_urlopen(request: Request, timeout: int) -> FakeResponse:
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads((request.data or b"").decode())
        return FakeResponse()

    monkeypatch.setattr(
        "cowork_agent.integrations.llm.providers.openai_transport.urlopen", fake_urlopen
    )

    response = _post_json(MISTRAL_CHAT_COMPLETIONS_URL, "test-key", {"model": "test"}, 12)

    assert response["choices"]
    assert captured == {
        "url": "https://api.mistral.ai/v1/chat/completions",
        "timeout": 12,
        "body": {"model": "test"},
    }


def test_mistral_http_errors_keep_only_safe_status_and_integer_retry_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = Message()
    headers["Retry-After"] = "17"

    def raise_http_error(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise HTTPError(
            MISTRAL_CHAT_COMPLETIONS_URL,
            429,
            "raw body must not escape",
            headers,
            None,
        )

    monkeypatch.setattr(
        "cowork_agent.integrations.llm.providers.mistral.post_json", raise_http_error
    )

    with pytest.raises(MistralAPIError) as excinfo:
        _post_json(MISTRAL_CHAT_COMPLETIONS_URL, "test-key", {"model": "test"}, 12)

    error = excinfo.value
    assert error.status_code == 429
    assert error.retry_after_seconds == 17
    assert not hasattr(error, "headers")
    assert "raw body must not escape" not in repr(error)


@pytest.mark.parametrize("retry_after", ["invalid", "Wed, 21 Oct 2015 07:28:00 GMT"])
def test_mistral_ignores_non_integer_retry_after_metadata(
    monkeypatch: pytest.MonkeyPatch, retry_after: str
) -> None:
    headers = Message()
    headers["Retry-After"] = retry_after

    def raise_http_error(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise HTTPError(MISTRAL_CHAT_COMPLETIONS_URL, 503, "error", headers, None)

    monkeypatch.setattr(
        "cowork_agent.integrations.llm.providers.mistral.post_json", raise_http_error
    )

    with pytest.raises(MistralAPIError) as excinfo:
        _post_json(MISTRAL_CHAT_COMPLETIONS_URL, "test-key", {"model": "test"}, 12)

    assert excinfo.value.status_code == 503
    assert excinfo.value.retry_after_seconds is None


def test_mistral_generator_rejects_missing_chat_completion_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_post_json(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        return {"choices": []}

    monkeypatch.setattr(
        "cowork_agent.integrations.llm.providers.mistral._post_json",
        lambda *args, **kwargs: asyncio.run(fake_post_json(*args, **kwargs)),
    )
    settings = MistralSettings.from_env(
        {"MISTRAL_API_KEY": "test-key", "MISTRAL_MODEL": "test-model"},
        load_env_file=False,
    )

    async def scenario() -> None:
        with pytest.raises(MistralAPIError, match="chat completion"):
            await MistralActionPlanGenerator(settings)._complete("test prompt")

    asyncio.run(scenario())


def test_mistral_generator_requests_json_with_bounded_output_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_post_json(
        url: str, api_key: str, body: dict[str, object], timeout_seconds: int
    ) -> dict[str, object]:
        captured.update({"url": url, "api_key": api_key, "body": body, "timeout": timeout_seconds})
        return {"choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(
        "cowork_agent.integrations.llm.providers.mistral._post_json", fake_post_json
    )
    settings = MistralSettings.from_env(
        {
            "MISTRAL_API_KEY": "test-key",
            "MISTRAL_MODEL": "mistral-small-2603",
            "MISTRAL_MAX_OUTPUT_TOKENS": "1024",
            "MISTRAL_TIMEOUT_SECONDS": "45",
        },
        load_env_file=False,
    )

    async def scenario() -> None:
        await MistralActionPlanGenerator(settings)._complete("test prompt")

    asyncio.run(scenario())

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "mistral-small-2603"
    assert body["max_tokens"] == 1024
    assert body["response_format"] == {"type": "json_object"}
    assert captured["timeout"] == 45
