"""Tests for the Groq adapters' transport layer without calling the external API."""

from urllib.error import HTTPError
from urllib.request import Request

import pytest

from cowork_agent.config import GroqSettings
from cowork_agent.integrations.llm.providers.groq import (
    GroqAPIError,
    _post_json,
)


def test_groq_settings_default_to_requested_qwen_model() -> None:
    settings = GroqSettings.from_env({"GROQ_API_KEY": "test-key"}, load_env_file=False)

    assert settings.model == "qwen/qwen3.6-27b"
    assert "test-key" not in repr(settings)


def test_groq_settings_require_api_key() -> None:
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        GroqSettings.from_env({}, load_env_file=False)


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

    monkeypatch.setattr("cowork_agent.integrations.llm.providers.groq.urlopen", fake_urlopen)

    assert _post_json("https://api.groq.com/test", "test-key", {}, 12) == {}
    assert captured == {"user_agent": "module-mail/0.1.0", "timeout": 12}


def test_groq_http_error_has_actionable_safe_message(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_urlopen(request: Request, timeout: int) -> None:
        raise HTTPError(request.full_url, 400, "Bad Request", {}, None)

    monkeypatch.setattr("cowork_agent.integrations.llm.providers.groq.urlopen", fail_urlopen)

    with pytest.raises(GroqAPIError) as caught:
        _post_json("https://api.groq.com/test", "test-key", {}, 12)

    assert caught.value.error_code == "GROQ_API_ERROR"
    assert "HTTP 400" in caught.value.safe_message
    assert "reasoning" in caught.value.safe_message
