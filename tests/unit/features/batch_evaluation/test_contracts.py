from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from cowork_agent.features.batch_evaluation.contracts import (
    ArtifactBundle,
    AttemptState,
    CleanupOutcome,
    CredentialState,
    EvaluationBudget,
    EvaluationRequest,
    EvaluationWarning,
    ExecutionMode,
    FailureClass,
    JobState,
    PluginPlan,
    ProviderAttemptEvent,
    StepState,
    UnitState,
    WorkContext,
    WorkUnit,
    WorkUnitOutcome,
    canonical_request_hash,
)


def valid_request() -> dict[str, object]:
    return {
        "evaluation_type": "memory-eval",
        "provider": "mistral",
        "target_model": "mistral-small-latest",
        "dataset_ref": "memory-probes-v1",
        "credential_pool": "mistral-eval",
        "execution_mode": "workflow_shards",
        "execution_options": {"max_attempts_per_unit": 2},
        "budget": {"max_provider_requests": 10, "max_total_tokens": 1_000},
        "parameters": {"probe_set": {"version": "v1"}},
    }


def test_request_defaults_to_one_worker_and_rejects_zero() -> None:
    payload = valid_request()
    payload["execution_options"] = {}
    request = EvaluationRequest.from_dict(payload)
    assert request.max_workers == 1
    assert request.max_attempts_per_unit == 1

    payload["execution_options"] = {"max_workers": 0, "max_attempts_per_unit": 2}
    with pytest.raises(ValueError, match="max_workers"):
        EvaluationRequest.from_dict(payload)


def test_canonical_hash_ignores_json_key_order_but_not_values() -> None:
    first = EvaluationRequest.from_dict(valid_request())
    reordered = EvaluationRequest.from_dict(dict(reversed(list(valid_request().items()))))
    changed_payload = valid_request()
    changed_payload["target_model"] = "mistral-large-latest"
    changed = EvaluationRequest.from_dict(changed_payload)

    assert canonical_request_hash(first) == canonical_request_hash(reordered)
    assert canonical_request_hash(first) != canonical_request_hash(changed)


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("evaluation_type", ["memory-eval"]),
        ("provider", ["mistral"]),
        ("target_model", ["mistral-small-latest"]),
        ("dataset_ref", ["memory-probes-v1"]),
        ("credential_pool", ["mistral-eval"]),
        ("execution_mode", ["workflow_shards"]),
        ("evaluation_type", "memory eval"),
        ("dataset_ref", "../memory-probes-v1"),
    ],
)
def test_request_requires_one_safe_identifier_per_selector(
    field: str, bad_value: object
) -> None:
    payload = valid_request()
    payload[field] = bad_value

    with pytest.raises((TypeError, ValueError), match=field):
        EvaluationRequest.from_dict(payload)


@pytest.mark.parametrize(
    "key",
    [
        "api_key",
        "token",
        "authorization",
        "nested_api_key",
        "apiKey",
        "APIKey",
        "apikey",
        "accessToken",
        "AccessToken",
        "access_token",
    ],
)
def test_request_recursively_rejects_secret_shaped_parameter_keys(key: str) -> None:
    payload = valid_request()
    payload["parameters"] = {"safe": {key: "not-for-a-record"}}

    with pytest.raises(ValueError, match="secret"):
        EvaluationRequest.from_dict(payload)


def test_request_rejects_non_positive_budgets_and_unsupported_execution_mode() -> None:
    payload = valid_request()
    payload["budget"] = {"max_provider_requests": 0, "max_total_tokens": 1}
    with pytest.raises(ValueError, match="max_provider_requests"):
        EvaluationRequest.from_dict(payload)

    payload = valid_request()
    payload["budget"] = {"max_provider_requests": 1, "max_total_tokens": 0}
    with pytest.raises(ValueError, match="max_total_tokens"):
        EvaluationRequest.from_dict(payload)

    payload = valid_request()
    invalid_mode = "provider_batch_with_private_value"
    payload["execution_mode"] = invalid_mode
    with pytest.raises(ValueError, match="execution_mode") as error:
        EvaluationRequest.from_dict(payload)
    assert invalid_mode not in str(error.value)
    assert error.value.__cause__ is None


def test_contract_records_and_nested_safe_metadata_are_frozen() -> None:
    request = EvaluationRequest.from_dict(valid_request())
    unit = WorkUnit(unit_id="unit-1", ordinal=0, payload={"item_id": "case-1"})
    artifact = ArtifactBundle(
        public_result={"summary": {"succeeded": 1}}, private_artifact_ids=("detail-1",)
    )
    warning = EvaluationWarning(
        code="WORKER_COUNT_REDUCED",
        message="Worker count was reduced.",
        details={"requested_workers": 4},
    )

    with pytest.raises(FrozenInstanceError):
        request.max_workers = 2  # type: ignore[misc]
    with pytest.raises(TypeError):
        request.parameters["probe_set"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        unit.payload["item_id"] = "case-2"  # type: ignore[index]
    with pytest.raises(TypeError):
        unit.payload["item_ids"] = ("case-2",)  # type: ignore[index]
    with pytest.raises(TypeError):
        artifact.public_result["summary"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        warning.details["requested_workers"] = 1  # type: ignore[index]


def test_safe_records_reject_secret_shaped_keys_and_hide_private_values_from_repr() -> None:
    with pytest.raises(ValueError, match="secret"):
        WorkUnit(unit_id="unit-1", ordinal=0, payload={"authorization": "secret-value"})
    with pytest.raises(ValueError, match="secret"):
        ArtifactBundle(
            public_result={"api_key": "secret-value"}, private_artifact_ids=("detail-1",)
        )

    plan = PluginPlan(dataset_ref="memory-probes-v1", ready_work=1, private_plan="private-plan")
    context = WorkContext(
        job_id="job-1",
        attempt_id="attempt-1",
        lane_id="lane-1",
        credential_alias="mistral-eval-1",
        plugin_plan=plan,
        provider_client="private-client",
        scratch_dir=Path("private-scratch"),
    )
    outcome = WorkUnitOutcome(
        unit_id="unit-1",
        ordinal=0,
        state=UnitState.SUCCEEDED,
        provider_requests=1,
        total_tokens=10,
        private_result="private-result",
    )

    assert "private-plan" not in repr(plan)
    assert "private-client" not in repr(context)
    assert "private-scratch" not in repr(context)
    assert "private-result" not in repr(outcome)


@pytest.mark.parametrize("key", ["message", "question", "reply", "content"])
def test_work_unit_payload_rejects_every_non_metadata_key(key: str) -> None:
    with pytest.raises(ValueError, match="unsupported metadata key") as error:
        WorkUnit(
            unit_id="unit-1",
            ordinal=0,
            payload={key: "private evaluation content"},
        )
    assert key not in str(error.value)
    assert error.value.__cause__ is None


def test_work_unit_payload_accepts_stable_id_and_shard_metadata() -> None:
    unit = WorkUnit(
        unit_id="unit-1",
        ordinal=2,
        payload={
            "case_id": "case-1",
            "item_id": "item-1",
            "probe_ids": ["probe-1", "probe-2"],
            "ordinal": 2,
            "ordinals": [0, 2],
            "shard_id": "shard-1",
            "shard_index": 0,
            "shard_count": 2,
        },
    )

    assert unit.payload == {
        "case_id": "case-1",
        "item_id": "item-1",
        "probe_ids": ("probe-1", "probe-2"),
        "ordinal": 2,
        "ordinals": (0, 2),
        "shard_id": "shard-1",
        "shard_index": 0,
        "shard_count": 2,
    }


def test_work_unit_payload_accepts_multi_segment_stable_id_metadata() -> None:
    unit = WorkUnit(
        unit_id="unit-1",
        ordinal=0,
        payload={
            "source_document_id": "document-1",
            "test_case_ids": ["case-1", "case-2"],
        },
    )

    assert unit.payload == {
        "source_document_id": "document-1",
        "test_case_ids": ("case-1", "case-2"),
    }


@pytest.mark.parametrize(
    "key",
    ["Source_document_id", "source__document_id", "_source_document_id", "message"],
)
def test_work_unit_payload_rejects_malformed_or_free_form_metadata_keys(key: str) -> None:
    with pytest.raises(ValueError, match="unsupported metadata key") as error:
        WorkUnit(unit_id="unit-1", ordinal=0, payload={key: "value"})
    assert key not in str(error.value)
    assert error.value.__cause__ is None


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("item_id", 1),
        ("probe_ids", "probe-1"),
        ("ordinals", [0, -1]),
        ("shard_index", -1),
    ],
)
def test_work_unit_payload_type_checks_stable_metadata(key: str, value: object) -> None:
    with pytest.raises((TypeError, ValueError), match=key):
        WorkUnit(unit_id="unit-1", ordinal=0, payload={key: value})


def test_artifact_and_cleanup_records_coerce_mutable_lists_to_tuples() -> None:
    artifact_ids = ["detail-1"]
    warning = EvaluationWarning(
        code="WORKER_COUNT_REDUCED",
        message="Worker count was reduced.",
        details={"requested_workers": 4},
    )
    warnings = [warning]
    artifact = ArtifactBundle(public_result={}, private_artifact_ids=artifact_ids)  # type: ignore[arg-type]
    cleanup = CleanupOutcome(removed_resources=1, warnings=warnings)  # type: ignore[arg-type]

    artifact_ids.append("detail-2")
    warnings.clear()

    assert artifact.private_artifact_ids == ("detail-1",)
    assert cleanup.warnings == (warning,)
    with pytest.raises(FrozenInstanceError):
        artifact.private_artifact_ids = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        cleanup.warnings = ()  # type: ignore[misc]


def test_contract_enums_expose_documented_values() -> None:
    assert set(ExecutionMode) == {ExecutionMode.REQUEST_BATCH, ExecutionMode.WORKFLOW_SHARDS}
    assert JobState.SUCCEEDED.value == "succeeded"
    assert UnitState.READY.value == "ready"
    assert AttemptState.UNKNOWN.value == "unknown"
    assert StepState.SKIPPED.value == "skipped"
    assert FailureClass.PROVIDER.value == "provider"
    assert CredentialState.COOLING_DOWN.value == "cooling_down"


def test_budget_and_provider_event_require_safe_non_negative_counts() -> None:
    with pytest.raises(ValueError, match="max_provider_requests"):
        EvaluationBudget(max_provider_requests=0, max_total_tokens=1)
    with pytest.raises(ValueError, match="latency_ms"):
        ProviderAttemptEvent(
            credential_alias="mistral-eval-1",
            request_attempt_id="attempt-1",
            outcome="succeeded",
            status_code=200,
            retry_after_seconds=None,
            latency_ms=-1,
        )
