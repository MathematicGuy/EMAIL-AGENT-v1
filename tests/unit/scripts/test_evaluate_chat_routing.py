"""Chat routing evaluation: default output location, metric math, dry-run report."""

import json
from pathlib import Path

from tests.unit.scripts.cli_harness import load_script, run_cli


def _module():
    return load_script("evaluate_chat_routing")


def test_default_output_directory_stays_under_documented_evaluations_store() -> None:
    assert _module().DEFAULT_OUTPUT_DIR == Path("docs/evaluations/CHAT")


def test_chat_routing_dry_run_passes_and_report_is_metadata_only(tmp_path: Path) -> None:
    output = tmp_path / "chat-routing-eval"
    result = run_cli("evaluate_chat_routing", "--dry-run", "--output-dir", str(output))

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
    module = _module()
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
