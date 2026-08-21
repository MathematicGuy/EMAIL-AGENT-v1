"""Strict, metadata-safe contracts for Email evaluation artifacts."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from json import JSONDecodeError
from pathlib import Path
from typing import cast

ACTIONABILITIES = frozenset(
    {"action_required", "action_suggested", "informational", "irrelevant", "unclear"}
)
ROUTES = frozenset({"no_action", "direct_plan", "retrieve_rag"})
ROUTE_MODES = frozenset({"full", "partial"})
EVIDENCE_STATUSES = frozenset({"supported", "unsupported", "unavailable"})
QUERY_REWRITE_STATUSES = frozenset({"classifier_query", "rewritten", "fallback"})
RETRIEVAL_STATUSES = frozenset(
    {
        "success",
        "no_results",
        "timeout",
        "authorization_denied",
        "partial",
        "failed",
        "unavailable",
    }
)
DOCUMENT_TYPES = frozenset(
    {
        "company_policy",
        "governance_document",
        "procedure",
        "guideline",
        "template",
        "product_documentation",
    }
)
PRIVATE_CONTENT_KEYS = frozenset({"gmail_content", "snippet", "normalized_body"})
RUBRIC_VERSION = "email-pipeline-annotation-v2"
PROMPT_VERSION = "email-intent-v1"
PIPELINE_VERSION = "4"
GATE_VERSION = "email-rag-gate-v1"

_ANNOTATION_SOURCES = frozenset({"human_reviewed", "calibrated_labeling_agent"})
_CONSISTENCY_STATUSES = frozenset({"consistent", "needs_review"})
_PROPOSAL_REVIEW_STATUSES = frozenset({"pending"})
_REVIEW_STATUSES = frozenset({"accepted", "corrected"})
_RUN_SOURCE_STATUSES = frozenset({"model_prediction", "classifier_fallback"})


def load_json_object(path: Path) -> dict[str, object]:
    """Load one JSON object and reject arrays or scalar JSON values."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, JSONDecodeError) as exc:
        raise ValueError(f"could not load JSON object from {path}: {exc}") from exc
    mapping = _mapping(value, str(path))
    return copy.deepcopy(dict(mapping))


def atomic_write_json(value: Mapping[str, object], path: Path) -> None:
    """Write JSON through the task-specified sibling temporary file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def validate_candidate_dataset(
    value: object, *, expected_count: int | None = None
) -> dict[str, object]:
    """Validate the private full-content candidate export."""

    top = _top_level(
        value,
        {
            "schema_version",
            "fetched_at",
            "gmail_query",
            "ordering",
            "case_count",
            "cases",
        },
        "candidate dataset",
        blocked_private_keys=PRIVATE_CONTENT_KEYS - {"gmail_content"},
    )
    _require_int(top["schema_version"], "candidate dataset.schema_version", minimum=1)
    _require_timestamp(top["fetched_at"], "candidate dataset.fetched_at")
    if top["gmail_query"] != "in:inbox":
        raise ValueError("candidate dataset.gmail_query must be exactly 'in:inbox'")
    if top["ordering"] != "received_at_desc":
        raise ValueError("candidate dataset.ordering must be 'received_at_desc'")
    cases = _list(top["cases"], "candidate dataset.cases")
    _require_case_count(top["case_count"], len(cases), expected_count, "candidate dataset")
    validated_cases = []
    for index, value_case in enumerate(cases, start=1):
        case = _top_level(
            value_case,
            {
                "case_id",
                "source_message_id",
                "gmail_thread_id",
                "sender",
                "subject",
                "received_at",
                "labels",
                "gmail_content",
            },
            f"candidate dataset.cases[{index - 1}]",
            blocked_private_keys=PRIVATE_CONTENT_KEYS - {"gmail_content"},
        )
        _require_nonempty_string(case["case_id"], f"candidate case {index}.case_id")
        _require_nonempty_string(
            case["source_message_id"], f"candidate case {index}.source_message_id"
        )
        _require_nonempty_string(case["gmail_thread_id"], f"candidate case {index}.gmail_thread_id")
        _require_nonempty_string(case["sender"], f"candidate case {index}.sender")
        _require_nonempty_string(case["subject"], f"candidate case {index}.subject")
        _require_timestamp(case["received_at"], f"candidate case {index}.received_at")
        _require_string_list(case["labels"], f"candidate case {index}.labels")
        _require_nonempty_string(case["gmail_content"], f"candidate case {index}.gmail_content")
        validated_cases.append(case)
    _require_unique(validated_cases, "case_id", "candidate dataset")
    _require_unique(validated_cases, "source_message_id", "candidate dataset")
    received_at = [
        _parse_timestamp(case["received_at"], f"candidate case {index}.received_at")
        for index, case in enumerate(validated_cases, start=1)
    ]
    if any(
        previous < current for previous, current in zip(received_at, received_at[1:], strict=False)
    ):
        raise ValueError("candidate dataset cases must be ordered by received_at descending")
    return _copy_validated(top, validated_cases)


def validate_proposal_batch(value: object, *, expected_count: int = 70) -> dict[str, object]:
    """Validate the proposal batch, which contains no Gmail content."""

    top = _top_level(
        value,
        {"schema_version", "rubric_version", "case_count", "cases"},
        "proposal batch",
    )
    _validate_version_fields(top, "proposal batch")
    cases = _list(top["cases"], "proposal batch.cases")
    _require_case_count(top["case_count"], len(cases), expected_count, "proposal batch")
    validated_cases = [_validate_proposal_case(item, index) for index, item in enumerate(cases)]
    _require_unique(validated_cases, "case_id", "proposal batch")
    _require_unique(validated_cases, "source_message_id", "proposal batch")
    return _copy_validated(top, validated_cases)


def validate_review_export(value: object, *, expected_count: int = 70) -> dict[str, object]:
    """Validate the browser-exported human review artifact."""

    top = _top_level(
        value,
        {
            "schema_version",
            "rubric_version",
            "reviewed_at",
            "systematic_errors_resolved",
            "case_count",
            "cases",
        },
        "review export",
    )
    _validate_version_fields(top, "review export")
    _require_timestamp(top["reviewed_at"], "review export.reviewed_at")
    _require_bool(top["systematic_errors_resolved"], "review export.systematic_errors_resolved")
    cases = _list(top["cases"], "review export.cases")
    _require_case_count(top["case_count"], len(cases), expected_count, "review export")
    validated_cases = [_validate_review_case(item, index) for index, item in enumerate(cases)]
    _require_unique(validated_cases, "case_id", "review export")
    _require_unique(validated_cases, "source_message_id", "review export")
    return _copy_validated(top, validated_cases)


def validate_golden_dataset(
    value: object, *, expected_count: int | None = None
) -> dict[str, object]:
    """Validate truth-only labels and annotation provenance."""

    top = _top_level(
        value,
        {"schema_version", "rubric_version", "case_count", "cases"},
        "golden dataset",
    )
    _validate_version_fields(top, "golden dataset")
    cases = _list(top["cases"], "golden dataset.cases")
    _require_case_count(top["case_count"], len(cases), expected_count, "golden dataset")
    validated_cases = [_validate_golden_case(item, index) for index, item in enumerate(cases)]
    _require_unique(validated_cases, "case_id", "golden dataset")
    _require_unique(validated_cases, "source_message_id", "golden dataset")
    return _copy_validated(top, validated_cases)


def validate_run_artifact(value: object, *, maximum_cases: int = 50) -> dict[str, object]:
    """Validate one metadata-only provider/model evaluation run."""

    if maximum_cases > 50:
        raise ValueError("run artifact maximum_cases cannot exceed 50")
    top = _top_level(
        value,
        {
            "schema_version",
            "run_id",
            "created_at",
            "dataset_fingerprint",
            "rubric_version",
            "provider",
            "model",
            "prompt_version",
            "pipeline_version",
            "quality_gate",
            "shard",
            "cases",
        },
        "run artifact",
    )
    if _require_int(top["schema_version"], "run artifact.schema_version", minimum=1) != 2:
        raise ValueError("run artifact.schema_version must be 2")
    _require_nonempty_string(top["run_id"], "run artifact.run_id")
    _require_timestamp(top["created_at"], "run artifact.created_at")
    fingerprint = _require_nonempty_string(
        top["dataset_fingerprint"], "run artifact.dataset_fingerprint"
    )
    if not fingerprint.startswith("sha256:"):
        raise ValueError("run artifact.dataset_fingerprint must start with 'sha256:'")
    _require_rubric_version(top, "run artifact")
    _require_nonempty_string(top["provider"], "run artifact.provider")
    _require_nonempty_string(top["model"], "run artifact.model")
    if top["prompt_version"] != PROMPT_VERSION:
        raise ValueError("run artifact.prompt_version must be 'email-intent-v1'")
    if top["pipeline_version"] != PIPELINE_VERSION:
        raise ValueError(f"run artifact.pipeline_version must be '{PIPELINE_VERSION}'")
    quality_gate = _top_level(
        top["quality_gate"],
        {"version", "min_rerank_score", "relative_cutoff_ratio"},
        "run artifact.quality_gate",
    )
    if quality_gate["version"] != GATE_VERSION:
        raise ValueError(f"run artifact.quality_gate.version must be '{GATE_VERSION}'")
    for field in ("min_rerank_score", "relative_cutoff_ratio"):
        score = _require_number(quality_gate[field], f"run artifact.quality_gate.{field}")
        if not 0 <= score <= 1:
            raise ValueError(f"run artifact.quality_gate.{field} must be between 0 and 1")
    shard = _top_level(
        top["shard"],
        {"index", "count", "case_count"},
        "run artifact.shard",
    )
    shard_index = _require_int(shard["index"], "run artifact.shard.index", minimum=1)
    shard_count = _require_int(shard["count"], "run artifact.shard.count", minimum=1)
    if shard_index > shard_count:
        raise ValueError("run artifact.shard.index cannot exceed shard.count")
    cases = _list(top["cases"], "run artifact.cases")
    if len(cases) > maximum_cases:
        raise ValueError(f"run artifact contains {len(cases)} cases; maximum is {maximum_cases}")
    _require_case_count(shard["case_count"], len(cases), None, "run artifact.shard")
    validated_cases = [_validate_run_case(item, index) for index, item in enumerate(cases)]
    _require_unique(validated_cases, "case_id", "run artifact")
    return _copy_validated(top, validated_cases, shard=dict(shard), quality_gate=dict(quality_gate))


def dataset_fingerprint(golden: Mapping[str, object]) -> str:
    """Return a stable hash for the ordered, truth-only golden dataset."""

    validated = validate_golden_dataset(golden)
    canonical = json.dumps(
        validated, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _top_level(
    value: object,
    keys: set[str],
    location: str,
    *,
    blocked_private_keys: frozenset[str] = PRIVATE_CONTENT_KEYS,
) -> dict[str, object]:
    _reject_private_content(value, location, blocked_private_keys)
    mapping = _mapping(value, location)
    _require_exact_keys(mapping, keys, location)
    return dict(mapping)


def _mapping(value: object, location: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{location} must be a JSON object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{location} keys must be strings")
    return cast(Mapping[str, object], value)


def _list(value: object, location: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{location} must be a JSON array")
    return value


def _reject_private_content(
    value: object, location: str, blocked_private_keys: frozenset[str] = PRIVATE_CONTENT_KEYS
) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if key in blocked_private_keys:
                raise ValueError(f"{location}.{key} is private content and is not allowed")
            _reject_private_content(nested, f"{location}.{key}", blocked_private_keys)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, nested in enumerate(value):
            _reject_private_content(nested, f"{location}[{index}]", blocked_private_keys)


def _require_exact_keys(mapping: Mapping[str, object], keys: set[str], location: str) -> None:
    missing = keys - set(mapping)
    if missing:
        raise ValueError(f"{location} missing required key(s): {', '.join(sorted(missing))}")
    unknown = set(mapping) - keys
    if unknown:
        raise ValueError(f"{location} has unknown key(s): {', '.join(sorted(unknown))}")


def _require_unique(cases: Sequence[Mapping[str, object]], field: str, location: str) -> None:
    seen: set[object] = set()
    for case in cases:
        value = case[field]
        if value in seen:
            raise ValueError(f"{location} has duplicate {field}: {value}")
        seen.add(value)


def _require_case_count(
    value: object,
    actual_count: int,
    expected_count: int | None,
    location: str,
) -> None:
    count = _require_int(value, f"{location}.case_count", minimum=0)
    if count != actual_count:
        raise ValueError(f"{location}.case_count is {count}; found {actual_count} cases")
    if expected_count is not None and count != expected_count:
        raise ValueError(f"{location} requires exactly {expected_count} cases; found {count}")


def _validate_version_fields(mapping: Mapping[str, object], location: str) -> None:
    if _require_int(mapping["schema_version"], f"{location}.schema_version", minimum=1) != 2:
        raise ValueError(f"{location}.schema_version must be 2")
    _require_rubric_version(mapping, location)


def _require_rubric_version(mapping: Mapping[str, object], location: str) -> None:
    if mapping["rubric_version"] != RUBRIC_VERSION:
        raise ValueError(f"{location}.rubric_version must be '{RUBRIC_VERSION}'")


def _validate_proposal_case(value: object, index: int) -> dict[str, object]:
    location = f"proposal batch.cases[{index}]"
    case = _top_level(
        value,
        {
            "case_id",
            "source_message_id",
            "proposed_ground_truth",
            "resolver_expected_retrieval",
            "consistency_status",
            "selection_reason",
            "review_status",
        },
        location,
    )
    _require_nonempty_string(case["case_id"], f"{location}.case_id")
    _require_nonempty_string(case["source_message_id"], f"{location}.source_message_id")
    _validate_ground_truth(case["proposed_ground_truth"], f"{location}.proposed_ground_truth")
    _require_bool(case["resolver_expected_retrieval"], f"{location}.resolver_expected_retrieval")
    _require_enum(
        case["consistency_status"], _CONSISTENCY_STATUSES, f"{location}.consistency_status"
    )
    _require_nonempty_string(case["selection_reason"], f"{location}.selection_reason")
    _require_enum(case["review_status"], _PROPOSAL_REVIEW_STATUSES, f"{location}.review_status")
    return case


def _validate_review_case(value: object, index: int) -> dict[str, object]:
    location = f"review export.cases[{index}]"
    case = _top_level(
        value,
        {"case_id", "source_message_id", "final"},
        location,
    )
    _require_nonempty_string(case["case_id"], f"{location}.case_id")
    _require_nonempty_string(case["source_message_id"], f"{location}.source_message_id")
    _validate_ground_truth(case["final"], f"{location}.final")
    return case


def _validate_golden_case(value: object, index: int) -> dict[str, object]:
    location = f"golden dataset.cases[{index}]"
    case = _top_level(
        value,
        {"case_id", "source_message_id", "ground_truth", "annotation"},
        location,
    )
    _require_nonempty_string(case["case_id"], f"{location}.case_id")
    _require_nonempty_string(case["source_message_id"], f"{location}.source_message_id")
    _validate_ground_truth(case["ground_truth"], f"{location}.ground_truth")
    annotation = _top_level(
        case["annotation"],
        {"source", "rubric_version", "reviewed_at"},
        f"{location}.annotation",
    )
    _require_enum(annotation["source"], _ANNOTATION_SOURCES, f"{location}.annotation.source")
    _require_rubric_version(annotation, f"{location}.annotation")
    _require_timestamp(annotation["reviewed_at"], f"{location}.annotation.reviewed_at")
    return case


def _validate_run_case(value: object, index: int) -> dict[str, object]:
    location = f"run artifact.cases[{index}]"
    case = _top_level(value, {"case_id", "prediction", "retrieval", "routing"}, location)
    _require_nonempty_string(case["case_id"], f"{location}.case_id")
    prediction = _top_level(
        case["prediction"],
        {
            "actionability",
            "email_is_sufficient",
            "expected_document_types",
            "confidence",
            "source_status",
        },
        f"{location}.prediction",
    )
    _require_enum(
        prediction["actionability"], ACTIONABILITIES, f"{location}.prediction.actionability"
    )
    _require_bool(prediction["email_is_sufficient"], f"{location}.prediction.email_is_sufficient")
    _require_document_types(
        prediction["expected_document_types"], f"{location}.prediction.expected_document_types"
    )
    confidence = _require_number(prediction["confidence"], f"{location}.prediction.confidence")
    if not 0 <= confidence <= 1:
        raise ValueError(f"{location}.prediction.confidence must be between 0 and 1")
    _require_enum(
        prediction["source_status"], _RUN_SOURCE_STATUSES, f"{location}.prediction.source_status"
    )
    retrieval = _top_level(
        case["retrieval"],
        {
            "attempted",
            "retrieval_status",
            "evidence_status",
            "result_count",
            "accepted_chunk_count",
            "top_rerank_score",
            "query_rewrite_status",
            "degraded",
        },
        f"{location}.retrieval",
    )
    attempted = _require_bool(retrieval["attempted"], f"{location}.retrieval.attempted")
    degraded = _require_bool(retrieval["degraded"], f"{location}.retrieval.degraded")
    if attempted:
        _require_enum(
            retrieval["retrieval_status"],
            RETRIEVAL_STATUSES,
            f"{location}.retrieval.retrieval_status",
        )
        _require_enum(
            retrieval["evidence_status"],
            EVIDENCE_STATUSES,
            f"{location}.retrieval.evidence_status",
        )
        _require_enum(
            retrieval["query_rewrite_status"],
            QUERY_REWRITE_STATUSES,
            f"{location}.retrieval.query_rewrite_status",
        )
    elif any(
        retrieval[field] is not None
        for field in (
            "retrieval_status",
            "evidence_status",
            "top_rerank_score",
            "query_rewrite_status",
        )
    ):
        raise ValueError(f"{location}.retrieval unattempted fields must be null")
    result_count = _require_int(
        retrieval["result_count"], f"{location}.retrieval.result_count", minimum=0
    )
    accepted_count = _require_int(
        retrieval["accepted_chunk_count"],
        f"{location}.retrieval.accepted_chunk_count",
        minimum=0,
    )
    if accepted_count > result_count:
        raise ValueError(f"{location}.retrieval.accepted_chunk_count cannot exceed result_count")
    if retrieval["top_rerank_score"] is not None:
        score = _require_number(
            retrieval["top_rerank_score"], f"{location}.retrieval.top_rerank_score"
        )
        if not 0 <= score <= 1:
            raise ValueError(f"{location}.retrieval.top_rerank_score must be between 0 and 1")
    evidence_status = retrieval["evidence_status"]
    if not attempted and (result_count != 0 or accepted_count != 0 or degraded):
        raise ValueError(f"{location}.retrieval unattempted counts must be zero and not degraded")
    if evidence_status == "supported" and (
        accepted_count == 0 or retrieval["top_rerank_score"] is None
    ):
        raise ValueError(f"{location}.retrieval supported evidence requires accepted scored chunks")
    if evidence_status in {"unsupported", "unavailable"} and accepted_count != 0:
        raise ValueError(f"{location}.retrieval rejected evidence cannot have accepted chunks")
    if degraded != (evidence_status == "unavailable"):
        raise ValueError(f"{location}.retrieval degraded must match unavailable evidence")
    routing = _top_level(
        case["routing"],
        {"resolved_route", "mode", "forced_by_guard", "reason_codes"},
        f"{location}.routing",
    )
    _require_enum(routing["resolved_route"], ROUTES, f"{location}.routing.resolved_route")
    _require_enum(routing["mode"], ROUTE_MODES, f"{location}.routing.mode")
    _require_bool(routing["forced_by_guard"], f"{location}.routing.forced_by_guard")
    _require_string_list(routing["reason_codes"], f"{location}.routing.reason_codes")
    return case


def _validate_ground_truth(value: object, location: str) -> None:
    ground_truth = _top_level(
        value,
        {
            "actionability",
            "email_is_sufficient",
            "knowledge_gaps",
            "expected_document_types",
            "retrieval_expected",
            "company_context_required",
            "rationale",
        },
        location,
    )
    _require_enum(ground_truth["actionability"], ACTIONABILITIES, f"{location}.actionability")
    _require_bool(ground_truth["email_is_sufficient"], f"{location}.email_is_sufficient")
    _require_string_list(ground_truth["knowledge_gaps"], f"{location}.knowledge_gaps")
    _require_document_types(
        ground_truth["expected_document_types"], f"{location}.expected_document_types"
    )
    retrieval_expected = _require_bool(
        ground_truth["retrieval_expected"], f"{location}.retrieval_expected"
    )
    context_required = _require_bool(
        ground_truth["company_context_required"],
        f"{location}.company_context_required",
    )
    actionable = ground_truth["actionability"] not in {"informational", "irrelevant"}
    if retrieval_expected != actionable:
        raise ValueError(
            f"{location}.retrieval_expected must be true exactly for non-NO_ACTION cases"
        )
    if context_required and not actionable:
        raise ValueError(f"{location}.company_context_required cannot be true for NO_ACTION cases")
    _require_nonempty_string(ground_truth["rationale"], f"{location}.rationale")


def _require_enum(value: object, allowed: frozenset[str], location: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        values = ", ".join(sorted(allowed))
        raise ValueError(f"{location} must be one of: {values}")
    return value


def _require_document_types(value: object, location: str) -> list[str]:
    values = _require_string_list(value, location)
    for item in values:
        if item not in DOCUMENT_TYPES:
            allowed = ", ".join(sorted(DOCUMENT_TYPES))
            raise ValueError(
                f"{location} contains unsupported document type {item}; allowed: {allowed}"
            )
    return values


def _require_string_list(value: object, location: str) -> list[str]:
    values = _list(value, location)
    if any(not isinstance(item, str) for item in values):
        raise ValueError(f"{location} must contain only strings")
    return cast(list[str], values)


def _require_nonempty_string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location} must be a non-empty string")
    return value


def _require_bool(value: object, location: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{location} must be a boolean")
    return value


def _require_int(value: object, location: str, *, minimum: int | None = None) -> int:
    if type(value) is not int:
        raise ValueError(f"{location} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{location} must be at least {minimum}")
    return value


def _require_number(value: object, location: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{location} must be a number")
    return float(value)


def _require_timestamp(value: object, location: str) -> str:
    timestamp = _require_nonempty_string(value, location)
    _parse_timestamp(timestamp, location)
    return timestamp


def _parse_timestamp(value: str, location: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{location} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{location} must include a timezone")
    return parsed


def _copy_validated(
    top: Mapping[str, object],
    cases: Sequence[Mapping[str, object]],
    *,
    shard: dict[str, object] | None = None,
    quality_gate: dict[str, object] | None = None,
) -> dict[str, object]:
    copied = dict(top)
    copied["cases"] = [dict(case) for case in cases]
    if shard is not None:
        copied["shard"] = shard
    if quality_gate is not None:
        copied["quality_gate"] = quality_gate
    return copy.deepcopy(copied)
