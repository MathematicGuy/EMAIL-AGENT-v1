"""Tests for the paired chat evaluation CLI script.

Runs the script main() in-process with monkeypatched env.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "run_paired_chat_evaluation.py"


def _load_module() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("run_paired_chat_evaluation_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_MOD = _load_module()
main = _MOD.main  # type: ignore[attr-defined]


def test_missing_thresholds_exits_with_code_2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing EVAL_* env vars -> SystemExit code 2."""
    for var in (
        "EVAL_MIN_CONTINUITY_DELTA",
        "EVAL_MIN_GROUNDED_DELTA",
        "EVAL_MIN_CITATION_DELTA",
        "EVAL_MIN_CONTINUITY_SCORE",
        "EVAL_MIN_GROUNDED_SCORE",
        "EVAL_MIN_CITATION_SCORE",
        "EVAL_MAX_DEGRADATION_RATE",
    ):
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 2


def test_trivial_pass_thresholds_exit_0(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Trivial-pass placeholder thresholds -> exit 0, zero safety counters."""
    # PRODUCT-OWNER APPROVAL REQUIRED — test-local trivial thresholds
    monkeypatch.setenv("EVAL_MIN_CONTINUITY_DELTA", "0.0")
    monkeypatch.setenv("EVAL_MIN_GROUNDED_DELTA", "0.0")
    monkeypatch.setenv("EVAL_MIN_CITATION_DELTA", "0.0")
    monkeypatch.setenv("EVAL_MIN_CONTINUITY_SCORE", "0.0")
    monkeypatch.setenv("EVAL_MIN_GROUNDED_SCORE", "0.0")
    monkeypatch.setenv("EVAL_MIN_CITATION_SCORE", "0.0")
    monkeypatch.setenv("EVAL_MAX_DEGRADATION_RATE", "1.0")

    # Gate passes -> main returns normally (no SystemExit)
    main(["--json"])

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    report = output["report"]
    # Hard-safety counters must all be zero
    assert report["unvalidated_retrievals"] == 0
    assert report["cross_tenant_incidents"] == 0
    assert report["raw_email_memory_violations"] == 0
    assert report["expired_record_retrievals"] == 0
    assert report["rejected_retrievals"] == 0
    assert output["gate"]["passed"] is True


def test_impossible_thresholds_exit_1_with_reason_codes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Impossible thresholds (minimum_continuity_delta=1.0) -> exit 1."""
    monkeypatch.setenv("EVAL_MIN_CONTINUITY_DELTA", "1.0")
    monkeypatch.setenv("EVAL_MIN_GROUNDED_DELTA", "0.0")
    monkeypatch.setenv("EVAL_MIN_CITATION_DELTA", "0.0")
    monkeypatch.setenv("EVAL_MIN_CONTINUITY_SCORE", "0.0")
    monkeypatch.setenv("EVAL_MIN_GROUNDED_SCORE", "0.0")
    monkeypatch.setenv("EVAL_MIN_CITATION_SCORE", "0.0")
    monkeypatch.setenv("EVAL_MAX_DEGRADATION_RATE", "1.0")

    with pytest.raises(SystemExit) as exc_info:
        main(["--json"])
    assert exc_info.value.code == 1

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["gate"]["passed"] is False
    assert "continuity_delta" in output["gate"]["reason_codes"]
