"""Tests for the Faucet OpenAI-compatible adapter without external calls."""

import asyncio
import json
from urllib.request import Request

import pytest

from cowork_agent.config import FaucetSettings
from cowork_agent.integrations.llm.providers.faucet import (
    FAUCET_CHAT_COMPLETIONS_URL,
    FaucetActionPlanGenerator,
    FaucetAPIError,
    _post_json,
)

pytestmark = pytest.mark.extended


def test_faucet_settings_require_configured_key_and_model_without_exposing_key() -> None:
    with pytest.raises(ValueError, match="FAUCET_API_KEY"):
        FaucetSettings.from_env({"FAUCET_MODEL": "test-model"}, load_env_file=False)
    with pytest.raises(ValueError, match="FAUCET_MODEL"):
        FaucetSettings.from_env({"FAUCET_API_KEY": "test-key"}, load_env_file=False)

    settings = FaucetSettings.from_env(
        {"FAUCET_API_KEY": "test-key", "FAUCET_MODEL": "test-model"},
        load_env_file=False,
    )

    assert settings.model == "test-model"
    assert "test-key" not in repr(settings)


def test_faucet_settings_reject_values_above_resource_bounds() -> None:
    base = {"FAUCET_API_KEY": "test-key", "FAUCET_MODEL": "test-model"}
    with pytest.raises(ValueError, match="FAUCET_MAX_OUTPUT_TOKENS must not exceed 4096"):
        FaucetSettings.from_env(
            {**base, "FAUCET_MAX_OUTPUT_TOKENS": "4097"}, load_env_file=False
        )
    with pytest.raises(ValueError, match="FAUCET_TIMEOUT_SECONDS must not exceed 120"):
        FaucetSettings.from_env({**base, "FAUCET_TIMEOUT_SECONDS": "121"}, load_env_file=False)


def test_faucet_completion_uses_fixed_url_and_bounded_request(
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

    monkeypatch.setattr("cowork_agent.integrations.llm.providers.faucet.urlopen", fake_urlopen)

    response = _post_json(FAUCET_CHAT_COMPLETIONS_URL, "test-key", {"model": "test"}, 12)

    assert response["choices"]
    assert captured == {
        "url": "https://freetokenfaucet.com/v1/chat/completions",
        "timeout": 12,
        "body": {"model": "test"},
    }


def test_faucet_generator_rejects_missing_chat_completion_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_post_json(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        return {"choices": []}

    monkeypatch.setattr(
        "cowork_agent.integrations.llm.providers.faucet._post_json",
        lambda *args, **kwargs: asyncio.run(fake_post_json(*args, **kwargs)),
    )
    settings = FaucetSettings.from_env(
        {"FAUCET_API_KEY": "test-key", "FAUCET_MODEL": "test-model"},
        load_env_file=False,
    )

    async def scenario() -> None:
        with pytest.raises(FaucetAPIError, match="chat completion"):
            await FaucetActionPlanGenerator(settings)._complete("test prompt")

    asyncio.run(scenario())


def test_faucet_generator_requests_json_with_bounded_output_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_post_json(
        url: str, api_key: str, body: dict[str, object], timeout_seconds: int
    ) -> dict[str, object]:
        captured.update(
            {"url": url, "api_key": api_key, "body": body, "timeout": timeout_seconds}
        )
        return {"choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr("cowork_agent.integrations.llm.providers.faucet._post_json", fake_post_json)
    settings = FaucetSettings.from_env(
        {
            "FAUCET_API_KEY": "test-key",
            "FAUCET_MODEL": "test-model",
            "FAUCET_MAX_OUTPUT_TOKENS": "128",
            "FAUCET_TIMEOUT_SECONDS": "12",
        },
        load_env_file=False,
    )

    async def scenario() -> None:
        assert await FaucetActionPlanGenerator(settings)._complete("test prompt") == {}

    asyncio.run(scenario())

    assert captured["url"] == FAUCET_CHAT_COMPLETIONS_URL
    assert captured["api_key"] == "test-key"
    assert captured["timeout"] == 12
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "test-model"
    assert body["max_tokens"] == 128
    assert body["response_format"] == {"type": "json_object"}
