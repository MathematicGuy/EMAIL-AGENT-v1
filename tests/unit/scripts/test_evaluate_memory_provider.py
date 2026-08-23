"""Provider selection for the memory evaluation harness.

The harness was hardwired to Gemini: `GeminiChatReply` was constructed
unconditionally and the report's `provider` field was the string literal
"gemini". When every Gemini key is quota-exhausted no run is possible at all,
and under any other provider the report would misname what produced it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts.evaluate_memory import _build_chat_reply, _default_provider
from tests.unit.scripts.cli_harness import load_script, run_cli


def _probe_set_file(tmp_path: Path) -> Path:
    payload = {
        "schema_version": "2.0.0",
        "probe_set_id": "unit",
        "label": "unit",
        "seed": {"short_term": ["a turn"], "long_term": {}, "episodic": [], "semantic": None},
        "probes": [
            {
                "id": "st_recall_01",
                "targets": "short_term",
                "test": "recall",
                "question": "what did I say?",
                "expect_any": ["a turn"],
            }
        ],
    }
    path = tmp_path / "probes.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_default_provider_follows_llm_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    assert _default_provider(dict(os.environ)) == "openrouter"


def test_default_provider_falls_back_to_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert _default_provider(dict(os.environ)) == "gemini"


def test_openrouter_reports_its_own_model() -> None:
    # The recorded model must be the one that actually answered. A run labelled
    # with another provider's model cannot be compared against anything.
    environ = {"OPENROUTER_API_KEY": "sk-or-test", "OPENROUTER_MODEL": "vendor/model-under-test"}
    _reply, provider, model = _build_chat_reply("openrouter", environ)
    assert provider == "openrouter"
    assert model == "vendor/model-under-test"


def test_mimo_reports_its_own_model() -> None:
    environ = {"MIMO_API_KEY": "tp-mimo-test", "MIMO_MODEL": "mimo-v2.5-pro"}
    _reply, provider, model = _build_chat_reply("mimo", environ)
    assert provider == "mimo"
    assert model == "mimo-v2.5-pro"


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        _build_chat_reply("no-such-provider", {})


def test_a_provider_missing_its_key_is_rejected() -> None:
    # Same contract as the Gemini path: no usable model means no run, and the
    # check has to follow the provider that was actually selected. The environ
    # is explicit so the real `.env` cannot put the key back and turn this into
    # a billed run.
    with pytest.raises(ValueError):
        _build_chat_reply("openrouter", {"OPENROUTER_MODEL": "vendor/model-under-test"})


def test_selecting_a_provider_without_its_key_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import evaluate_memory

    # Patch the module's own view of the environment rather than deleting the
    # key: `from_env` reloads `.env`, so a deleted key comes straight back.
    monkeypatch.setattr(
        evaluate_memory.os, "environ", {"OPENROUTER_MODEL": "vendor/model-under-test"}
    )
    code = evaluate_memory.main(
        ["--provider", "openrouter", "--probe-set", str(_probe_set_file(tmp_path))]
    )
    assert code == 1


@pytest.mark.parametrize("provider", ("gemini", "openrouter", "vyce", "vyne"))
def test_non_mistral_parallel_workers_are_rejected_before_environment_probe(
    provider: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = load_script("evaluate_memory")
    monkeypatch.setattr(
        script,
        "probe_environment",
        lambda environ: (_ for _ in ()).throw(AssertionError("would spend")),
    )

    result = run_cli(
        "evaluate_memory",
        "--provider",
        provider,
        "--max-workers",
        "2",
        "--probe-set",
        str(_probe_set_file(tmp_path)),
    )

    assert result.returncode == 2
    assert "--max-workers is only supported for provider=mistral" in result.stderr
