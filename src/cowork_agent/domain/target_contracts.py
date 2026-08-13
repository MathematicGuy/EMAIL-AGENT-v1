"""Versioned target contracts for the Email Action Plan workflow.

Pure-stdlib domain contracts implementing Step 6 of
``docs/architectures/TARGET-ARCHITECTURE.md``:

- §6.1 ``EphemeralEmailEnvelope`` — the Ephemeral Envelope
- §6.2 ``EmailRouteDecision`` — the Classifier's Route Decision
- §6.4/§6.5 ``SemanticRetrievalRequest``/``SemanticRetrievalResponse`` —
  the retrieval-only Semantic Memory boundary
- §6.6 ``ActionPlanOutput``, ``Task``, ``PlanStep``, ``SupportingDocument``
- §6.8 ``TraceEvent`` with ``TraceLatency``

:data:`TARGET_CONTRACTS_VERSION` versions this contract set; the dataclasses
carry no per-instance version fields. Contracts assigned to later milestones
by ``tasks/plan.md`` (``TaskEpisode``, ``MemoryContextRequest``) are
intentionally absent.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from enum import Enum, StrEnum
from typing import Literal, Self, TypeVar

from .models import Priority

TARGET_CONTRACTS_VERSION = "1.1.0"

#: Pipeline version — fourth component of the idempotent task persistence key
#: ``tenant_id:user_id:gmail_message_id:pipeline_version`` (V1-M4 T4.1).
#: Bump whenever persisted Task semantics change so replays never collide
#: with rows written by an older pipeline. "2": rows may now carry the
#: system-injected FR-11 missing-context note (§15 gate remediation).
TASK_PIPELINE_VERSION = "2"


class Actionability(StrEnum):
    """Classifier actionability label for one email (§6.2)."""

    ACTION_REQUIRED = "action_required"
    ACTION_SUGGESTED = "action_suggested"
    INFORMATIONAL = "informational"
    UNCLEAR = "unclear"
    IRRELEVANT = "irrelevant"


class Route(StrEnum):
    """Execution path resolved deterministically by the Route Resolver (§6.2)."""

    NO_ACTION = "no_action"
    DIRECT_PLAN = "direct_plan"
    RETRIEVE_RAG = "retrieve_rag"


class ReasonCode(StrEnum):
    """Reason codes attached to a Route Decision (§6.2)."""

    NO_ACTION = "no_action"
    EMAIL_SELF_CONTAINED = "email_self_contained"
    COMPANY_PROCEDURE_REQUIRED = "company_procedure_required"
    GOVERNANCE_REQUIRED = "governance_required"
    POLICY_REQUIRED = "policy_required"
    TEMPLATE_REQUIRED = "template_required"
    INTERNAL_TERM_UNRESOLVED = "internal_term_unresolved"
    DOMAIN_KNOWLEDGE_REQUIRED = "domain_knowledge_required"


class ExpectedDocumentType(StrEnum):
    """Document categories the Classifier expects retrieval to find (§6.2)."""

    COMPANY_POLICY = "company_policy"
    GOVERNANCE_DOCUMENT = "governance_document"
    PROCEDURE = "procedure"
    GUIDELINE = "guideline"
    TEMPLATE = "template"
    PRODUCT_DOCUMENTATION = "product_documentation"


class BodyFormat(StrEnum):
    """Normalization format of the Ephemeral Envelope body (§6.1)."""

    TEXT = "text"
    HTML_CONVERTED = "html_converted"


class FetchStatus(StrEnum):
    """Completeness of the Gmail fetch behind one Ephemeral Envelope (§6.1)."""

    COMPLETE = "complete"
    PARTIAL = "partial"


class ValidationStatus(StrEnum):
    """Shared validation lifecycle state for Task and Episode (§6.6, §6.7)."""

    SYSTEM_GENERATED = "system_generated"
    USER_APPROVED = "user_approved"
    COMPLETED = "completed"
    REJECTED = "rejected"


class TraceStatus(StrEnum):
    """Outcome status of a trace event (§6.8)."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class RetrievalStatus(StrEnum):
    """Outcome status of one semantic retrieval call (§6.5)."""

    SUCCESS = "success"
    NO_RESULTS = "no_results"
    TIMEOUT = "timeout"
    AUTHORIZATION_DENIED = "authorization_denied"
    PARTIAL = "partial"


_T = TypeVar("_T")
_E = TypeVar("_E", bound=Enum)


def _jsonable(value: object) -> object:
    """Convert one contract field value to its JSON-safe representation."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return _to_dict(value)
    return value


def _to_dict(instance: object) -> dict[str, object]:
    """Serialize every field of a contract dataclass via `_jsonable`."""
    if not is_dataclass(instance) or isinstance(instance, type):
        raise TypeError(f"Expected a dataclass instance, got {type(instance).__name__}")
    return {field.name: _jsonable(getattr(instance, field.name)) for field in fields(instance)}


def _as_str(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"Expected string, got {type(value).__name__}")
    return value


def _as_bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"Expected boolean, got {type(value).__name__}")
    return value


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"Expected integer, got {type(value).__name__}")
    return value


def _as_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"Expected number, got {type(value).__name__}")
    return float(value)


def _as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError(f"Expected datetime or ISO-8601 string, got {type(value).__name__}")


def _as_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"Expected mapping, got {type(value).__name__}")
    return value


def _as_sequence(value: object) -> Sequence[object]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise TypeError(f"Expected sequence, got {type(value).__name__}")
    return value


def _as_str_tuple(value: object) -> tuple[str, ...]:
    return tuple(_as_str(item) for item in _as_sequence(value))


def _as_enum(value: object, enum_type: type[_E]) -> _E:
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise TypeError(f"Expected a {enum_type.__name__} value, got {type(value).__name__}")
    return enum_type(value)


def _as_enum_tuple(value: object, enum_type: type[_E]) -> tuple[_E, ...]:
    return tuple(_as_enum(item, enum_type) for item in _as_sequence(value))


def _optional(value: object, parse: Callable[[object], _T]) -> _T | None:
    if value is None:
        return None
    return parse(value)


@dataclass(frozen=True, slots=True)
class EphemeralEmailEnvelope:
    """Ephemeral Envelope (§6.1): one normalized Gmail message.

    Exists only as current-run state and is deleted at run completion. The
    yaml "Lifecycle" block (scope, TTL, cleanup) describes runtime behavior
    owned by the run finalizer, not envelope data.
    """

    run_id: str
    tenant_id: str
    user_id: str
    gmail_message_id: str
    gmail_thread_id: str
    gmail_url: str
    sender_name: str
    sender_email: str
    recipients: tuple[str, ...]
    subject: str
    received_at: datetime
    labels: tuple[str, ...]
    normalized_body: str
    body_format: BodyFormat
    attachments_present: bool
    fetch_status: FetchStatus
    # Always False: attachment processing is out of scope (ADR-003); presence
    # is recorded only. Kept last because it is the only defaulted field.
    attachments_processed: Literal[False] = False

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        if data.get("attachments_processed", False):
            raise ValueError(
                "attachments_processed must be False: attachment processing is out of scope"
            )
        return cls(
            run_id=_as_str(data["run_id"]),
            tenant_id=_as_str(data["tenant_id"]),
            user_id=_as_str(data["user_id"]),
            gmail_message_id=_as_str(data["gmail_message_id"]),
            gmail_thread_id=_as_str(data["gmail_thread_id"]),
            gmail_url=_as_str(data["gmail_url"]),
            sender_name=_as_str(data["sender_name"]),
            sender_email=_as_str(data["sender_email"]),
            recipients=_as_str_tuple(data["recipients"]),
            subject=_as_str(data["subject"]),
            received_at=_as_datetime(data["received_at"]),
            labels=_as_str_tuple(data["labels"]),
            normalized_body=_as_str(data["normalized_body"]),
            body_format=_as_enum(data["body_format"], BodyFormat),
            attachments_present=_as_bool(data["attachments_present"]),
            fetch_status=_as_enum(data["fetch_status"], FetchStatus),
        )


@dataclass(frozen=True, slots=True)
class EmailRouteDecision:
    """Route Decision (§6.2): the Classifier's structured per-email output.

    Proposed by the Classifier; the deterministic Route Resolver verifies
    consistency and applies hard rules before selecting the Route.
    """

    actionability: Actionability
    route: Route
    candidate_action_item: str | None
    email_is_sufficient: bool
    knowledge_gaps: tuple[str, ...]
    retrieval_query: str | None
    expected_document_types: tuple[ExpectedDocumentType, ...]
    reason_codes: tuple[ReasonCode, ...]
    confidence: float

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        return cls(
            actionability=_as_enum(data["actionability"], Actionability),
            route=_as_enum(data["route"], Route),
            candidate_action_item=_optional(data["candidate_action_item"], _as_str),
            email_is_sufficient=_as_bool(data["email_is_sufficient"]),
            knowledge_gaps=_as_str_tuple(data["knowledge_gaps"]),
            retrieval_query=_optional(data["retrieval_query"], _as_str),
            expected_document_types=_as_enum_tuple(
                data["expected_document_types"], ExpectedDocumentType
            ),
            reason_codes=_as_enum_tuple(data["reason_codes"], ReasonCode),
            confidence=_as_float(data["confidence"]),
        )


@dataclass(frozen=True, slots=True)
class PlanStep:
    """One ordered Action Plan step for a Task (§6.6 ``action_plan`` item).

    Named ``PlanStep`` to avoid collision with the legacy
    ``cowork_agent.domain.models.ActionPlanStep``.
    """

    step: int
    instruction: str
    supporting_citation_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        return cls(
            step=_as_int(data["step"]),
            instruction=_as_str(data["instruction"]),
            supporting_citation_ids=_as_str_tuple(data["supporting_citation_ids"]),
        )


@dataclass(frozen=True, slots=True)
class SupportingDocument:
    """Citation to one retrieved company document chunk (§6.6)."""

    citation_id: str
    document_id: str
    title: str
    section: str | None
    url: str
    relevance_score: float

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        return cls(
            citation_id=_as_str(data["citation_id"]),
            document_id=_as_str(data["document_id"]),
            title=_as_str(data["title"]),
            section=_optional(data["section"], _as_str),
            url=_as_str(data["url"]),
            relevance_score=_as_float(data["relevance_score"]),
        )


@dataclass(frozen=True, slots=True)
class Task:
    """Task (§6.6): the minimal durable artifact per actionable result.

    Carries the Gmail pointer, Action Plan, Citations, missing information,
    and the qualified ``classifier_confidence`` / ``generation_confidence``.
    """

    task_id: str
    run_id: str
    gmail_message_id: str
    gmail_url: str
    source_message_ids: tuple[str, ...]
    incident_key: str | None
    title: str
    request_summary: str
    actionability: Actionability
    route: Route
    priority: Priority | None
    deadline: datetime | None
    action_plan: tuple[PlanStep, ...]
    supporting_documents: tuple[SupportingDocument, ...]
    missing_information: tuple[str, ...]
    classifier_confidence: float
    generation_confidence: float | None
    validation_status: ValidationStatus
    created_at: datetime

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        return cls(
            task_id=_as_str(data["task_id"]),
            run_id=_as_str(data["run_id"]),
            gmail_message_id=_as_str(data["gmail_message_id"]),
            gmail_url=_as_str(data["gmail_url"]),
            source_message_ids=_as_str_tuple(data["source_message_ids"]),
            incident_key=_optional(data["incident_key"], _as_str),
            title=_as_str(data["title"]),
            request_summary=_as_str(data["request_summary"]),
            actionability=_as_enum(data["actionability"], Actionability),
            route=_as_enum(data["route"], Route),
            priority=_optional(data["priority"], lambda value: _as_enum(value, Priority)),
            deadline=_optional(data["deadline"], _as_datetime),
            action_plan=tuple(
                PlanStep.from_dict(_as_mapping(item)) for item in _as_sequence(data["action_plan"])
            ),
            supporting_documents=tuple(
                SupportingDocument.from_dict(_as_mapping(item))
                for item in _as_sequence(data["supporting_documents"])
            ),
            missing_information=_as_str_tuple(data["missing_information"]),
            classifier_confidence=_as_float(data["classifier_confidence"]),
            generation_confidence=_optional(data["generation_confidence"], _as_float),
            validation_status=_as_enum(data["validation_status"], ValidationStatus),
            created_at=_as_datetime(data["created_at"]),
        )


@dataclass(frozen=True, slots=True)
class ActionPlanOutput:
    """Generator output contract (§6.6): one Task per resolved Task Candidate.

    The §6.6 validation-rule bullets are validator concerns (V1-M3) and are
    not implemented on this contract.
    """

    task: Task

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        return cls(task=Task.from_dict(_as_mapping(data["task"])))


@dataclass(frozen=True, slots=True)
class TraceLatency:
    """Stage latencies in milliseconds attached to a trace event (§6.8)."""

    email: int | None = None
    memory: int | None = None
    classifier: int | None = None
    rag: int | None = None
    generation: int | None = None
    persistence: int | None = None

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        return cls(
            email=_optional(data.get("email"), _as_int),
            memory=_optional(data.get("memory"), _as_int),
            classifier=_optional(data.get("classifier"), _as_int),
            rag=_optional(data.get("rag"), _as_int),
            generation=_optional(data.get("generation"), _as_int),
            persistence=_optional(data.get("persistence"), _as_int),
        )


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """Metadata-only trace event (§6.8).

    ``reason_codes`` are plain strings here because §6.8 serializes them
    without an enum constraint.
    """

    run_id: str
    tenant_id: str
    user_id: str
    gmail_message_id: str | None
    event_name: str
    status: TraceStatus
    route: Route | None
    reason_codes: tuple[str, ...]
    classifier_confidence: float | None
    rag_result_count: int | None
    retrieval_status: str | None
    generation_status: str | None
    validation_status: str | None
    latency_ms: TraceLatency

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        return cls(
            run_id=_as_str(data["run_id"]),
            tenant_id=_as_str(data["tenant_id"]),
            user_id=_as_str(data["user_id"]),
            gmail_message_id=_optional(data["gmail_message_id"], _as_str),
            event_name=_as_str(data["event_name"]),
            status=_as_enum(data["status"], TraceStatus),
            route=_optional(data["route"], lambda value: _as_enum(value, Route)),
            reason_codes=_as_str_tuple(data["reason_codes"]),
            classifier_confidence=_optional(data["classifier_confidence"], _as_float),
            rag_result_count=_optional(data["rag_result_count"], _as_int),
            retrieval_status=_optional(data["retrieval_status"], _as_str),
            generation_status=_optional(data["generation_status"], _as_str),
            validation_status=_optional(data["validation_status"], _as_str),
            latency_ms=TraceLatency.from_dict(_as_mapping(data["latency_ms"])),
        )


@dataclass(frozen=True, slots=True)
class SemanticChunk:
    """One retrieved company-knowledge chunk with citation metadata (§6.5)."""

    chunk_id: str
    document_id: str
    document_title: str
    section: str | None
    text: str
    source_url: str
    document_version: str | None
    relevance_score: float
    rerank_score: float | None

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        return cls(
            chunk_id=_as_str(data["chunk_id"]),
            document_id=_as_str(data["document_id"]),
            document_title=_as_str(data["document_title"]),
            section=_optional(data["section"], _as_str),
            text=_as_str(data["text"]),
            source_url=_as_str(data["source_url"]),
            document_version=_optional(data["document_version"], _as_str),
            relevance_score=_as_float(data["relevance_score"]),
            rerank_score=_optional(data["rerank_score"], _as_float),
        )


@dataclass(frozen=True, slots=True)
class RetrievalFilters:
    """Namespace/status filters applied to a semantic retrieval (§6.4)."""

    tenant_scope: str
    document_status: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        return cls(
            tenant_scope=_as_str(data["tenant_scope"]),
            document_status=_as_str_tuple(data["document_status"]),
        )


@dataclass(frozen=True, slots=True)
class RetrievalLimits:
    """Bounded retrieval limits (§6.4)."""

    top_k: int
    min_score: float
    timeout_ms: int

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        return cls(
            top_k=_as_int(data["top_k"]),
            min_score=_as_float(data["min_score"]),
            timeout_ms=_as_int(data["timeout_ms"]),
        )


@dataclass(frozen=True, slots=True)
class SemanticRetrievalRequest:
    """Retrieval-only Semantic Memory request (§6.4).

    Constraint from master-comparison §6.4: the response returns chunks,
    citation metadata, and scores only — never a generated Action Plan.
    """

    run_id: str
    tenant_id: str
    user_id: str
    query: str
    knowledge_gaps: tuple[str, ...]
    filters: RetrievalFilters
    limits: RetrievalLimits

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        return cls(
            run_id=_as_str(data["run_id"]),
            tenant_id=_as_str(data["tenant_id"]),
            user_id=_as_str(data["user_id"]),
            query=_as_str(data["query"]),
            knowledge_gaps=_as_str_tuple(data["knowledge_gaps"]),
            filters=RetrievalFilters.from_dict(_as_mapping(data["filters"])),
            limits=RetrievalLimits.from_dict(_as_mapping(data["limits"])),
        )


@dataclass(frozen=True, slots=True)
class SemanticRetrievalResponse:
    """Structured semantic retrieval result (§6.5)."""

    query_id: str
    tenant_id: str
    chunks: tuple[SemanticChunk, ...]
    retrieval_status: RetrievalStatus
    latency_ms: int

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        return cls(
            query_id=_as_str(data["query_id"]),
            tenant_id=_as_str(data["tenant_id"]),
            chunks=tuple(
                SemanticChunk.from_dict(_as_mapping(item)) for item in _as_sequence(data["chunks"])
            ),
            retrieval_status=_as_enum(data["retrieval_status"], RetrievalStatus),
            latency_ms=_as_int(data["latency_ms"]),
        )


# §6.8 ``content_policy`` is trace policy, not event data: production traces
# stay metadata-only, and full-content traces are a development-only exception
# that requires the mandated marker and a TTL.
TRACE_CONTENT_POLICY_PRODUCTION = "metadata_only"
TRACE_CONTENT_POLICY_DEVELOPMENT = "full_content_allowed"
TRACE_DEVELOPMENT_MARKER = "ALLOW ONLY FOR CURRENT DEVELOPMENT STAGE"
