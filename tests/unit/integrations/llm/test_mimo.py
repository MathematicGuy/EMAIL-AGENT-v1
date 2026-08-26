from collections.abc import Mapping

import pytest

from cowork_agent.config import MimoSettings
from cowork_agent.integrations.llm.providers.mimo import (
    MimoAPIError,
    MimoGatewayError,
    MimoRateLimitError,
    execute_chat_completion,
)


def test_mimo_settings_from_env_defaults() -> None:
    settings = MimoSettings.from_env(
        {"MIMO_API_KEY": "test-key-1"},
    )
    assert settings.rotator.keys == ("test-key-1",)
    assert settings.model == "mimo-v2.5-pro"
    assert settings.base_url == "https://token-plan-ams.xiaomimimo.com/v1"
    assert settings.max_emails_per_batch == 5
    assert settings.max_output_tokens == 4096
    assert settings.timeout_seconds == 60
    assert settings.rotate_on_rate_limit is True
    assert settings.max_attempts == 1


def test_mimo_settings_from_env_multiple_keys() -> None:
    settings = MimoSettings.from_env(
        {
            "MIMO_API_KEY_1": "key-one",
            "MIMO_API_KEY_2": "key-two",
            "MIMO_API_KEY_3": "key-three",
        },
    )
    assert settings.rotator.keys == ("key-one", "key-two", "key-three")
    assert settings.max_attempts == 3


def test_mimo_settings_from_env_validates_model() -> None:
    with pytest.raises(ValueError, match="MIMO_MODEL must be a real Mimo model name"):
        MimoSettings.from_env(
            {"MIMO_API_KEY": "test-key", "MIMO_MODEL": "replace-with-real-model"},
        )


@pytest.mark.asyncio
async def test_mimo_execute_chat_completion_rotates_on_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = MimoSettings.from_env(
        {
            "MIMO_API_KEY_1": "key-1",
            "MIMO_API_KEY_2": "key-2",
        },
    )

    attempted_keys: list[str] = []
    sleeps: list[float] = []

    async def mock_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("asyncio.sleep", mock_sleep)

    def mock_post_json(
        url: str, key: str, body: Mapping[str, object], timeout: int
    ) -> Mapping[str, object]:
        attempted_keys.append(key)
        if key == "key-1":
            raise MimoRateLimitError("429 Rate Limit")
        return {"choices": [{"message": {"content": '{"success": true}'}}]}

    monkeypatch.setattr("cowork_agent.integrations.llm.providers.mimo._post_json", mock_post_json)

    result = await execute_chat_completion(
        settings,
        "System prompt",
        "User prompt",
        {"type": "object"},
    )
    assert result == {"success": True}
    assert attempted_keys == ["key-1", "key-2"]
    assert sleeps == [0.5]


@pytest.mark.asyncio
async def test_mimo_fast_mode_disables_thinking_and_captures_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = MimoSettings.from_env({"MIMO_API_KEY": "key-1"})
    received: dict[str, object] = {}

    def mock_post_json(
        url: str, key: str, body: Mapping[str, object], timeout: int
    ) -> Mapping[str, object]:
        del url, key, timeout
        received.update(body)
        return {
            "choices": [{"message": {
                "content": '{"answer": true}',
                "reasoning_content": "provider reasoning",
            }}]
        }

    monkeypatch.setattr("cowork_agent.integrations.llm.providers.mimo._post_json", mock_post_json)

    result = await execute_chat_completion(
        settings,
        "System prompt",
        "User prompt",
        {"type": "object"},
        reasoning_mode="fast",
        capture_reasoning=True,
    )

    assert received["thinking"] == {"type": "disabled"}
    assert result == {"answer": True, "__provider_reasoning__": "provider reasoning"}


@pytest.mark.asyncio
async def test_mimo_execute_chat_completion_rotates_on_502_gateway_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = MimoSettings.from_env(
        {
            "MIMO_API_KEY_1": "key-1",
            "MIMO_API_KEY_2": "key-2",
        },
    )

    attempted_keys: list[str] = []
    sleeps: list[float] = []

    async def mock_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("asyncio.sleep", mock_sleep)

    def mock_post_json(
        url: str, key: str, body: Mapping[str, object], timeout: int
    ) -> Mapping[str, object]:
        attempted_keys.append(key)
        if key == "key-1":
            raise MimoGatewayError("502 Bad Gateway")
        return {"choices": [{"message": {"content": '{"recovered": true}'}}]}

    monkeypatch.setattr("cowork_agent.integrations.llm.providers.mimo._post_json", mock_post_json)

    result = await execute_chat_completion(
        settings,
        "System prompt",
        "User prompt",
        {"type": "object"},
    )
    assert result == {"recovered": True}
    assert attempted_keys == ["key-1", "key-2"]
    assert sleeps == [0.5]


@pytest.mark.asyncio
async def test_mimo_execute_chat_completion_all_keys_fail_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = MimoSettings.from_env(
        {
            "MIMO_API_KEY_1": "key-1",
            "MIMO_API_KEY_2": "key-2",
        },
    )

    def mock_post_json(
        url: str, key: str, body: Mapping[str, object], timeout: int
    ) -> Mapping[str, object]:
        raise MimoRateLimitError("429 Rate Limit")

    monkeypatch.setattr("cowork_agent.integrations.llm.providers.mimo._post_json", mock_post_json)

    with pytest.raises(MimoAPIError, match="429 Rate Limit"):
        await execute_chat_completion(
            settings,
            "System prompt",
            "User prompt",
            {"type": "object"},
        )
