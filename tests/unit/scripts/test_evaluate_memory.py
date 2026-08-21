from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cowork_agent.features.ai_chat.memory_eval.probes import SeedSpec
from cowork_agent.features.ai_chat.memory_eval.runner import run_key
from scripts.evaluate_memory import main

pytestmark = pytest.mark.extended


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


def test_run_key_is_stable_for_the_same_inputs() -> None:
    seed = SeedSpec(("a",), {}, (), None)
    assert run_key("set", "model", seed) == run_key("set", "model", seed)


def test_run_key_changes_when_the_seed_changes() -> None:
    assert run_key("set", "model", SeedSpec(("a",), {}, (), None)) != run_key(
        "set", "model", SeedSpec(("b",), {}, (), None)
    )


def test_run_key_changes_when_the_model_changes() -> None:
    seed = SeedSpec(("a",), {}, (), None)
    assert run_key("set", "model-a", seed) != run_key("set", "model-b", seed)


def test_dry_run_writes_a_report_and_exits_zero(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    code = main(
        ["--dry-run", "--probe-set", str(_probe_set_file(tmp_path)), "--output", str(output)]
    )
    assert code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema_version"] == "2.1.0"
    assert report["probe_set_id"] == "unit"
    assert len(report["verdicts"]) == 1


def test_dry_run_report_contains_no_probe_text(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    main(["--dry-run", "--probe-set", str(_probe_set_file(tmp_path)), "--output", str(output)])
    assert "what did I say?" not in output.read_text(encoding="utf-8")


def test_an_invalid_probe_set_exits_two(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": "9.9.9"}), encoding="utf-8")
    assert main(["--dry-run", "--probe-set", str(bad)]) == 2


def test_a_missing_probe_set_exits_two(tmp_path: Path) -> None:
    assert main(["--dry-run", "--probe-set", str(tmp_path / "nope.json")]) == 2


@pytest.mark.live
def test_live_run_requires_a_database_and_key() -> None:
    pytest.skip("live tier: run manually with DATABASE_URL and a provider key set")


def test_a_live_run_without_a_gemini_key_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No model means no reply to score, so there is no run at all. This is the
    # one dependency whose absence is fatal rather than a per-scope finding.
    # Every GEMINI_API_KEY* name has to go: a .env sitting in the checkout
    # supplies numbered keys, and leaving one behind would turn this unit test
    # into a real billed run against a real model.
    for name in [item for item in os.environ if item.startswith("GEMINI_API_KEY")]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    assert main(["--provider", "gemini", "--probe-set", str(_probe_set_file(tmp_path))]) == 1


def test_dry_run_still_works_after_the_live_path_lands(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    assert (
        main(["--dry-run", "--probe-set", str(_probe_set_file(tmp_path)), "--output", str(output)])
        == 0
    )
