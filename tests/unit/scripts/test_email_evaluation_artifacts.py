"""Contract tests for the privacy-safe Email evaluation artifacts."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tests.unit.scripts.cli_harness import load_script

PRIVATE_KEYS = {"gmail_content", "snippet", "normalized_body"}
RUBRIC_VERSION = "email-intent-annotation-v1"


def load_module():
    return load_script("email_evaluation_artifacts")


def _ground_truth(*, route: str = "retrieve_rag") -> dict[str, object]:
    return {
        "actionability": "action_required",
        "email_is_sufficient": False,
        "knowledge_gaps": ["Synthetic missing policy"],
        "expected_document_types": ["company_policy"],
        "expected_route": route,
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
        "resolver_expected_route": "retrieve_rag",
        "consistency_status": "consistent",
        "selection_reason": "Synthetic route-diverse calibration case.",
        "review_status": "pending",
    }


def valid_proposals(case_count: int = 1) -> dict[str, object]:
    return {
        "schema_version": 1,
        "rubric_version": RUBRIC_VERSION,
        "case_count": case_count,
        "cases": [_proposal_case(index) for index in range(1, case_count + 1)],
    }


def _review_case(index: int = 1) -> dict[str, object]:
    truth = _ground_truth()
    return {
        "case_id": f"email_case_{index:03d}",
        "source_message_id": f"synthetic-message-{index}",
        "proposal": copy.deepcopy(truth),
        "final": truth,
        "review_status": "accepted",
    }


def valid_review_export(case_count: int = 1) -> dict[str, object]:
    return {
        "schema_version": 1,
        "rubric_version": RUBRIC_VERSION,
        "reviewed_at": "2026-08-19T00:00:00Z",
        "systematic_errors_resolved": True,
        "case_count": case_count,
        "cases": [_review_case(index) for index in range(1, case_count + 1)],
    }


def valid_golden(case_count: int = 1) -> dict[str, object]:
    return {
        "schema_version": 1,
        "rubric_version": RUBRIC_VERSION,
        "case_count": case_count,
        "cases": [
            {
                "case_id": f"email_case_{index:03d}",
                "source_message_id": f"synthetic-message-{index}",
                "ground_truth": _ground_truth(),
                "annotation": {
                    "source": "human_reviewed",
                    "rubric_version": RUBRIC_VERSION,
                    "reviewed_at": "2026-08-19T00:00:00Z",
                },
            }
            for index in range(1, case_count + 1)
        ],
    }


def valid_run(case_count: int = 1) -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_id": "email-intent-2026-08-19-synthetic-shard-01",
        "created_at": "2026-08-19T00:00:00Z",
        "dataset_fingerprint": "sha256:synthetic-fingerprint",
        "rubric_version": RUBRIC_VERSION,
        "provider": "openrouter",
        "model": "synthetic-model",
        "prompt_version": "email-intent-v1",
        "shard": {"index": 1, "count": 1, "case_count": case_count},
        "cases": [
            {
                "case_id": f"email_case_{index:03d}",
                "prediction": {
                    "actionability": "action_required",
                    "email_is_sufficient": False,
                    "knowledge_gaps": ["Synthetic missing policy"],
                    "retrieval_query": "synthetic policy",
                    "expected_document_types": ["company_policy"],
                    "confidence": 0.93,
                    "source_status": "model_prediction",
                },
                "routing": {
                    "resolved_route": "retrieve_rag",
                    "reason_codes": ["policy_required"],
                },
            }
            for index in range(1, case_count + 1)
        ],
    }


def test_approved_artifacts_validate_and_return_copies() -> None:
    module = load_module()

    values_and_validators = [
        (
            valid_candidates(2),
            lambda value: module.validate_candidate_dataset(value, expected_count=2),
        ),
        (
            valid_proposals(),
            lambda value: module.validate_proposal_batch(value, expected_count=1),
        ),
        (
            valid_review_export(),
            lambda value: module.validate_review_export(value, expected_count=1),
        ),
        (valid_golden(), lambda value: module.validate_golden_dataset(value)),
        (valid_run(), lambda value: module.validate_run_artifact(value)),
    ]

    for value, validate in values_and_validators:
        validated = validate(value)
        assert validated == value
        assert validated is not value
        assert validated["cases"] is not value["cases"]


def test_candidate_requires_complete_named_content_and_unique_ids() -> None:
    module = load_module()
    candidate = valid_candidates(case_count=2)
    candidate["cases"][1]["source_message_id"] = candidate["cases"][0]["source_message_id"]

    with pytest.raises(ValueError, match="duplicate source_message_id"):
        module.validate_candidate_dataset(candidate, expected_count=2)


def test_candidate_metadata_and_content_are_strict() -> None:
    module = load_module()
    candidate = valid_candidates(case_count=1)
    candidate["cases"][0]["snippet"] = "Synthetic forbidden excerpt."

    with pytest.raises(ValueError, match="snippet"):
        module.validate_candidate_dataset(candidate)

    candidate = valid_candidates(case_count=1)
    candidate["unexpected"] = True
    with pytest.raises(ValueError, match="unknown key.*unexpected"):
        module.validate_candidate_dataset(candidate)


def test_candidate_requires_the_fixed_inbox_query() -> None:
    module = load_module()
    candidate = valid_candidates(case_count=1)
    candidate["gmail_query"] = "is:unread"

    with pytest.raises(ValueError, match="gmail_query.*in:inbox"):
        module.validate_candidate_dataset(candidate)


def test_candidate_requires_newest_first_received_at_order() -> None:
    module = load_module()
    candidate = valid_candidates(case_count=2)
    candidate["cases"][0]["received_at"] = "2026-08-18T00:00:00Z"
    candidate["cases"][1]["received_at"] = "2026-08-19T00:00:00Z"

    with pytest.raises(ValueError, match="received_at.*descending"):
        module.validate_candidate_dataset(candidate, expected_count=2)


def test_golden_rejects_prediction_and_private_content() -> None:
    module = load_module()
    golden = valid_golden(case_count=1)
    golden["cases"][0]["prediction"] = {"resolved_route": "no_action"}

    with pytest.raises(ValueError, match="prediction"):
        module.validate_golden_dataset(golden)


@pytest.mark.parametrize(
    ("factory", "validator"),
    [
        (valid_proposals, "validate_proposal_batch"),
        (valid_review_export, "validate_review_export"),
        (valid_golden, "validate_golden_dataset"),
        (valid_run, "validate_run_artifact"),
    ],
)
def test_non_candidate_artifacts_recursively_reject_private_keys(factory, validator) -> None:
    module = load_module()
    value = factory()
    value["cases"][0]["nested"] = {"metadata": {"gmail_content": "Synthetic private body."}}

    with pytest.raises(ValueError, match="gmail_content"):
        getattr(module, validator)(value)


def test_validators_enforce_fixed_enums_and_case_limits() -> None:
    module = load_module()

    golden = valid_golden()
    golden["cases"][0]["ground_truth"]["actionability"] = "maybe"
    with pytest.raises(ValueError, match="actionability"):
        module.validate_golden_dataset(golden)

    run = valid_run()
    run["prompt_version"] = "current"
    with pytest.raises(ValueError, match="prompt_version"):
        module.validate_run_artifact(run)

    oversized_run = valid_run(case_count=51)
    with pytest.raises(ValueError, match="maximum.*50"):
        module.validate_run_artifact(oversized_run)


def test_run_validator_enforces_an_absolute_fifty_case_cap() -> None:
    module = load_module()
    run = valid_run(case_count=51)

    with pytest.raises(ValueError, match="maximum.*50"):
        module.validate_run_artifact(run, maximum_cases=51)


def test_atomic_write_and_load_json_object_are_metadata_safe(tmp_path: Path) -> None:
    module = load_module()
    destination = tmp_path / "nested" / "artifact.json"
    value = {"schema_version": 1, "case_count": 0, "cases": []}

    module.atomic_write_json(value, destination)

    assert json.loads(destination.read_text(encoding="utf-8")) == value
    assert module.load_json_object(destination) == value
    assert not destination.with_name(f".{destination.name}.tmp").exists()


def test_json_loader_requires_an_object(tmp_path: Path) -> None:
    module = load_module()
    source = tmp_path / "array.json"
    source.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON object"):
        module.load_json_object(source)


def test_dataset_fingerprint_is_key_order_stable_and_label_sensitive() -> None:
    module = load_module()
    golden = valid_golden(case_count=2)
    reordered = {
        "cases": [dict(reversed(case.items())) for case in golden["cases"]],
        "case_count": 2,
        "rubric_version": RUBRIC_VERSION,
        "schema_version": 1,
    }

    assert module.dataset_fingerprint(golden) == module.dataset_fingerprint(reordered)

    changed = copy.deepcopy(golden)
    changed["cases"][1]["ground_truth"]["expected_route"] = "no_action"
    assert module.dataset_fingerprint(golden) != module.dataset_fingerprint(changed)
