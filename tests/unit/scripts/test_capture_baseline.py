"""Smoke test: baseline capture script dry-run works without provider keys."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "capture_baseline.py"


def test_help_runs_without_provider_keys() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0
    assert "dry-run" in result.stdout


def test_dry_run_writes_report_without_provider_keys() -> None:
    output_dir = Path(tempfile.mkdtemp(prefix="baseline-smoke-"))
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env["LLM_PROVIDER"] = "gemini"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", "--output-dir", str(output_dir)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    reports = list(output_dir.glob("combined-extractor-baseline-*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["provider"] == "dry-run-fake"
    assert report["summary"]["case_count"] >= 25
    assert report["summary"]["agreement_rate"] == 1.0
    assert all(set(case) == {
        "id",
        "expected_actionability",
        "predicted_classification",
        "agreement",
        "latency_ms",
    } for case in report["cases"])
