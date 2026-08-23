from collections.abc import Mapping

import pytest

from cowork_agent.config import VyceSettings
from cowork_agent.integrations.llm.providers.vyce import (
    VyceAPIError,
    VyceGatewayError,
    VyceRateLimitError,
    _completion_json,
    _request_body,
    execute_chat_completion,
)


def test_vyce_settings_from_env_defaults() -> None:
    settings = VyceSettings.from_env(
        {"VYCE_API_KEY": "test-key-1"},
        load_env_file=False,
    )
    assert settings.rotator.keys == ("test-key-1",)
    assert settings.model == "gpt-5.6-luna"
    assert settings.base_url == "https://vyceai.com/v1"
    assert settings.max_emails_per_batch == 5
    assert settings.max_output_tokens == 4096
    assert settings.timeout_seconds == 60
    assert settings.rotate_on_rate_limit is True
    assert settings.max_attempts == 1


def test_vyce_settings_from_env_multiple_keys() -> None:
    settings = VyceSettings.from_env(
        {
            "VYCE_API_KEY_1": "key-one",
            "VYCE_API_KEY_2": "key-two",
            "VYCE_API_KEY_3": "key-three",
        },
        load_env_file=False,
    )
    assert settings.rotator.keys == ("key-one", "key-two", "key-three")
    assert settings.max_attempts == 3


def test_vyce_settings_from_env_fallback_to_vyne() -> None:
    settings = VyceSettings.from_env(
        {"VYNE_API_KEY": "legacy-vyne-key"},
        load_env_file=False,
    )
    assert settings.rotator.keys == ("legacy-vyne-key",)


def test_vyce_settings_from_env_validates_model() -> None:
    with pytest.raises(ValueError, match="VYCE_MODEL must be a real Vyce model name"):
        VyceSettings.from_env(
            {"VYCE_API_KEY": "test-key", "VYCE_MODEL": "replace-with-real-model"},
            load_env_file=False,
        )


def test_vyce_request_body_structure() -> None:
    body = _request_body(
        model="gpt-5.6-luna",
        system_instruction="System prompt",
        prompt="User prompt",
        schema={"type": "object", "properties": {"result": {"type": "string"}}},
        max_output_tokens=1024,
    )
    assert body["model"] == "gpt-5.6-luna"
    assert body["temperature"] == 0.0
    assert body["max_tokens"] == 1024
    assert body["response_format"] == {"type": "json_object"}
    messages = body["messages"]
    assert isinstance(messages, list)
    assert "System prompt" in messages[0]["content"]
    assert "CRITICAL JSON FORMAT REQUIREMENT" in messages[0]["content"]
    assert "User prompt" in messages[1]["content"]


def test_vyce_completion_json_normalizes_and_extracts() -> None:
    # 1. Valid JSON object with markdown code fences
    response = {
        "choices": [
            {
                "message": {
                    "content": '```json\n{"assistant_text": "Xin chào", "task_proposal": null}\n```'
                }
            }
        ]
    }
    payload = _completion_json(response)
    assert payload["assistant_text"] == "Xin chào"
    assert payload["conversation_title"] == "Phản hồi"
    assert payload["citation_ids"] == []

    # 2. Raw conversational text (Instructor coercion)
    response_raw = {
        "choices": [
            {
                "message": {
                    "content": "Tôi không có thông tin về số điện thoại của bạn."
                }
            }
        ]
    }
    payload_coerced = _completion_json(response_raw)
    assert payload_coerced["assistant_text"] == "Tôi không có thông tin về số điện thoại của bạn."
    assert payload_coerced["conversation_title"].startswith("Tôi không có thông tin")
    assert payload_coerced["citation_ids"] == []
    assert payload_coerced["task_proposal"] is None


@pytest.mark.asyncio
async def test_vyce_execute_chat_completion_rotates_on_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = VyceSettings.from_env(
        {
            "VYCE_API_KEY_1": "key-1",
            "VYCE_API_KEY_2": "key-2",
        },
        load_env_file=False,
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
            raise VyceRateLimitError("429 Rate Limit")
        return {"choices": [{"message": {"content": '{"success": true}'}}]}

    monkeypatch.setattr("cowork_agent.integrations.llm.providers.vyce._post_json", mock_post_json)

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
async def test_vyce_execute_chat_completion_rotates_on_502_gateway_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = VyceSettings.from_env(
        {
            "VYCE_API_KEY_1": "key-1",
            "VYCE_API_KEY_2": "key-2",
        },
        load_env_file=False,
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
            raise VyceGatewayError("502 Bad Gateway")
        return {"choices": [{"message": {"content": '{"recovered": true}'}}]}

    monkeypatch.setattr("cowork_agent.integrations.llm.providers.vyce._post_json", mock_post_json)

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
async def test_vyce_execute_chat_completion_all_keys_fail_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = VyceSettings.from_env(
        {
            "VYCE_API_KEY_1": "key-1",
            "VYCE_API_KEY_2": "key-2",
        },
        load_env_file=False,
    )

    def mock_post_json(
        url: str, key: str, body: Mapping[str, object], timeout: int
    ) -> Mapping[str, object]:
        raise VyceRateLimitError("429 Rate Limit")

    monkeypatch.setattr("cowork_agent.integrations.llm.providers.vyce._post_json", mock_post_json)

    with pytest.raises(VyceAPIError, match="429 Rate Limit"):
        await execute_chat_completion(
            settings,
            "System prompt",
            "User prompt",
            {"type": "object"},
        )
