"""Shared OpenAI-compatible request/response helpers."""

from __future__ import annotations

import json
from collections.abc import Mapping
from urllib.request import Request

import pytest

from cowork_agent.integrations.llm.providers.openai_transport import (
    openai_completion_json,
    openai_request_body,
    post_json,
)

_SCHEMA: Mapping[str, object] = {"type": "object", "properties": {"ok": {"type": "boolean"}}}


def test_request_body_omits_models_and_embeds_schema_in_user_content() -> None:
    body = openai_request_body("model-x", "sys", "prompt", _SCHEMA, 128)

    assert body["model"] == "model-x"
    assert body["temperature"] == 0.0
    assert body["max_tokens"] == 128
    assert "models" not in body
    messages = body["messages"]
    assert isinstance(messages, list)
    assert messages[0]["content"] == "sys"
    assert json.dumps(_SCHEMA, ensure_ascii=False) in messages[1]["content"]


def test_request_body_includes_fallback_models_and_openrouter_temperature() -> None:
    body = openai_request_body(
        "primary",
        "sys",
        "prompt",
        _SCHEMA,
        64,
        temperature=0.7,
        fallback_models=("fallback-a",),
    )

    assert body["temperature"] == 0.7
    assert body["models"] == ["fallback-a"]


def test_request_body_schema_in_system_and_task_proposal_guard() -> None:
    prompt = '{"task_proposal_requested": true}'
    body = openai_request_body(
        "model-x",
        "sys",
        prompt,
        _SCHEMA,
        32,
        schema_in_system=True,
        task_proposal_guard=True,
    )
    messages = body["messages"]
    assert isinstance(messages, list)
    assert "CRITICAL JSON FORMAT REQUIREMENT" in messages[0]["content"]
    assert "task_proposal with a full object" in messages[1]["content"]


def test_completion_json_parses_object() -> None:
    payload = openai_completion_json(
        {"choices": [{"message": {"content": '{"ok": true}'}}]},
        error_cls=RuntimeError,
        missing_completion="missing",
        invalid_json="invalid",
        not_object="not-object",
    )
    assert payload == {"ok": True}


def test_completion_json_coerces_plain_text() -> None:
    payload = openai_completion_json(
        {"choices": [{"message": {"content": "Xin chào"}}]},
        error_cls=RuntimeError,
        missing_completion="missing",
        invalid_json="invalid",
        not_object="not-object",
        coerce_plain_text=True,
        empty_response="empty",
    )
    assert payload["assistant_text"] == "Xin chào"
    assert payload["citation_ids"] == []
    assert payload["task_proposal"] is None


def test_completion_json_strips_markdown_fences_when_coercing() -> None:
    payload = openai_completion_json(
        {
            "choices": [
                {
                    "message": {
                        "content": (
                            '```json\n{"assistant_text": "Xin chào", "task_proposal": null}\n```'
                        )
                    }
                }
            ]
        },
        error_cls=RuntimeError,
        missing_completion="missing",
        invalid_json="invalid",
        not_object="not-object",
        coerce_plain_text=True,
        empty_response="empty",
    )
    assert payload["assistant_text"] == "Xin chào"
    assert payload["conversation_title"] == "Phản hồi"


def test_post_json_sends_bearer_and_extra_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"ok": true}'

    def fake_urlopen(request: Request, timeout: int) -> FakeResponse:
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads((request.data or b"").decode())
        return FakeResponse()

    monkeypatch.setattr(
        "cowork_agent.integrations.llm.providers.openai_transport.urlopen", fake_urlopen
    )

    payload = post_json(
        "https://example.test/v1/chat/completions",
        "secret",
        {"model": "x"},
        9,
        extra_headers={"X-Title": "Cowork"},
        user_agent="module-mail/0.1.0",
    )

    assert payload == {"ok": True}
    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["timeout"] == 9
    assert captured["body"] == {"model": "x"}
    headers = captured["headers"]
    assert isinstance(headers, dict)
    header_names = {str(name).lower(): value for name, value in headers.items()}
    assert header_names["authorization"] == "Bearer secret"
    assert header_names["x-title"] == "Cowork"
    assert header_names["user-agent"] == "module-mail/0.1.0"
