"""Smoke tests: routing evaluation metrics and dry-run plumbing (T2.6)."""

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "evaluate_routing.py"


def load_module():
    spec = importlib.util.spec_from_file_location("evaluate_routing_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations via sys.modules
    spec.loader.exec_module(module)
    return module


def _result(
    module,
    case_id: str,
    expected_route: str,
    predicted_route: str,
    expected_actionability: str = "action_required",
    predicted_actionability: str | None = None,
):
    return module.EvalCaseResult(
        case_id=case_id,
        expected_actionability=expected_actionability,
        predicted_actionability=(
            expected_actionability if predicted_actionability is None else predicted_actionability
        ),
        expected_route=expected_route,
        predicted_route=predicted_route,
    )


def test_help_runs_without_provider_keys() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0
    assert "dry-run" in result.stdout


def test_precision_recall_math_including_zero_division() -> None:
    module = load_module()
    results = (
        _result(module, "c1", "RETRIEVE_RAG", "RETRIEVE_RAG"),
        _result(module, "c2", "RETRIEVE_RAG", "DIRECT_PLAN"),
        _result(module, "c3", "DIRECT_PLAN", "RETRIEVE_RAG"),
        _result(module, "c4", "DIRECT_PLAN", "DIRECT_PLAN"),
    )
    confusion = module.route_confusion(results)
    assert confusion["RETRIEVE_RAG"] == {"NO_ACTION": 0, "DIRECT_PLAN": 1, "RETRIEVE_RAG": 1}
    assert confusion["DIRECT_PLAN"] == {"NO_ACTION": 0, "DIRECT_PLAN": 1, "RETRIEVE_RAG": 1}
    assert confusion["NO_ACTION"] == {"NO_ACTION": 0, "DIRECT_PLAN": 0, "RETRIEVE_RAG": 0}
    precision, recall = module.route_precision_recall(confusion)
    assert precision["RETRIEVE_RAG"] == 0.5
    assert recall["RETRIEVE_RAG"] == 0.5
    assert precision["DIRECT_PLAN"] == 0.5
    assert recall["DIRECT_PLAN"] == 0.5
    # NO_ACTION is neither expected nor predicted: zero-division yields None.
    assert precision["NO_ACTION"] is None
    assert recall["NO_ACTION"] is None


def test_actionability_agreement_accuracy() -> None:
    module = load_module()
    results = (
        _result(module, "c1", "DIRECT_PLAN", "DIRECT_PLAN"),
        _result(
            module,
            "c2",
            "DIRECT_PLAN",
            "DIRECT_PLAN",
            expected_actionability="action_required",
            predicted_actionability="action_suggested",
        ),
    )
    agreement_count, accuracy = module.actionability_agreement(results)
    assert agreement_count == 1
    assert accuracy == 0.5
    assert module.actionability_agreement(()) == (0, None)


def test_false_negative_retrieval_counts_missed_retrieval() -> None:
    module = load_module()
    results = (
        _result(module, "c1", "RETRIEVE_RAG", "RETRIEVE_RAG"),
        _result(module, "c2", "RETRIEVE_RAG", "DIRECT_PLAN"),
        _result(module, "c3", "RETRIEVE_RAG", "NO_ACTION"),
        _result(module, "c4", "DIRECT_PLAN", "DIRECT_PLAN"),
    )
    count, rate, case_ids = module.false_negative_retrieval(results)
    assert count == 2
    assert rate == round(2 / 3, 4)
    assert case_ids == ("c2", "c3")


def test_false_negative_retrieval_without_retrieval_labels() -> None:
    module = load_module()
    count, rate, case_ids = module.false_negative_retrieval(())
    assert count == 0
    assert rate is None
    assert case_ids == ()


def test_dry_run_flow_reports_perfect_scores_and_no_content() -> None:
    module = load_module()
    cases = module.load_routing_cases()
    classifier = module.build_dry_run_classifier(cases)
    results, batch_count = asyncio.run(module.evaluate(cases, classifier))
    assert batch_count == 1
    assert len(results) == len(cases)

    metrics = module.compute_metrics(results)
    assert metrics.agreement_count == len(cases)
    assert metrics.actionability_accuracy == 1.0
    for route in ("NO_ACTION", "DIRECT_PLAN", "RETRIEVE_RAG"):
        assert metrics.precision[route] == 1.0
        assert metrics.recall[route] == 1.0
    assert metrics.false_negative_count == 0
    assert metrics.false_negative_rate == 0.0

    report = module.build_report(results, metrics, "dry-run-fake", "dry-run-fake")
    case_ids = [case["case_id"] for case in report["per_case"]]
    assert "r-009" in case_ids  # the §14 false-negative-retrieval case is evaluated
    assert report["false_negative_retrieval"] == {"count": 0, "rate": 0.0, "case_ids": []}

    serialized = json.dumps(report, ensure_ascii=False)
    assert '"body"' not in serialized
    assert '"subject"' not in serialized
    for case in cases:
        assert case.subject not in serialized
        assert case.body not in serialized


def test_dry_run_writes_report_without_provider_keys() -> None:
    output_dir = Path(tempfile.mkdtemp(prefix="routing-eval-smoke-"))
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
    reports = list(output_dir.glob("routing-eval-*.json"))
    assert len(reports) == 1
    serialized = reports[0].read_text(encoding="utf-8")
    report = json.loads(serialized)
    assert report["provider"] == "dry-run-fake"
    assert report["case_count"] >= 25
    assert report["actionability_accuracy"]["accuracy"] == 1.0
    for route in ("NO_ACTION", "DIRECT_PLAN", "RETRIEVE_RAG"):
        assert report["precision"][route] == 1.0
        assert report["recall"][route] == 1.0
    assert report["false_negative_retrieval"] == {"count": 0, "rate": 0.0, "case_ids": []}
    assert {case["case_id"] for case in report["per_case"]} >= {"r-009"}
    assert '"body"' not in serialized
    assert '"subject"' not in serialized
