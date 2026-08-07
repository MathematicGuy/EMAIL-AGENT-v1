import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest

from cowork_agent.config import GeminiSettings
from cowork_agent.integrations.llm.providers.gemini import (
    EXTRACTION_SCHEMA,
    SYSTEM_INSTRUCTION,
    GeminiActionExtractor,
    GeminiKeyRotator,
    GeminiRateLimitError,
    _parse_batch,
)


def environment(**overrides: str) -> dict[str, str]:
    values = {
        "GEMINI_API_KEY_1": "key-one",
        "GEMINI_API_KEY_2": "key-two",
        "GEMINI_API_KEY_3": "key-three",
        "GEMINI_MODEL": "test-model",
        "GEMINI_KEY_ROTATION_STRATEGY": "round_robin",
        "GEMINI_ROTATE_ON_RATE_LIMIT": "true",
        "GEMINI_MAX_ATTEMPTS_PER_REQUEST": "3",
    }
    values.update(overrides)
    return values


def test_settings_load_three_unique_keys_without_exposing_them_in_repr() -> None:
    settings = GeminiSettings.from_env(environment(), load_env_file=False)
    assert settings.api_keys == ("key-one", "key-two", "key-three")
    assert "key-one" not in repr(settings)


def test_system_instruction_uses_requested_unread_email_prompt() -> None:
    assert SYSTEM_INSTRUCTION.startswith("Unread Email To-Do Summarizer")
    assert "is:unread in:inbox" in SYSTEM_INSTRUCTION
    assert "Đề xuất Bước Tiếp theo" in SYSTEM_INSTRUCTION
    assert "Viết toàn bộ title" in SYSTEM_INSTRUCTION
    assert "relatedMessageIds" in SYSTEM_INSTRUCTION


def test_placeholder_keys_are_not_accepted_as_credentials() -> None:
    with pytest.raises(ValueError, match="At least one"):
        GeminiSettings.from_env(
            {
                "GEMINI_API_KEY_1": "replace-with-gemini-api-key-1",
                "GEMINI_API_KEY_2": "",
                "GEMINI_API_KEY_3": "",
            },
            load_env_file=False,
        )


def test_default_model_is_gemini_3_5_flash_lite() -> None:
    values = environment()
    del values["GEMINI_MODEL"]
    settings = GeminiSettings.from_env(values, load_env_file=False)
    assert settings.model == "gemini-3.5-flash-lite"


def test_placeholder_model_is_rejected_during_startup() -> None:
    values = environment(GEMINI_MODEL="replace-with-gemini-structured-output-model")
    with pytest.raises(ValueError, match="real Gemini model"):
        GeminiSettings.from_env(values, load_env_file=False)


def test_round_robin_starts_each_request_with_the_next_key() -> None:
    async def scenario() -> None:
        rotator = GeminiKeyRotator(("key-one", "key-two", "key-three"))
        assert await rotator.candidates(3) == ("key-one", "key-two", "key-three")
        assert await rotator.candidates(3) == ("key-two", "key-three", "key-one")
        assert await rotator.candidates(3) == ("key-three", "key-one", "key-two")
        assert await rotator.candidates(3) == ("key-one", "key-two", "key-three")

    asyncio.run(scenario())


class RecordingTransport:
    def __init__(self, rate_limited_keys: set[str] | None = None) -> None:
        self.keys: list[str] = []
        self.prompts: list[str] = []
        self.rate_limited_keys = rate_limited_keys or set()

    async def generate(
        self,
        *,
        api_key: str,
        model: str,
        prompt: str,
        schema: Mapping[str, object],
        timeout_seconds: int,
    ) -> Mapping[str, Any]:
        del model, schema, timeout_seconds
        self.keys.append(api_key)
        self.prompts.append(prompt)
        if api_key in self.rate_limited_keys:
            self.rate_limited_keys.remove(api_key)
            raise GeminiRateLimitError("quota")
        return {"emails": []}


def test_extractor_rotates_to_next_key_after_rate_limit() -> None:
    async def scenario() -> None:
        settings = GeminiSettings.from_env(environment(), load_env_file=False)
        transport = RecordingTransport({"key-one"})
        extractor = GeminiActionExtractor(settings, transport)
        result = await extractor.extract("Asia/Ho_Chi_Minh", datetime.now(UTC), ())
        assert result.emails == ()
        assert transport.keys == ["key-one", "key-two"]

    asyncio.run(scenario())


def test_schema_and_parser_preserve_incident_correlation_and_impact() -> None:
    properties = EXTRACTION_SCHEMA["properties"]
    assert isinstance(properties, dict)
    emails_schema = properties["emails"]
    assert isinstance(emails_schema, dict)
    email_items = emails_schema["items"]
    assert isinstance(email_items, dict)
    email_properties = email_items["properties"]
    assert isinstance(email_properties, dict)
    actions_schema = email_properties["actionItems"]
    assert isinstance(actions_schema, dict)
    action_items = actions_schema["items"]
    assert isinstance(action_items, dict)
    assert "explicitBlocker" in action_items["required"]
    assert "relatedMessageIds" in action_items["required"]

    result = _parse_batch(
        {
            "emails": [
                {
                    "providerMessageId": "build-message",
                    "classification": "actionable",
                    "classificationReason": "Build production bị chặn.",
                    "actionItems": [
                        {
                            "title": "Xử lý build production HR-Chatbot",
                            "summary": "Build production thất bại.",
                            "deadlineText": None,
                            "deadlineSource": "none",
                            "required": True,
                            "explicitBlocker": True,
                            "impact": "production_blocked",
                            "incidentKey": "railway:eloquent-victory:hr-chatbot:production",
                            "relatedMessageIds": ["build-message", "volume-message"],
                            "actionPlan": [
                                {
                                    "instruction": "Mở build logs để tìm lỗi đầu tiên.",
                                    "basis": "email",
                                },
                                {
                                    "instruction": (
                                        "// a single parseable JSON array. "
                                        "Follow the schema provided in the context. "
                                        "Unread Email To-Do Summarizer "
                                        "relatedMessageIds"
                                    ),
                                    "basis": "suggestion",
                                },
                                {
                                    "instruction": "Sửa lỗi và chạy lại bản build để xác minh.",
                                    "basis": "suggestion",
                                }
                            ],
                            "evidence": [
                                {
                                    "sourceKind": "email_body",
                                    "filename": None,
                                    "location": None,
                                    "excerpt": "Build failed!",
                                    "sourceMessageId": "build-message",
                                }
                            ],
                            "confidence": "high",
                        }
                    ],
                }
            ]
        }
    )

    action = result.emails[0].action_items[0]
    assert action.explicit_blocker is True
    assert action.impact == "production_blocked"
    assert action.related_message_ids == ("build-message", "volume-message")
    assert action.evidence[0].source_message_id == "build-message"
    assert [step.instruction for step in action.action_plan] == [
        "Mở build logs để tìm lỗi đầu tiên.",
        "Sửa lỗi và chạy lại bản build để xác minh.",
    ]
