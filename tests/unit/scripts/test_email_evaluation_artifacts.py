"""Contract tests for the privacy-safe Email evaluation artifacts."""

from __future__ import annotations

import copy

import pytest

from tests.unit.scripts.cli_harness import load_script

PRIVATE_KEYS = {"gmail_content", "snippet", "normalized_body"}
RUBRIC_VERSION = "email-pipeline-annotation-v2"


def load_module():
    return load_script("email_evaluation_artifacts")


def _ground_truth() -> dict[str, object]:
    return {
        "actionability": "action_required",
        "email_is_sufficient": False,
        "knowledge_gaps": ["Synthetic missing policy"],
        "expected_document_types": ["company_policy"],
        "retrieval_expected": True,
        "company_context_required": True,
        "rationale": "Synthetic evaluation rationale.",
    }


def _candidate_case(index: int, case_count: int) -> dict[str, object]:
    return {
        "case_id": f"email_case_{index:03d}",
        "source_message_id": f"synthetic-message-{index}",
        "gmail_thread_id": f"synthetic-thread-{index}",
        "sender": "Synthetic Sender <synthetic@example.com>",
        "subject": f"Synthetic subject {index}",
        "received_at": f"2026-08-19T00:{case_count - index + 1:02d}:00Z",
        "labels": ["INBOX"],
        "gmail_content": "Synthetic private body for contract testing.",
    }


def valid_candidates(case_count: int = 2) -> dict[str, object]:
    return {
        "schema_version": 1,
        "fetched_at": "2026-08-19T00:00:00Z",
        "gmail_query": "in:inbox",
        "ordering": "received_at_desc",
        "case_count": case_count,
        "cases": [_candidate_case(index, case_count) for index in range(1, case_count + 1)],
    }


def _proposal_case(index: int = 1) -> dict[str, object]:
    return {
        "case_id": f"email_case_{index:03d}",
        "source_message_id": f"synthetic-message-{index}",
        "proposed_ground_truth": _ground_truth(),
        "resolver_expected_retrieval": True,
        "consistency_status": "consistent",
        "selection_reason": "Synthetic route-diverse calibration case.",
        "review_status": "pending",
    }


def valid_proposals(case_count: int = 1) -> dict[str, object]:
    return {
        "schema_version": 2,
        "rubric_version": RUBRIC_VERSION,
        "case_count": case_count,
        "cases": [_proposal_case(index) for index in range(1, case_count + 1)],
    }


def _review_case(index: int = 1) -> dict[str, object]:
    return {
        "case_id": f"email_case_{index:03d}",
        "source_message_id": f"synthetic-message-{index}",
        "final": _ground_truth(),
    }


def valid_review_export(case_count: int = 1) -> dict[str, object]:
    return {
        "schema_version": 2,
        "rubric_version": RUBRIC_VERSION,
        "reviewed_at": "2026-08-19T00:00:00Z",
        "systematic_errors_resolved": True,
        "case_count": case_count,
        "cases": [_review_case(index) for index in range(1, case_count + 1)],
    }


def _golden_case(index: int = 1) -> dict[str, object]:
    return {
        "case_id": f"email_case_{index:03d}",
        "source_message_id": f"synthetic-message-{index}",
        "ground_truth": _ground_truth(),
        "annotation": {
            "source": "human_reviewed",
            "rubric_version": RUBRIC_VERSION,
            "reviewed_at": "2026-08-19T00:00:00Z",
        },
    }


def valid_golden(case_count: int = 1) -> dict[str, object]:
    return {
        "schema_version": 2,
        "rubric_version": RUBRIC_VERSION,
        "case_count": case_count,
        "cases": [_golden_case(index) for index in range(1, case_count + 1)],
    }


def test_candidates_artifact_validation_and_ordering() -> None:
    module = load_module()
    valid = valid_candidates(2)
    assert module.validate_candidate_dataset(valid) == valid

    # Case count mismatch
    bad_count = copy.deepcopy(valid)
    bad_count["case_count"] = 99
    with pytest.raises(ValueError, match="case_count"):
        module.validate_candidate_dataset(bad_count)


def test_proposals_artifact_privacy_and_consistency() -> None:
    module = load_module()
    valid = valid_proposals(1)
    assert module.validate_proposal_batch(valid, expected_count=1) == valid

    # Privacy leak check: private key present
    leaked = copy.deepcopy(valid)
    leaked["cases"][0]["gmail_content"] = "leak"
    with pytest.raises(ValueError, match="private content"):
        module.validate_proposal_batch(leaked, expected_count=1)


def test_review_and_golden_export_contracts() -> None:
    module = load_module()
    valid_review = valid_review_export(1)
    assert module.validate_review_export(valid_review, expected_count=1) == valid_review

    valid_gold = valid_golden(1)
    assert module.validate_golden_dataset(valid_gold, expected_count=1) == valid_gold

    # Missing rubric version
    bad_rubric = copy.deepcopy(valid_gold)
    bad_rubric["rubric_version"] = "bad"
    with pytest.raises(ValueError, match="rubric_version"):
        module.validate_golden_dataset(bad_rubric, expected_count=1)
