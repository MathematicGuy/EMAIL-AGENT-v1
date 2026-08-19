"""Tests for proposal enrichment and the local Email annotation reviewer."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from tests.unit.scripts.cli_harness import load_script, run_cli

REPO_ROOT = Path(__file__).resolve().parents[3]
REVIEW_HTML = REPO_ROOT / "evaluations" / "EMAIL" / "review" / "review_annotations.html"
RUBRIC_VERSION = "email-intent-annotation-v1"


def load_module():
    return load_script("review_email_annotations")


def ground_truth(
    *,
    actionability: str = "action_required",
    email_is_sufficient: bool = False,
    knowledge_gaps: list[str] | None = None,
    expected_document_types: list[str] | None = None,
    expected_route: str = "retrieve_rag",
) -> dict[str, object]:
    return {
        "actionability": actionability,
        "email_is_sufficient": email_is_sufficient,
        "knowledge_gaps": knowledge_gaps if knowledge_gaps is not None else ["Missing policy"],
        "expected_document_types": (
            expected_document_types if expected_document_types is not None else ["company_policy"]
        ),
        "expected_route": expected_route,
        "rationale": "Synthetic annotation rationale.",
    }


def candidate_case(index: int) -> dict[str, object]:
    return {
        "case_id": f"email_case_{index:03d}",
        "source_message_id": f"synthetic-message-{index}",
        "gmail_thread_id": f"synthetic-thread-{index}",
        "sender": "Synthetic Sender <synthetic@example.com>",
        "subject": f"Synthetic subject {index}",
        "received_at": "2026-08-19T00:00:00Z",
        "labels": ["INBOX"],
        "gmail_content": "Synthetic private body for contract testing.",
    }


def candidates(case_count: int = 1) -> dict[str, object]:
    return {
        "schema_version": 1,
        "fetched_at": "2026-08-19T00:00:00Z",
        "gmail_query": "in:inbox",
        "ordering": "received_at_desc",
        "case_count": case_count,
        "cases": [candidate_case(index) for index in range(1, case_count + 1)],
    }


def proposal_case(
    index: int = 1,
    *,
    actionability: str = "action_required",
    email_is_sufficient: bool = False,
    knowledge_gaps: list[str] | None = None,
    expected_document_types: list[str] | None = None,
    expected_route: str = "retrieve_rag",
) -> dict[str, object]:
    return {
        "case_id": f"email_case_{index:03d}",
        "source_message_id": f"synthetic-message-{index}",
        "proposed_ground_truth": ground_truth(
            actionability=actionability,
            email_is_sufficient=email_is_sufficient,
            knowledge_gaps=knowledge_gaps,
            expected_document_types=expected_document_types,
            expected_route=expected_route,
        ),
        "resolver_expected_route": expected_route,
        "consistency_status": "consistent",
        "selection_reason": "Synthetic route-diverse calibration case.",
        "review_status": "pending",
    }


def proposal_batch(*proposal_cases: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "rubric_version": RUBRIC_VERSION,
        "case_count": len(proposal_cases),
        "cases": list(proposal_cases),
    }


def reviewed_case(
    index: int = 1,
    *,
    final: dict[str, object] | None = None,
    review_status: str = "accepted",
) -> dict[str, object]:
    proposal = ground_truth()
    return {
        "case_id": f"email_case_{index:03d}",
        "source_message_id": f"synthetic-message-{index}",
        "proposal": proposal,
        "final": deepcopy(final if final is not None else proposal),
        "review_status": review_status,
    }


def review_export(
    case_count: int = 70,
    *,
    corrected_actionability: int = 0,
    corrected_route: int = 0,
    corrected_case_ids: tuple[str, ...] = (),
    conflict_case_ids: tuple[str, ...] = (),
    systematic_errors_resolved: bool = True,
) -> dict[str, object]:
    actionability_ids = {
        f"email_case_{index:03d}"
        for index in range(1, corrected_actionability + 1)
    }
    route_ids = {
        f"email_case_{index:03d}"
        for index in range(
            corrected_actionability + 1,
            corrected_actionability + corrected_route + 1,
        )
    }
    corrected_ids = actionability_ids | route_ids | set(corrected_case_ids)
    conflict_ids = set(conflict_case_ids)
    cases: list[dict[str, object]] = []
    for index in range(1, case_count + 1):
        case_id = f"email_case_{index:03d}"
        final = ground_truth()
        if case_id in actionability_ids:
            final["actionability"] = "action_suggested"
        if case_id in route_ids:
            final.update(
                {
                    "email_is_sufficient": True,
                    "knowledge_gaps": [],
                    "expected_document_types": [],
                    "expected_route": "direct_plan",
                }
            )
        if case_id in conflict_ids:
            final["expected_route"] = "direct_plan"
        cases.append(
            reviewed_case(
                index,
                final=final,
                review_status=(
                    "corrected"
                    if case_id in corrected_ids or case_id in conflict_ids
                    else "accepted"
                ),
            )
        )
    return {
        "schema_version": 1,
        "rubric_version": RUBRIC_VERSION,
        "reviewed_at": "2026-08-19T00:00:00Z",
        "systematic_errors_resolved": systematic_errors_resolved,
        "case_count": case_count,
        "cases": cases,
    }


def second_pass(*, case_ids: tuple[str, ...]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "rubric_version": RUBRIC_VERSION,
        "case_count": len(case_ids),
        "case_ids": list(case_ids),
    }


def test_route_conflict_is_preserved_and_requires_review() -> None:
    module = load_module()
    proposal = proposal_case(
        actionability="action_required",
        email_is_sufficient=False,
        knowledge_gaps=["Missing policy"],
        expected_route="direct_plan",
    )

    enriched = module.validate_and_enrich_proposals(candidates(), proposal_batch(proposal))
    case = enriched["cases"][0]

    assert case["proposed_ground_truth"]["expected_route"] == "direct_plan"
    assert case["resolver_expected_route"] == "retrieve_rag"
    assert case["consistency_status"] == "needs_review"


def test_proposal_count_cannot_exceed_seventy() -> None:
    module = load_module()
    proposal_cases = tuple(proposal_case(index) for index in range(1, 72))

    with pytest.raises(ValueError, match="70"):
        module.validate_and_enrich_proposals(candidates(71), proposal_batch(*proposal_cases))


def test_proposals_must_join_candidate_case_and_source_ids_exactly() -> None:
    module = load_module()
    proposal = proposal_batch(proposal_case())
    proposal["cases"][0]["source_message_id"] = "different-source-message"

    with pytest.raises(ValueError, match="source_message_id"):
        module.validate_and_enrich_proposals(candidates(), proposal)


def test_proposals_reject_private_content_recursively() -> None:
    module = load_module()
    proposal = proposal_batch(proposal_case())
    proposal["cases"][0]["proposed_ground_truth"]["gmail_content"] = (
        "Synthetic private body must not cross the proposal boundary."
    )

    with pytest.raises(ValueError, match="gmail_content"):
        module.validate_and_enrich_proposals(candidates(), proposal)


def test_enrichment_records_actual_route_distribution_without_candidate_content() -> None:
    module = load_module()
    proposals = proposal_batch(
        proposal_case(
            1,
            actionability="informational",
            email_is_sufficient=True,
            knowledge_gaps=[],
            expected_document_types=[],
            expected_route="no_action",
        ),
        proposal_case(
            2,
            email_is_sufficient=True,
            knowledge_gaps=[],
            expected_document_types=[],
            expected_route="direct_plan",
        ),
        proposal_case(3),
    )

    enriched = module.validate_and_enrich_proposals(candidates(3), proposals)

    assert enriched["metadata"]["route_distribution"] == {
        "no_action": 1,
        "direct_plan": 1,
        "retrieve_rag": 1,
    }
    assert enriched["metadata"]["route_shortages"] == {
        "no_action": 23,
        "direct_plan": 22,
        "retrieve_rag": 22,
    }
    assert "gmail_content" not in json.dumps(enriched)


def test_review_page_is_local_and_contains_required_controls() -> None:
    html = REVIEW_HTML.read_text(encoding="utf-8")

    assert 'type="file"' in html
    assert html.count('type="file"') == 2
    assert "FileReader" in html
    assert "Map" in html
    assert "Blob" in html
    assert "fetch(" not in html
    assert "localStorage" not in html
    assert "gmail_content" not in html
    assert "reviewed_annotations.json" in html
    assert "Accept proposal" in html
    assert "Needs correction" in html
    for value in (
        "action_required",
        "action_suggested",
        "informational",
        "irrelevant",
        "unclear",
        "no_action",
        "direct_plan",
        "retrieve_rag",
        "pending",
        "accepted",
        "corrected",
    ):
        assert value in html
    assert 'id="completed-count"' in html
    assert 'id="remaining-count"' in html


def test_promotion_metrics_measure_unchanged_actionability_and_route() -> None:
    module = load_module()

    metrics = module.promotion_metrics(
        review_export(corrected_actionability=2, corrected_route=1)
    )

    assert metrics["review_count"] == 70
    assert metrics["actionability_agreement"] == {
        "unchanged": 68,
        "total": 70,
        "rate": 68 / 70,
    }
    assert metrics["route_agreement"] == {
        "unchanged": 69,
        "total": 70,
        "rate": 69 / 70,
    }
    assert metrics["corrected_case_ids"] == [
        "email_case_001",
        "email_case_002",
        "email_case_003",
    ]
    assert metrics["final_resolver_conflict_case_ids"] == []


def test_promotion_requires_seventy_reviews_and_ninety_percent_agreement() -> None:
    module = load_module()
    reviewed = review_export(case_count=70, corrected_actionability=8, corrected_route=7)

    with pytest.raises(ValueError, match="actionability agreement 88.6% is below 90.0%"):
        module.promote_reviewed_annotations(reviewed, second_pass(case_ids=()))


def test_promotion_requires_second_pass_for_every_corrected_case() -> None:
    module = load_module()
    reviewed = review_export(case_count=70, corrected_case_ids=("email_case_001",))

    with pytest.raises(ValueError, match="missing second-pass cases"):
        module.promote_reviewed_annotations(reviewed, second_pass(case_ids=()))


def test_promotion_rejects_unresolved_systematic_errors() -> None:
    module = load_module()
    reviewed = review_export(systematic_errors_resolved=False)

    with pytest.raises(ValueError, match="systematic errors"):
        module.promote_reviewed_annotations(reviewed, second_pass(case_ids=()))


def test_promotion_rejects_final_resolver_conflicts() -> None:
    module = load_module()
    reviewed = review_export(conflict_case_ids=("email_case_001",))

    with pytest.raises(ValueError, match="final resolver conflict"):
        module.promote_reviewed_annotations(
            reviewed, second_pass(case_ids=("email_case_001",))
        )


def test_successful_promotion_is_truth_only_and_human_reviewed() -> None:
    module = load_module()
    reviewed = review_export(corrected_case_ids=("email_case_001",))

    golden = module.promote_reviewed_annotations(
        reviewed,
        second_pass(case_ids=("email_case_001",)),
        reviewed_at="2026-08-20T00:00:00Z",
    )

    assert set(golden) == {"schema_version", "rubric_version", "case_count", "cases"}
    assert golden["case_count"] == 70
    assert set(golden["cases"][0]) == {
        "case_id",
        "source_message_id",
        "ground_truth",
        "annotation",
    }
    assert all(
        case["annotation"] == {
            "source": "human_reviewed",
            "rubric_version": RUBRIC_VERSION,
            "reviewed_at": "2026-08-20T00:00:00Z",
        }
        for case in golden["cases"]
    )
    serialized = json.dumps(golden)
    for forbidden in ("proposal", "comparison", "prediction", "gmail_content"):
        assert forbidden not in serialized


def test_validate_proposals_cli_reads_inputs_without_writing_output(tmp_path: Path) -> None:
    candidates_path = tmp_path / "candidates.json"
    proposals_path = tmp_path / "proposals.json"
    candidates_path.write_text(json.dumps(candidates()), encoding="utf-8")
    proposals_path.write_text(
        json.dumps(proposal_batch(proposal_case())), encoding="utf-8"
    )

    result = run_cli(
        "review_email_annotations",
        "validate-proposals",
        "--candidates",
        str(candidates_path),
        "--proposals",
        str(proposals_path),
    )

    assert result.returncode == 0
    assert "Validated 1 proposals" in result.stdout
    assert not (tmp_path / "golden_dataset.json").exists()


def test_promote_cli_validates_before_atomic_write_and_protects_existing_output(
    tmp_path: Path,
) -> None:
    reviewed_path = tmp_path / "reviewed.json"
    second_pass_path = tmp_path / "second-pass.json"
    output_path = tmp_path / "golden.json"
    reviewed_path.write_text(json.dumps(review_export()), encoding="utf-8")
    second_pass_path.write_text(json.dumps(second_pass(case_ids=())), encoding="utf-8")
    output_path.write_text('{"sentinel": true}\n', encoding="utf-8")

    result = run_cli(
        "review_email_annotations",
        "promote",
        "--reviewed",
        str(reviewed_path),
        "--second-pass",
        str(second_pass_path),
        "--output",
        str(output_path),
    )

    assert result.returncode == 2
    assert "--replace" in result.stderr
    assert output_path.read_text(encoding="utf-8") == '{"sentinel": true}\n'

    result = run_cli(
        "review_email_annotations",
        "promote",
        "--reviewed",
        str(reviewed_path),
        "--second-pass",
        str(second_pass_path),
        "--output",
        str(output_path),
        "--replace",
    )

    assert result.returncode == 0
    promoted = json.loads(output_path.read_text(encoding="utf-8"))
    assert promoted["case_count"] == 70
    assert "proposal" not in json.dumps(promoted)
