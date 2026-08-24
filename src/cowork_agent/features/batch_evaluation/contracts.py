"""Stable, safe records shared by every Level 1 evaluation plug-in."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, cast


class ExecutionMode(StrEnum):
    """How the Level 1 executor schedules an evaluation plug-in's work."""

    REQUEST_BATCH = "request_batch"
    WORKFLOW_SHARDS = "workflow_shards"


class JobState(StrEnum):
    """Durable lifecycle state for one evaluation job."""

    ACCEPTED = "accepted"
    VALIDATING = "validating"
    QUEUED = "queued"
    RUNNING = "running"
    COLLECTING = "collecting"
    SUCCEEDED = "succeeded"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    FAILED = "failed"
    CANCELLATION_REQUESTED = "cancellation_requested"
    CANCELLED = "cancelled"


class UnitState(StrEnum):
    """Durable state for a plug-in-defined work unit."""

    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AttemptState(StrEnum):
    """State of one provider attempt, including restart-safe ambiguity."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


class StepState(StrEnum):
    """State of one bounded step inside a work unit."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class FailureClass(StrEnum):
    """Safe failure category returned by a plug-in."""

    VALIDATION = "validation"
    PERMANENT = "permanent"
    PROVIDER = "provider"
    PRODUCT = "product"
    EVALUATION = "evaluation"
    UNKNOWN = "unknown"


class CredentialState(StrEnum):
    """Lifecycle state for a leased non-secret credential alias."""

    AVAILABLE = "available"
    LEASED = "leased"
    COOLING_DOWN = "cooling_down"
    DISABLED = "disabled"


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_SECRET_KEY_PARTS = frozenset({"authorization", "credential", "password", "secret", "token"})
_SECRET_KEY_COMPACTS = frozenset({"apikey", "accesstoken"})
_WARNING_PRIVATE_KEY_PARTS = _SECRET_KEY_PARTS | frozenset(
    {
        "content",
        "dataset",
        "error",
        "message",
        "path",
        "private",
        "prompt",
        "question",
        "reply",
        "traceback",
    }
)
_WARNING_MESSAGES = MappingProxyType(
    {
        "CLEANUP_FAILED": "Evaluation cleanup did not complete.",
        "WORKER_COUNT_REDUCED": (
            "Worker count was reduced because fewer credentials are healthy."
        ),
    }
)
_WORK_UNIT_ID_FIELD = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z][a-z0-9]*)*_id$")
_WORK_UNIT_ID_COLLECTION_FIELD = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z][a-z0-9]*)*_ids$")
_WORK_UNIT_INTEGER_FIELDS = frozenset({"ordinal", "shard_index", "shard_count"})
_WORK_UNIT_INTEGER_COLLECTION_FIELDS = frozenset({"ordinals"})

# A job retries no work by default unless a later submission explicitly opts in.
DEFAULT_MAX_ATTEMPTS_PER_UNIT = 1


def _require_identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{name} must be a safe identifier")
    return value


def _require_int_at_least(value: object, name: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _require_mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise TypeError(f"{name} must be a mapping with string keys")
    return value


def _require_exact_keys(mapping: Mapping[str, object], name: str, expected: frozenset[str]) -> None:
    missing = expected - mapping.keys()
    extra = mapping.keys() - expected
    if missing or extra:
        raise ValueError(f"{name} has invalid keys")


def _reject_unknown_keys(mapping: Mapping[str, object], name: str, allowed: frozenset[str]) -> None:
    if mapping.keys() - allowed:
        raise ValueError(f"{name} has invalid keys")


def _normalize_key(key: str) -> str:
    with_word_boundaries = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    return re.sub(r"[^a-z0-9]+", "_", with_word_boundaries.lower()).strip("_")


def _reject_secret_shaped_keys(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if isinstance(key, str):
                normalized = _normalize_key(key)
                parts = frozenset(part for part in normalized.split("_") if part)
                compact = normalized.replace("_", "")
                if any(marker in compact for marker in _SECRET_KEY_COMPACTS) or (
                    parts & _SECRET_KEY_PARTS
                ):
                    raise ValueError("secret-shaped keys are not allowed in safe records")
            _reject_secret_shaped_keys(nested)
        return
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for nested in value:
            _reject_secret_shaped_keys(nested)


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("nested mappings must have string keys")
        return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})
    if isinstance(value, tuple | list):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("safe record floats must be finite")
    if value is None or isinstance(value, str | int | float | bool | Enum):
        return value
    raise TypeError("safe record values must be JSON-compatible")


def _freeze_safe_mapping(value: object, name: str) -> Mapping[str, object]:
    mapping = _require_mapping(value, name)
    _reject_secret_shaped_keys(mapping)
    return cast(Mapping[str, object], _freeze_value(mapping))


def _freeze_warning_details(value: object) -> Mapping[str, int | str]:
    details = _freeze_safe_mapping(value, "details")
    for key, item in details.items():
        normalized = _normalize_key(key)
        parts = frozenset(part for part in normalized.split("_") if part)
        if parts & _WARNING_PRIVATE_KEY_PARTS:
            raise ValueError("warning details contain a private key")
        if isinstance(item, str):
            _require_identifier(item, "warning detail")
        elif isinstance(item, bool) or not isinstance(item, int):
            raise TypeError("details values must be safe strings or integers")
    return cast(Mapping[str, int | str], details)


def _freeze_work_metadata(value: object) -> Mapping[str, object]:
    mapping = _require_mapping(value, "payload")
    _reject_secret_shaped_keys(mapping)
    return MappingProxyType(
        {key: _freeze_work_metadata_value(key, item) for key, item in mapping.items()}
    )


def _freeze_work_metadata_value(key: str, value: object) -> object:
    if _WORK_UNIT_ID_FIELD.fullmatch(key):
        return _require_identifier(value, key)
    if _WORK_UNIT_ID_COLLECTION_FIELD.fullmatch(key):
        return _freeze_identifier_collection(value, key)
    if key in _WORK_UNIT_INTEGER_FIELDS:
        return _require_int_at_least(value, key, 0)
    if key in _WORK_UNIT_INTEGER_COLLECTION_FIELDS:
        return _freeze_non_negative_integer_collection(value, key)
    raise ValueError("payload has unsupported metadata key")


def _freeze_identifier_collection(value: object, name: str) -> tuple[str, ...]:
    if isinstance(value, str | bytes | bytearray) or not isinstance(value, Sequence):
        raise TypeError(f"{name} must be a sequence of safe identifiers")
    return tuple(_require_identifier(item, name) for item in value)


def _freeze_non_negative_integer_collection(value: object, name: str) -> tuple[int, ...]:
    if isinstance(value, str | bytes | bytearray) or not isinstance(value, Sequence):
        raise TypeError(f"{name} must be a sequence of non-negative integers")
    return tuple(_require_int_at_least(item, name, 0) for item in value)


def _as_execution_mode(value: object) -> ExecutionMode:
    if isinstance(value, ExecutionMode):
        return value
    if not isinstance(value, str):
        raise TypeError("execution_mode must be an ExecutionMode")
    try:
        return ExecutionMode(value)
    except ValueError:
        raise ValueError("execution_mode is not supported") from None


@dataclass(frozen=True, slots=True)
class EvaluationBudget:
    max_provider_requests: int
    max_total_tokens: int

    def __post_init__(self) -> None:
        _require_int_at_least(self.max_provider_requests, "max_provider_requests", 1)
        _require_int_at_least(self.max_total_tokens, "max_total_tokens", 1)


@dataclass(frozen=True, slots=True)
class EvaluationRequest:
    evaluation_type: str
    provider: str
    target_model: str
    dataset_ref: str
    credential_pool: str
    execution_mode: ExecutionMode
    max_workers: int
    max_attempts_per_unit: int
    budget: EvaluationBudget
    parameters: Mapping[str, object]

    def __post_init__(self) -> None:
        for name in (
            "evaluation_type",
            "provider",
            "target_model",
            "dataset_ref",
            "credential_pool",
        ):
            _require_identifier(getattr(self, name), name)
        if not isinstance(self.execution_mode, ExecutionMode):
            raise TypeError("execution_mode must be an ExecutionMode")
        _require_int_at_least(self.max_workers, "max_workers", 1)
        _require_int_at_least(self.max_attempts_per_unit, "max_attempts_per_unit", 1)
        if not isinstance(self.budget, EvaluationBudget):
            raise TypeError("budget must be an EvaluationBudget")
        object.__setattr__(self, "parameters", _freeze_safe_mapping(self.parameters, "parameters"))

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> EvaluationRequest:
        data = _require_mapping(payload, "payload")
        _require_exact_keys(
            data,
            "payload",
            frozenset(
                {
                    "evaluation_type",
                    "provider",
                    "target_model",
                    "dataset_ref",
                    "credential_pool",
                    "execution_mode",
                    "execution_options",
                    "budget",
                    "parameters",
                }
            ),
        )
        execution_options = _require_mapping(data["execution_options"], "execution_options")
        _reject_unknown_keys(
            execution_options,
            "execution_options",
            frozenset({"max_workers", "max_attempts_per_unit"}),
        )
        budget = _require_mapping(data["budget"], "budget")
        _require_exact_keys(
            budget,
            "budget",
            frozenset({"max_provider_requests", "max_total_tokens"}),
        )
        return cls(
            evaluation_type=_require_identifier(data["evaluation_type"], "evaluation_type"),
            provider=_require_identifier(data["provider"], "provider"),
            target_model=_require_identifier(data["target_model"], "target_model"),
            dataset_ref=_require_identifier(data["dataset_ref"], "dataset_ref"),
            credential_pool=_require_identifier(data["credential_pool"], "credential_pool"),
            execution_mode=_as_execution_mode(data["execution_mode"]),
            max_workers=_require_int_at_least(
                execution_options.get("max_workers", 1), "max_workers", 1
            ),
            max_attempts_per_unit=_require_int_at_least(
                execution_options.get("max_attempts_per_unit", DEFAULT_MAX_ATTEMPTS_PER_UNIT),
                "max_attempts_per_unit",
                1,
            ),
            budget=EvaluationBudget(
                max_provider_requests=_require_int_at_least(
                    budget["max_provider_requests"], "max_provider_requests", 1
                ),
                max_total_tokens=_require_int_at_least(
                    budget["max_total_tokens"], "max_total_tokens", 1
                ),
            ),
            parameters=_freeze_safe_mapping(data["parameters"], "parameters"),
        )


def canonical_request_hash(request: EvaluationRequest) -> str:
    """Return the deterministic idempotency hash for one fully validated request."""

    payload = {
        "evaluation_type": request.evaluation_type,
        "provider": request.provider,
        "target_model": request.target_model,
        "dataset_ref": request.dataset_ref,
        "credential_pool": request.credential_pool,
        "execution_mode": request.execution_mode.value,
        "max_workers": request.max_workers,
        "max_attempts_per_unit": request.max_attempts_per_unit,
        "budget": {
            "max_provider_requests": request.budget.max_provider_requests,
            "max_total_tokens": request.budget.max_total_tokens,
        },
        "parameters": _json_value(request.parameters),
    }
    canonical_json = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return sha256(canonical_json.encode("utf-8")).hexdigest()


def _json_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class PluginPlan:
    dataset_ref: str
    ready_work: int
    private_plan: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        _require_identifier(self.dataset_ref, "dataset_ref")
        _require_int_at_least(self.ready_work, "ready_work", 0)


@dataclass(frozen=True, slots=True)
class WorkUnit:
    unit_id: str
    ordinal: int
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        _require_identifier(self.unit_id, "unit_id")
        _require_int_at_least(self.ordinal, "ordinal", 0)
        object.__setattr__(self, "payload", _freeze_work_metadata(self.payload))


@dataclass(frozen=True, slots=True)
class WorkContext:
    job_id: str
    attempt_id: str
    lane_id: str
    credential_alias: str
    plugin_plan: PluginPlan = field(repr=False, compare=False)
    provider_client: object = field(repr=False, compare=False)
    scratch_dir: Path = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        for name in ("job_id", "attempt_id", "lane_id", "credential_alias"):
            _require_identifier(getattr(self, name), name)
        if not isinstance(self.plugin_plan, PluginPlan):
            raise TypeError("plugin_plan must be a PluginPlan")
        if not isinstance(self.scratch_dir, Path):
            raise TypeError("scratch_dir must be a Path")


@dataclass(frozen=True, slots=True)
class WorkUnitOutcome:
    unit_id: str
    ordinal: int
    state: UnitState
    provider_requests: int
    total_tokens: int
    private_result: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        _require_identifier(self.unit_id, "unit_id")
        _require_int_at_least(self.ordinal, "ordinal", 0)
        if not isinstance(self.state, UnitState):
            raise TypeError("state must be a UnitState")
        _require_int_at_least(self.provider_requests, "provider_requests", 0)
        _require_int_at_least(self.total_tokens, "total_tokens", 0)


@dataclass(frozen=True, slots=True)
class ArtifactBundle:
    public_result: Mapping[str, object]
    private_artifact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "public_result", _freeze_safe_mapping(self.public_result, "public_result")
        )
        if isinstance(self.private_artifact_ids, str) or not isinstance(
            self.private_artifact_ids, Sequence
        ):
            raise TypeError("private_artifact_ids must be a sequence of non-empty strings")
        private_artifact_ids = tuple(self.private_artifact_ids)
        if not all(
            isinstance(identifier, str) and identifier for identifier in private_artifact_ids
        ):
            raise ValueError("private_artifact_ids must contain non-empty strings")
        object.__setattr__(self, "private_artifact_ids", private_artifact_ids)


@dataclass(frozen=True, slots=True)
class EvaluationWarning:
    code: str
    details: Mapping[str, int | str]
    message: str = ""

    def __post_init__(self) -> None:
        _require_identifier(self.code, "code")
        expected_message = _WARNING_MESSAGES.get(self.code)
        if expected_message is None:
            raise ValueError("warning code is not supported")
        if self.message and self.message != expected_message:
            raise ValueError("warning message must match its code-owned template")
        object.__setattr__(self, "message", expected_message)
        object.__setattr__(self, "details", _freeze_warning_details(self.details))


@dataclass(frozen=True, slots=True)
class CleanupOutcome:
    removed_resources: int
    warnings: tuple[EvaluationWarning, ...]

    def __post_init__(self) -> None:
        _require_int_at_least(self.removed_resources, "removed_resources", 0)
        if isinstance(self.warnings, str) or not isinstance(self.warnings, Sequence):
            raise TypeError("warnings must be a sequence of EvaluationWarning instances")
        warnings = tuple(self.warnings)
        if not all(isinstance(warning, EvaluationWarning) for warning in warnings):
            raise TypeError("warnings must contain EvaluationWarning instances")
        object.__setattr__(self, "warnings", warnings)


@dataclass(frozen=True, slots=True)
class FailureClassification:
    failure_class: FailureClass
    retryable: bool
    credential_state: CredentialState | None

    def __post_init__(self) -> None:
        if not isinstance(self.failure_class, FailureClass):
            raise TypeError("failure_class must be a FailureClass")
        if not isinstance(self.retryable, bool):
            raise TypeError("retryable must be a boolean")
        if self.credential_state is not None and not isinstance(
            self.credential_state, CredentialState
        ):
            raise TypeError("credential_state must be a CredentialState or None")


@dataclass(frozen=True, slots=True)
class WorkerResolution:
    requested_workers: int
    effective_workers: int
    healthy_credentials: int
    ready_work: int
    warning: EvaluationWarning | None

    def __post_init__(self) -> None:
        _require_int_at_least(self.requested_workers, "requested_workers", 1)
        _require_int_at_least(self.effective_workers, "effective_workers", 0)
        _require_int_at_least(self.healthy_credentials, "healthy_credentials", 0)
        _require_int_at_least(self.ready_work, "ready_work", 0)
        if self.warning is not None and not isinstance(self.warning, EvaluationWarning):
            raise TypeError("warning must be an EvaluationWarning or None")


@dataclass(frozen=True, slots=True)
class ProviderAttemptEvent:
    credential_alias: str
    request_attempt_id: str
    outcome: str
    status_code: int | None
    retry_after_seconds: int | None
    latency_ms: int

    def __post_init__(self) -> None:
        _require_identifier(self.credential_alias, "credential_alias")
        _require_identifier(self.request_attempt_id, "request_attempt_id")
        _require_identifier(self.outcome, "outcome")
        if self.status_code is not None:
            _require_int_at_least(self.status_code, "status_code", 100)
        if self.retry_after_seconds is not None:
            _require_int_at_least(self.retry_after_seconds, "retry_after_seconds", 0)
        _require_int_at_least(self.latency_ms, "latency_ms", 0)


class EvaluationPlugin(Protocol):
    """Static extension point for evaluation semantics, not execution policy."""

    evaluation_type: str
    version: str
    supported_modes: frozenset[ExecutionMode]
    parameter_schema: Mapping[str, object]

    async def preflight(self, request: EvaluationRequest) -> PluginPlan: ...

    def build_work_units(self, plan: PluginPlan, lane_count: int) -> tuple[WorkUnit, ...]: ...

    async def execute_work(self, unit: WorkUnit, context: WorkContext) -> WorkUnitOutcome: ...

    def aggregate(
        self, plan: PluginPlan, outcomes: Sequence[WorkUnitOutcome]
    ) -> ArtifactBundle: ...

    async def cleanup(self, context: WorkContext) -> CleanupOutcome: ...

    def classify_failure(self, error: BaseException) -> FailureClassification: ...
