from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from cowork_agent.integrations.llm.providers.mistral import MistralAPIError

_SYNTHETIC_PROMPT = "Reply with the word ready. This is a synthetic key-independence smoke check."
_PRIVATE_REPLY = "private fake reply"


def _configure_keys(monkeypatch: pytest.MonkeyPatch, count: int) -> dict[str, str]:
    for name in tuple(os.environ):
        if name.startswith("MISTRAL_API_KEY"):
            monkeypatch.delenv(name)
    keys: dict[str, str] = {}
    for number in range(1, count + 1):
        name = "MISTRAL_API_KEY" if number == 1 else f"MISTRAL_API_KEY{number}"
        key = f"secret-smoke-key-{number}"
        monkeypatch.setenv(name, key)
        keys[key] = f"mistral-{number}"
    monkeypatch.setenv("MISTRAL_MODEL", "mistral-small-smoke")
    return keys


def _completion_response() -> dict[str, object]:
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "assistant_text": _PRIVATE_REPLY,
                            "conversation_title": "Smoke check",
                            "citation_ids": [],
                            "task_proposal": None,
                        }
                    )
                }
            }
        ]
    }


def test_smoke_requests_each_selected_alias_concurrently_caps_workers_and_redacts_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts import smoke_test_mistral_evaluation_keys as smoke

    aliases = _configure_keys(monkeypatch, 2)
    entered = threading.Barrier(2)
    active = 0
    max_active = 0
    lock = threading.Lock()
    seen_aliases: list[str] = []

    def fake_post_json(
        url: str, api_key: str, body: object, timeout_seconds: int
    ) -> dict[str, object]:
        del url, body, timeout_seconds
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
            seen_aliases.append(aliases[api_key])
        try:
            entered.wait(timeout=2)
            return _completion_response()
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(
        "cowork_agent.integrations.llm.providers.mistral._post_json", fake_post_json
    )
    output = tmp_path / "smoke.json"

    assert smoke.main(["--workers", "3", "--output", str(output)]) == 0

    report = json.loads(output.read_text(encoding="utf-8"))
    captured = capsys.readouterr()
    assert sorted(seen_aliases) == ["mistral-1", "mistral-2"]
    assert max_active == 2
    assert report["requested_workers"] == 3
    assert report["effective_workers"] == 2
    assert report["independence_demonstrated"] is True
    assert [request["alias"] for request in report["requests"]] == ["mistral-1", "mistral-2"]
    assert [request["status_class"] for request in report["requests"]] == [
        "succeeded",
        "succeeded",
    ]
    assert all(
        isinstance(request["latency_ms"], int) and request["latency_ms"] >= 0
        for request in report["requests"]
    )
    assert "WORKER_COUNT_REDUCED requested_workers=3 effective_workers=2" in captured.err
    public_output = output.read_text(encoding="utf-8") + captured.out + captured.err
    for value in (*aliases, _SYNTHETIC_PROMPT, _PRIVATE_REPLY):
        assert value not in public_output


def test_smoke_rejects_provider_wide_429_without_exposing_transport_details(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts import smoke_test_mistral_evaluation_keys as smoke

    aliases = _configure_keys(monkeypatch, 2)

    def fake_post_json(
        url: str, api_key: str, body: object, timeout_seconds: int
    ) -> dict[str, object]:
        del url, api_key, body, timeout_seconds
        raise MistralAPIError(
            "private fake 429 transport exception",
            status_code=429,
            retry_after_seconds=7,
        )

    monkeypatch.setattr(
        "cowork_agent.integrations.llm.providers.mistral._post_json", fake_post_json
    )
    output = tmp_path / "smoke.json"

    assert smoke.main(["--workers", "2", "--output", str(output)]) == 1

    report = json.loads(output.read_text(encoding="utf-8"))
    captured = capsys.readouterr()
    assert report["independence_demonstrated"] is False
    assert [request["status_class"] for request in report["requests"]] == [
        "rate_limited",
        "rate_limited",
    ]
    assert len(report["cross_key_429_timing"]) == 1
    assert report["cross_key_429_timing"][0]["first_alias"] == "mistral-1"
    assert report["cross_key_429_timing"][0]["second_alias"] == "mistral-2"
    assert report["cross_key_429_timing"][0]["delta_ms"] >= 0
    public_output = output.read_text(encoding="utf-8") + captured.out + captured.err
    for value in (*aliases, _SYNTHETIC_PROMPT, "private fake 429 transport exception"):
        assert value not in public_output


def test_help_runs_without_provider_keys() -> None:
    from tests.unit.scripts.cli_harness import run_cli

    assert run_cli("smoke_test_mistral_evaluation_keys", "--help").returncode == 0
