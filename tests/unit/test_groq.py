"""Tests for the Groq action extractor without calling the external API."""

import asyncio
from datetime import UTC, datetime
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from mail_todo.infrastructure.config import GroqSettings
from mail_todo.infrastructure.groq import GroqActionExtractor, GroqAPIError, _post_json


def test_groq_settings_default_to_requested_qwen_model() -> None:
    settings = GroqSettings.from_env({"GROQ_API_KEY": "test-key"}, load_env_file=False)

    assert settings.model == "qwen/qwen3.6-27b"
    assert "test-key" not in repr(settings)


def test_groq_settings_require_api_key() -> None:
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        GroqSettings.from_env({}, load_env_file=False)


def test_groq_extractor_requests_hidden_default_reasoning(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_post_json(
        url: str, api_key: str, body: dict[str, object], timeout_seconds: int
    ) -> dict[str, object]:
        captured.update(
            {"url": url, "api_key": api_key, "body": body, "timeout": timeout_seconds}
        )
        return {"choices": [{"message": {"content": '{"emails": []}'}}]}

    monkeypatch.setattr("mail_todo.infrastructure.groq._post_json", fake_post_json)

    async def scenario() -> None:
        extractor = GroqActionExtractor(
            GroqSettings.from_env({"GROQ_API_KEY": "test-key"}, load_env_file=False)
        )
        result = await extractor.extract("Asia/Ho_Chi_Minh", datetime.now(UTC), ())

        assert result.emails == ()

    asyncio.run(scenario())

    request_body = captured["body"]
    assert isinstance(request_body, dict)
    assert request_body["model"] == "qwen/qwen3.6-27b"
    assert request_body["reasoning_effort"] == "none"
    assert request_body["reasoning_format"] == "hidden"
    assert request_body["response_format"] == {"type": "json_object"}
    assert "relatedMessageIds" in request_body["messages"][1]["content"]


def test_groq_requests_include_a_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"{}"

    def fake_urlopen(request: Request, timeout: int) -> FakeResponse:
        captured["user_agent"] = request.get_header("User-agent")
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("mail_todo.infrastructure.groq.urlopen", fake_urlopen)

    assert _post_json("https://api.groq.com/test", "test-key", {}, 12) == {}
    assert captured == {"user_agent": "module-mail/0.1.0", "timeout": 12}


def test_groq_http_error_has_actionable_safe_message(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_urlopen(request: Request, timeout: int) -> None:
        raise HTTPError(request.full_url, 400, "Bad Request", {}, None)

    monkeypatch.setattr("mail_todo.infrastructure.groq.urlopen", fail_urlopen)

    with pytest.raises(GroqAPIError) as caught:
        _post_json("https://api.groq.com/test", "test-key", {}, 12)

    assert caught.value.error_code == "GROQ_API_ERROR"
    assert "HTTP 400" in caught.value.safe_message
    assert "reasoning" in caught.value.safe_message
