"""Chat routing evaluation: default output location, metric math, dry-run report."""

import json
from pathlib import Path

from tests.unit.scripts.cli_harness import load_script, run_cli

REPO_ROOT = Path(__file__).resolve().parents[3]


def _module():
    return load_script("evaluate_chat_routing")


def _result(
    case_id: str,
    *,
    expected_rag: bool = False,
    predicted_rag: bool = False,
    expected_tool: bool = False,
    predicted_tool: bool = False,
    expected_route: str = "chat",
    predicted_route: str = "chat",
    latency_ms: int = 100,
):
    return _module().ChatRoutingEvalResult(
        case_id=case_id,
        expected_needs_rag=expected_rag,
        predicted_needs_rag=predicted_rag,
        expected_needs_tool=expected_tool,
        predicted_needs_tool=predicted_tool,
        expected_route=expected_route,
        predicted_route=predicted_route,
        latency_ms=latency_ms,
    )


def test_default_output_directory_stays_under_documented_evaluations_store() -> None:
    assert _module().DEFAULT_OUTPUT_DIR == REPO_ROOT / "evaluations" / "CHAT"


def test_chat_routing_dry_run_passes_and_report_is_metadata_only(tmp_path: Path) -> None:
    output = tmp_path / "chat-routing-eval"
    result = run_cli("evaluate_chat_routing", "--dry-run", "--output-dir", str(output))

    assert result.returncode == 0, result.stderr
    reports = list(output.glob("chat-routing-eval-*.json"))
    assert len(reports) == 1
    payload = reports[0].read_text(encoding="utf-8")
    report = json.loads(payload)
    assert report["case_count"] >= 64
    assert report["passed"] is True
    assert "current_message" not in payload
    assert "ready_document_titles" not in payload
    assert "recent_turns" not in payload


def test_the_dry_run_routes_the_labelled_tool_cases_to_tool() -> None:
    """The axis is on and the name is registered, so nothing narrows them away.

    This is what the four merged §11 cases exist to prove; before the axis was
    passed in, a correct classifier scored `chat` and `rag` on these two.
    """

    module = _module()
    cases = module.load_chat_routing_cases()
    expected_tool = {case.id for case in cases if case.labels.expected_needs_tool}

    assert expected_tool == {"cr-061", "cr-063"}


def test_chat_routing_metric_math_counts_missed_rag() -> None:
    module = _module()
    results = (
        _result(
            "a", expected_rag=True, predicted_rag=True, expected_route="rag", predicted_route="rag"
        ),
        _result("b", expected_rag=True, predicted_rag=False, expected_route="rag", latency_ms=200),
        _result("c", predicted_rag=True, predicted_route="rag", latency_ms=300),
    )

    metrics = module.compute_chat_routing_metrics(results)

    assert metrics.retrieval_recall == 0.5
    assert metrics.retrieval_precision == 0.5
    assert metrics.missed_rag_rate == 0.5
    assert metrics.missed_case_ids == ("b",)


def test_a_tool_case_routed_to_chat_is_reported_but_does_not_fail_the_gate() -> None:
    """Recall is measured, not gated -- see `ChatRoutingMetrics.passed`."""

    module = _module()
    results = (
        _result("a", expected_tool=True, expected_route="tool", predicted_route="chat"),
        _result("b"),
    )

    metrics = module.compute_chat_routing_metrics(results)

    assert metrics.tool_recall == 0.0
    assert metrics.missed_tool_case_ids == ("a",)
    assert metrics.tool_precision == 1.0
    assert metrics.passed is True


def test_a_chat_case_routed_to_tool_fails_the_gate() -> None:
    """The direction that writes to a real calendar is the one that is gated."""

    module = _module()
    results = (
        _result("a", predicted_tool=True, predicted_route="tool"),
        _result(
            "b",
            expected_tool=True,
            predicted_tool=True,
            expected_route="tool",
            predicted_route="tool",
        ),
    )

    metrics = module.compute_chat_routing_metrics(results)

    assert metrics.false_tool_case_ids == ("a",)
    assert metrics.tool_precision == 0.5
    assert metrics.passed is False


def test_a_rag_tool_downgrade_is_named_rather_than_scored_as_a_missed_retrieval() -> None:
    """`finalize_route` drops the retrieval half of a RAG_TOOL turn by design.

    Counting it as a missed retrieval would park a permanent entry in
    `missed_case_ids` that no classifier change could ever clear.
    """

    module = _module()
    results = (
        _result(
            "cr-063",
            expected_rag=True,
            predicted_rag=False,
            expected_tool=True,
            predicted_tool=True,
            expected_route="tool",
            predicted_route="tool",
        ),
        _result(
            "a", expected_rag=True, predicted_rag=True, expected_route="rag", predicted_route="rag"
        ),
    )

    metrics = module.compute_chat_routing_metrics(results)

    assert metrics.rag_tool_downgraded_case_ids == ("cr-063",)
    assert metrics.missed_case_ids == ()
    assert metrics.retrieval_recall == 1.0
    assert metrics.missed_rag_rate == 0.0
