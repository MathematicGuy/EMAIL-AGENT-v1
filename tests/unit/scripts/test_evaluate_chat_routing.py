import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts/evaluate_chat_routing.py"

def test_default_output_directory_stays_under_documented_evaluations_store() -> None:
    spec = importlib.util.spec_from_file_location("chat_eval_defaults", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    assert module.DEFAULT_OUTPUT_DIR == Path("docs/evaluations/CHAT")


def test_chat_routing_dry_run_passes_and_report_is_metadata_only() -> None:
    output = Path(tempfile.mkdtemp(prefix="chat-routing-eval-"))
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", "--output-dir", str(output)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    reports = list(output.glob("chat-routing-eval-*.json"))
    assert len(reports) == 1
    payload = reports[0].read_text(encoding="utf-8")
    report = json.loads(payload)
    assert report["case_count"] >= 60
    assert report["passed"] is True
    assert "current_message" not in payload
    assert "ready_document_titles" not in payload
    assert "recent_turns" not in payload


def test_chat_routing_metric_math_counts_missed_rag() -> None:
    spec = importlib.util.spec_from_file_location("chat_eval", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    results = (
        module.ChatRoutingEvalResult("a", True, True, "rag", "rag", 100),
        module.ChatRoutingEvalResult("b", True, False, "rag", "chat", 200),
        module.ChatRoutingEvalResult("c", False, True, "chat", "rag", 300),
    )

    metrics = module.compute_chat_routing_metrics(results)

    assert metrics.retrieval_recall == 0.5
    assert metrics.retrieval_precision == 0.5
    assert metrics.missed_rag_rate == 0.5
    assert metrics.missed_case_ids == ("b",)
