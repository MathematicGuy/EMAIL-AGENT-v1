"""Smoke tests: routing evaluation metrics and dry-run plumbing (T2.6)."""

import subprocess
import sys
from pathlib import Path

from tests.unit.scripts.cli_harness import load_script, run_cli

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "evaluate_routing.py"


def load_module():
    return load_script("evaluate_routing")


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


def test_routing_evaluation_metrics_and_confusion_matrix() -> None:
    module = load_module()
    assert module.DEFAULT_OUTPUT_DIR == REPO_ROOT / "evaluations" / "EMAIL" / "runs"

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
    assert precision["NO_ACTION"] is None

    agreement_count, accuracy = module.actionability_agreement(
        (
            _result(module, "c1", "DIRECT_PLAN", "DIRECT_PLAN"),
            _result(
                module,
                "c2",
                "DIRECT_PLAN",
                "DIRECT_PLAN",
                predicted_actionability="action_suggested",
            ),
        )
    )
    assert agreement_count == 1
    assert accuracy == 0.5


def test_routing_evaluation_cli_dry_run_and_help(tmp_path: Path) -> None:
    help_res = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, check=False
    )
    assert help_res.returncode == 0 and "dry-run" in help_res.stdout

    run_res = run_cli("evaluate_routing", "--dry-run", "--output-dir", str(tmp_path))
    assert run_res.returncode == 0
