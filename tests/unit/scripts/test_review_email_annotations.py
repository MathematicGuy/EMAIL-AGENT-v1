"""Tests for proposal enrichment and the local Email annotation reviewer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.unit.scripts.cli_harness import load_script

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
