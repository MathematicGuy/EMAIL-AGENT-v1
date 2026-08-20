"""Tests for the pure, metadata-only Email Intent report builder."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tests.unit.scripts.cli_harness import load_script, run_cli

pytestmark = pytest.mark.extended

RUBRIC_VERSION = "email-intent-annotation-v1"


def load_module():
    return load_script("build_email_evaluation_report")


def _ground_truth(index: int) -> dict[str, object]:
    if index == 1:
        return {
            "actionability": "action_required",
            "email_is_sufficient": False,
            "knowledge_gaps": ["private gap phrase"],
            "expected_document_types": ["company_policy"],
            "expected_route": "retrieve_rag",
            "rationale": "Synthetic rationale that must not be rendered.",
        }
    return {
        "actionability": "informational",
        "email_is_sufficient": True,
        "knowledge_gaps": [],
        "expected_document_types": [],
        "expected_route": "no_action",
        "rationale": "Another synthetic rationale that must not be rendered.",
    }


def golden_fixture(case_count: int = 2) -> dict[str, object]:
    cases = []
    for index in range(1, case_count + 1):
        cases.append(
            {
                "case_id": f"email_case_{index:03d}",
                "source_message_id": f"synthetic-source-id-{index:03d}",
                "ground_truth": _ground_truth(index),
                "annotation": {
                    "source": "human_reviewed",
                    "rubric_version": RUBRIC_VERSION,
                    "reviewed_at": "2026-08-19T00:00:00Z",
                },
            }
        )
    return {
        "schema_version": 1,
        "rubric_version": RUBRIC_VERSION,
        "case_count": case_count,
        "cases": cases,
    }


def run_fixture(
    *, dataset_fingerprint: str, case_count: int = 2
) -> dict[str, object]:
    cases = []
    for index in range(1, case_count + 1):
        is_first = index == 1
        cases.append(
            {
                "case_id": f"email_case_{index:03d}",
                "prediction": {
                    "actionability": "action_required" if is_first else "informational",
                    "email_is_sufficient": not is_first,
                    "knowledge_gaps": [] if not is_first else ["private gap phrase"],
                    "retrieval_query": None if not is_first else "private query phrase",
                    "expected_document_types": [] if not is_first else ["company_policy"],
                    "confidence": 0.93,
                    "source_status": (
                        "model_prediction" if is_first else "classifier_fallback"
                    ),
                },
                "routing": {
                    "resolved_route": (
                        "retrieve_rag" if is_first else "direct_plan"
                    ),
                    "reason_codes": [],
                },
            }
        )
    return {
        "schema_version": 1,
        "run_id": "email-intent-2026-08-19-synthetic-shard-01",
        "created_at": "2026-08-19T00:00:00Z",
        "dataset_fingerprint": dataset_fingerprint,
        "rubric_version": RUBRIC_VERSION,
        "provider": "openrouter",
        "model": "synthetic-model",
        "prompt_version": "email-intent-v1",
        "shard": {"index": 1, "count": 1, "case_count": case_count},
        "cases": cases,
    }


def test_report_compares_run_without_mutating_inputs() -> None:
    module = load_module()
    golden = golden_fixture()
    run = run_fixture(dataset_fingerprint=module.dataset_fingerprint(golden))
    before = copy.deepcopy((golden, run))

    metrics = module.compare_run_to_golden(golden, run)

    assert metrics["route_accuracy"] == {"correct": 1, "total": 2}
    assert metrics["actionability_accuracy"] == {"correct": 2, "total": 2}
    assert metrics["fallback_cases"] == {"count": 1, "total": 2}
    assert (golden, run) == before


def test_report_rejects_a_mismatched_dataset_fingerprint() -> None:
    module = load_module()
    golden = golden_fixture()
    run = run_fixture(dataset_fingerprint="sha256:wrong-fingerprint")

    with pytest.raises(ValueError, match="fingerprint"):
        module.compare_run_to_golden(golden, run)


def test_report_rejects_an_unknown_run_case_id() -> None:
    module = load_module()
    golden = golden_fixture()
    run = run_fixture(dataset_fingerprint=module.dataset_fingerprint(golden))
    run["cases"][1]["case_id"] = "email_case_unknown"

    with pytest.raises(ValueError, match="unknown.*case_id"):
        module.compare_run_to_golden(golden, run)


def test_report_rejects_duplicate_run_case_ids() -> None:
    module = load_module()
    golden = golden_fixture()
    run = run_fixture(dataset_fingerprint=module.dataset_fingerprint(golden))
    run["cases"][1]["case_id"] = run["cases"][0]["case_id"]

    with pytest.raises(ValueError, match="duplicate case_id"):
        module.compare_run_to_golden(golden, run)


def test_report_rejects_runs_over_fifty_cases() -> None:
    module = load_module()
    golden = golden_fixture(case_count=51)
    run = run_fixture(
        dataset_fingerprint=module.dataset_fingerprint(golden), case_count=51
    )

    with pytest.raises(ValueError, match="maximum.*50"):
        module.compare_run_to_golden(golden, run)


def test_rendered_report_contains_meanings_but_no_private_fields_or_values() -> None:
    module = load_module()
    golden = golden_fixture()
    run = run_fixture(dataset_fingerprint=module.dataset_fingerprint(golden))
    report = module.render_report(module.compare_run_to_golden(golden, run))

    for meaning in (
        "the email explicitly obligates or directly asks the user to act.",
        "action could benefit the user, but it is optional.",
        "useful information with no requested or necessary action.",
        "unrelated, promotional, noisy, or not useful enough to create an action.",
        "the intent or required action cannot be determined confidently from the email.",
    ):
        assert meaning in report

    for forbidden in (
        "Synthetic Sender",
        "Synthetic subject",
        "synthetic-source-id",
        "Synthetic rationale",
        "private gap phrase",
        "private query phrase",
        "private email body",
        "sender",
        "subject",
        "gmail",
        "rationale",
        "gap",
        "query",
        "content",
    ):
        assert forbidden.lower() not in report.lower()


def test_cli_writes_report_only_for_a_compatible_pair(tmp_path: Path) -> None:
    module = load_module()
    golden = golden_fixture()
    run = run_fixture(dataset_fingerprint=module.dataset_fingerprint(golden))
    golden_path = tmp_path / "golden.json"
    run_path = tmp_path / "run.json"
    output_path = tmp_path / "reports" / "EMAIL-EVALUATION-REPORT.md"
    golden_path.write_text(json.dumps(golden), encoding="utf-8")
    run_path.write_text(json.dumps(run), encoding="utf-8")

    result = run_cli(
        "build_email_evaluation_report",
        "--golden",
        str(golden_path),
        "--run",
        str(run_path),
        "--output",
        str(output_path),
    )

    assert result.returncode == 0
    assert output_path.exists()
    assert "email_case_001" not in output_path.read_text(encoding="utf-8")


def test_cli_does_not_write_report_for_an_incompatible_pair(tmp_path: Path) -> None:
    golden = golden_fixture()
    run = run_fixture(dataset_fingerprint="sha256:wrong-fingerprint")
    golden_path = tmp_path / "golden.json"
    run_path = tmp_path / "run.json"
    output_path = tmp_path / "reports" / "EMAIL-EVALUATION-REPORT.md"
    golden_path.write_text(json.dumps(golden), encoding="utf-8")
    run_path.write_text(json.dumps(run), encoding="utf-8")

    result = run_cli(
        "build_email_evaluation_report",
        "--golden",
        str(golden_path),
        "--run",
        str(run_path),
        "--output",
        str(output_path),
    )

    assert result.returncode == 2
    assert not output_path.exists()
