"""Memory namespace, context, profile, episode, and provenance contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Literal, Self, cast

from ._chat_contracts_common import (
    AI_CHAT_FEATURE,
    MAX_CHAT_SUMMARY_LENGTH,
    MAX_EPISODIC_RETRIEVAL_ITEMS,
    MAX_RETRIEVAL_QUERY_LENGTH,
    MAX_RETRIEVAL_TIMEOUT_MS,
    MAX_SEMANTIC_RETRIEVAL_ITEMS,
    DegradedMemorySource,
    EpisodeSourceType,
    MemoryType,
    _as_datetime,
    _as_enum,
    _as_mapping,
    _as_sequence,
    _frozen_mapping,
    _reject_raw_email_shaped_keys,
    _require_bounded_string,
    _require_key_component,
    _require_string,
    _to_dict,
)
from .target_contracts import ValidationStatus

MAX_TASK_TITLE_LENGTH = 200
MAX_TASK_REQUEST_PARAPHRASE_LENGTH = 1_000
MAX_TASK_ACTION_PLAN_ITEMS = 20
MAX_TASK_ACTION_PLAN_ITEM_LENGTH = 500
MAX_TASK_MISSING_INFORMATION_ITEMS = 20
MAX_TASK_MISSING_INFORMATION_ITEM_LENGTH = 500
MAX_TASK_RAG_CITATIONS = 20
MAX_EPISODE_CITATION_DOCUMENT_ID_LENGTH = 256
MAX_EPISODE_CITATION_DOCUMENT_TITLE_LENGTH = 300
MAX_EPISODE_CITATION_SECTION_LENGTH = 300
MAX_EPISODE_CITATION_SOURCE_URL_LENGTH = 2_048


@dataclass(frozen=True, slots=True)
class ChatMemoryScope:
    """Aggregate fail-closed chat scope used before selecting one memory type."""

    tenant_id: str
    user_id: str
    session_id: str
    feature: str = AI_CHAT_FEATURE

    def __post_init__(self) -> None:
        _require_key_component(self.tenant_id, "tenant_id")
        _require_key_component(self.user_id, "user_id")
        _require_key_component(self.session_id, "session_id")
        if self.feature != AI_CHAT_FEATURE:
            raise ValueError(f"feature must be {AI_CHAT_FEATURE!r}")

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        return cls(
            tenant_id=_require_key_component(data["tenant_id"], "tenant_id"),
            user_id=_require_key_component(data["user_id"], "user_id"),
            session_id=_require_key_component(data["session_id"], "session_id"),
            feature=_require_string(data["feature"], "feature"),
        )


@dataclass(frozen=True, slots=True)
class MemoryNamespace:
    """Concrete memory-operation namespace: scope plus memory type and identifiers."""

    scope: ChatMemoryScope
    memory_type: MemoryType
    record_id: str | None
    source_id: str | None

    def __post_init__(self) -> None:
        if self.record_id is not None:
            _require_key_component(self.record_id, "record_id")
        if self.source_id is not None:
            _require_string(self.source_id, "source_id")

    @property
    def tenant_id(self) -> str:
        return self.scope.tenant_id

    @property
    def user_id(self) -> str:
        return self.scope.user_id

    @property
    def session_id(self) -> str:
        return self.scope.session_id

    @property
    def feature(self) -> str:
        return self.scope.feature

    def logical_key(self) -> str:
        """Build the documented storage key only for a concrete record."""

        if self.record_id is None:
            raise ValueError("record_id is required to construct a logical key")
        return "/".join(
            (
                self.tenant_id,
                self.user_id,
                self.session_id,
                self.feature,
                self.memory_type.value,
                self.record_id,
            )
        )

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        record_id = data["record_id"]
        source_id = data["source_id"]
        return cls(
            scope=ChatMemoryScope.from_dict(_as_mapping(data["scope"], "scope")),
            memory_type=_as_enum(data["memory_type"], MemoryType, "memory_type"),
            record_id=(
                _require_key_component(record_id, "record_id") if record_id is not None else None
            ),
            source_id=_require_string(source_id, "source_id") if source_id is not None else None,
        )


@dataclass(frozen=True, slots=True)
class EpisodicMemoryRead:
    """Legacy disabled episodic read intent."""

    enabled: bool
    retrieval_eligible_only: Literal[True]
    max_items: int

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be a boolean")
        if self.enabled is not False:
            raise ValueError("enabled episodic reads require EpisodicMemoryQuery")
        if self.retrieval_eligible_only is not True:
            raise ValueError("retrieval_eligible_only must be True")
        if isinstance(self.max_items, bool) or self.max_items < 1:
            raise ValueError("max_items must be a positive integer")

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        max_items = data["max_items"]
        if isinstance(max_items, bool) or not isinstance(max_items, int):
            raise TypeError("max_items must be an integer")
        eligible = data["retrieval_eligible_only"]
        if not isinstance(eligible, bool):
            raise TypeError("retrieval_eligible_only must be a boolean")
        if eligible is not True:
            raise ValueError("retrieval_eligible_only must be True")
        enabled = data["enabled"]
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a boolean")
        return cls(
            enabled=enabled,
            retrieval_eligible_only=eligible,
            max_items=max_items,
        )


@dataclass(frozen=True, slots=True)
class SemanticMemoryRead:
    """Legacy disabled semantic read intent."""

    enabled: bool
    condition: Literal["chat_intent_requires_enterprise_context"] = (
        "chat_intent_requires_enterprise_context"
    )

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be a boolean")
        if self.enabled is not False:
            raise ValueError("enabled semantic reads require SemanticMemoryQuery")
        if self.condition != "chat_intent_requires_enterprise_context":
            raise ValueError("semantic condition is fixed by the contract")

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        enabled = data["enabled"]
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a boolean")
        condition = data.get("condition", "chat_intent_requires_enterprise_context")
        if not isinstance(condition, str):
            raise TypeError("condition must be a string")
        if condition != "chat_intent_requires_enterprise_context":
            raise ValueError("semantic condition is fixed by the contract")
        return cls(
            enabled=enabled,
            condition=cast(Literal["chat_intent_requires_enterprise_context"], condition),
        )


def _require_retrieval_max_items(value: object, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("max_items must be an integer")
    if not 1 <= value <= maximum:
        raise ValueError(f"max_items must be between 1 and {maximum}")
    return value


def _require_retrieval_score(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("min_score must be a number")
    score = float(value)
    if not 0.0 <= score <= 1.0:
        raise ValueError("min_score must be between 0 and 1")
    return score


def _require_retrieval_timeout(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("timeout_ms must be an integer")
    if not 1 <= value <= MAX_RETRIEVAL_TIMEOUT_MS:
        raise ValueError(f"timeout_ms must be between 1 and {MAX_RETRIEVAL_TIMEOUT_MS}")
    return value


@dataclass(frozen=True, slots=True)
class _QueryScopedMemoryRead:
    """Shared invariant-bearing base for the explicit retrieval variants."""

    query: str
    max_items: int
    min_score: float
    timeout_ms: int
    _max_items: ClassVar[int]
    _fixed_filter_name: ClassVar[str]
    _fixed_filter_value: ClassVar[object]

    @property
    def enabled(self) -> Literal[True]:
        """Compatibility discriminator for callers selecting the read variant."""

        return True

    def __post_init__(self) -> None:
        _require_bounded_string(self.query, "query", MAX_RETRIEVAL_QUERY_LENGTH)
        _require_retrieval_max_items(self.max_items, self._max_items)
        _require_retrieval_score(self.min_score)
        _require_retrieval_timeout(self.timeout_ms)

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": True,
            "query": self.query,
            self._fixed_filter_name: self._fixed_filter_value,
            "max_items": self.max_items,
            "min_score": self.min_score,
            "timeout_ms": self.timeout_ms,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        if data["enabled"] is not True:
            raise ValueError("enabled must be True for a retrieval query")
        fixed_value = data[cls._fixed_filter_name]
        if isinstance(cls._fixed_filter_value, bool):
            if fixed_value is not cls._fixed_filter_value:
                raise ValueError(f"{cls._fixed_filter_name} is fixed by the contract")
        elif fixed_value != cls._fixed_filter_value:
            raise ValueError(f"{cls._fixed_filter_name} is fixed by the contract")
        return cls(
            query=_require_bounded_string(
                data["query"], "query", MAX_RETRIEVAL_QUERY_LENGTH
            ),
            max_items=_require_retrieval_max_items(data["max_items"], cls._max_items),
            min_score=_require_retrieval_score(data["min_score"]),
            timeout_ms=_require_retrieval_timeout(data["timeout_ms"]),
        )


class EpisodicMemoryQuery(_QueryScopedMemoryRead):
    """Explicit, bounded request for eligible episodic context only."""

    _max_items = MAX_EPISODIC_RETRIEVAL_ITEMS
    _fixed_filter_name = "retrieval_eligible_only"
    _fixed_filter_value = True


class SemanticMemoryQuery(_QueryScopedMemoryRead):
    """Explicit, bounded request for enterprise semantic context only."""

    _max_items = MAX_SEMANTIC_RETRIEVAL_ITEMS
    _fixed_filter_name = "condition"
    _fixed_filter_value = "chat_intent_requires_enterprise_context"


@dataclass(frozen=True, slots=True)
class MemoryReadOptions:
    """The four memory reads a Chat Controller may request through the gateway."""

    short_term: bool
    long_term: bool
    episodic: EpisodicMemoryRead | EpisodicMemoryQuery
    semantic: SemanticMemoryRead | SemanticMemoryQuery

    def __post_init__(self) -> None:
        if not isinstance(self.short_term, bool) or not isinstance(self.long_term, bool):
            raise TypeError("short_term and long_term must be booleans")
        if not isinstance(self.episodic, EpisodicMemoryRead | EpisodicMemoryQuery):
            raise TypeError("episodic must be an episodic read contract")
        if not isinstance(self.semantic, SemanticMemoryRead | SemanticMemoryQuery):
            raise TypeError("semantic must be a semantic read contract")

    def to_dict(self) -> dict[str, object]:
        return {
            "short_term": self.short_term,
            "long_term": self.long_term,
            "episodic": self.episodic.to_dict(),
            "semantic": self.semantic.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        short_term = data["short_term"]
        long_term = data["long_term"]
        if not isinstance(short_term, bool) or not isinstance(long_term, bool):
            raise TypeError("short_term and long_term must be booleans")
        episodic_data = _as_mapping(data["episodic"], "episodic")
        semantic_data = _as_mapping(data["semantic"], "semantic")
        episodic_enabled = episodic_data["enabled"]
        semantic_enabled = semantic_data["enabled"]
        if not isinstance(episodic_enabled, bool) or not isinstance(semantic_enabled, bool):
            raise TypeError("retrieval enabled discriminators must be booleans")
        return cls(
            short_term=short_term,
            long_term=long_term,
            episodic=(
                EpisodicMemoryQuery.from_dict(episodic_data)
                if episodic_enabled
                else EpisodicMemoryRead.from_dict(episodic_data)
            ),
            semantic=(
                SemanticMemoryQuery.from_dict(semantic_data)
                if semantic_enabled
                else SemanticMemoryRead.from_dict(semantic_data)
            ),
        )


@dataclass(frozen=True, slots=True)
class MemoryContextRequest:
    """One scoped request for optional chat memory context (§6.5)."""

    session_id: str
    scope: ChatMemoryScope
    reads: MemoryReadOptions

    def __post_init__(self) -> None:
        _require_string(self.session_id, "session_id")
        if self.session_id != self.scope.session_id:
            raise ValueError("session_id must match scope.session_id")

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "scope": self.scope.to_dict(),
            "reads": self.reads.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        return cls(
            session_id=_require_string(data["session_id"], "session_id"),
            scope=ChatMemoryScope.from_dict(_as_mapping(data["scope"], "scope")),
            reads=MemoryReadOptions.from_dict(_as_mapping(data["reads"], "reads")),
        )


@dataclass(frozen=True, slots=True)
class EpisodeCitation:
    """Body-free pointer to RAG evidence used by one task episode."""

    document_id: str
    document_title: str
    section: str | None
    source_url: str

    def __post_init__(self) -> None:
        _require_bounded_string(
            self.document_id, "document_id", MAX_EPISODE_CITATION_DOCUMENT_ID_LENGTH
        )
        _require_bounded_string(
            self.document_title, "document_title", MAX_EPISODE_CITATION_DOCUMENT_TITLE_LENGTH
        )
        _require_bounded_string(
            self.source_url, "source_url", MAX_EPISODE_CITATION_SOURCE_URL_LENGTH
        )
        if self.section is not None:
            _require_bounded_string(self.section, "section", MAX_EPISODE_CITATION_SECTION_LENGTH)

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        _reject_raw_email_shaped_keys(data)
        expected_fields = {"document_id", "document_title", "section", "source_url"}
        unexpected_fields = set(data).difference(expected_fields)
        if unexpected_fields:
            raise ValueError(
                f"unexpected field(s) for EpisodeCitation: {sorted(unexpected_fields)}"
            )
        section = data["section"]
        return cls(
            document_id=_require_bounded_string(
                data["document_id"], "document_id", MAX_EPISODE_CITATION_DOCUMENT_ID_LENGTH
            ),
            document_title=_require_bounded_string(
                data["document_title"],
                "document_title",
                MAX_EPISODE_CITATION_DOCUMENT_TITLE_LENGTH,
            ),
            section=(
                _require_bounded_string(section, "section", MAX_EPISODE_CITATION_SECTION_LENGTH)
                if section is not None
                else None
            ),
            source_url=_require_bounded_string(
                data["source_url"], "source_url", MAX_EPISODE_CITATION_SOURCE_URL_LENGTH
            ),
        )


@dataclass(frozen=True, slots=True)
class TaskEpisode:
    """Body-free task proposal created from an explicit AI Chat request."""

    episode_id: str
    record_id: str
    tenant_id: str
    user_id: str
    chat_session_id: str
    chat_turn_id: str
    creation_reason: Literal["explicit_user_task_request"]
    task_title: str
    minimal_request_paraphrase: str
    action_plan: tuple[str, ...]
    rag_citations: tuple[EpisodeCitation, ...]
    missing_information: tuple[str, ...]
    validation_status: ValidationStatus
    retrieval_eligible: bool
    source_type: EpisodeSourceType
    created_at: datetime
    updated_at: datetime
    pipeline_version: str
    model_id: str | None
    prompt_version: str | None
    confidence: float | None

    def __post_init__(self) -> None:
        for name in (
            "episode_id",
            "record_id",
            "tenant_id",
            "user_id",
            "chat_session_id",
            "chat_turn_id",
            "pipeline_version",
        ):
            _require_string(getattr(self, name), name)
        _require_bounded_string(self.task_title, "task_title", MAX_TASK_TITLE_LENGTH)
        _require_bounded_string(
            self.minimal_request_paraphrase,
            "minimal_request_paraphrase",
            MAX_TASK_REQUEST_PARAPHRASE_LENGTH,
        )
        if self.creation_reason != "explicit_user_task_request":
            raise ValueError("creation_reason must be explicit_user_task_request")
        _reject_raw_email_shaped_keys(self.action_plan)
        _reject_raw_email_shaped_keys(self.rag_citations)
        _reject_raw_email_shaped_keys(self.missing_information)
        action_plan = _as_sequence(self.action_plan, "action_plan")
        if len(action_plan) > MAX_TASK_ACTION_PLAN_ITEMS:
            raise ValueError(
                f"action_plan must not contain more than {MAX_TASK_ACTION_PLAN_ITEMS} items"
            )
        object.__setattr__(
            self,
            "action_plan",
            tuple(
                _require_bounded_string(
                    item, "action_plan item", MAX_TASK_ACTION_PLAN_ITEM_LENGTH
                )
                for item in action_plan
            ),
        )
        rag_citations = _as_sequence(self.rag_citations, "rag_citations")
        if len(rag_citations) > MAX_TASK_RAG_CITATIONS:
            raise ValueError(
                f"rag_citations must not contain more than {MAX_TASK_RAG_CITATIONS} items"
            )
        if not all(isinstance(citation, EpisodeCitation) for citation in rag_citations):
            raise TypeError("rag_citations items must be EpisodeCitation instances")
        object.__setattr__(self, "rag_citations", tuple(rag_citations))
        missing_information = _as_sequence(self.missing_information, "missing_information")
        if len(missing_information) > MAX_TASK_MISSING_INFORMATION_ITEMS:
            raise ValueError(
                "missing_information must not contain more than "
                f"{MAX_TASK_MISSING_INFORMATION_ITEMS} items"
            )
        object.__setattr__(
            self,
            "missing_information",
            tuple(
                _require_bounded_string(
                    item,
                    "missing_information item",
                    MAX_TASK_MISSING_INFORMATION_ITEM_LENGTH,
                )
                for item in missing_information
            ),
        )
        if not isinstance(self.validation_status, ValidationStatus):
            raise TypeError("validation_status must be a ValidationStatus")
        if not isinstance(self.source_type, EpisodeSourceType):
            raise TypeError("source_type must be an EpisodeSourceType")
        if not isinstance(self.created_at, datetime):
            raise TypeError("created_at must be a datetime")
        if not isinstance(self.updated_at, datetime):
            raise TypeError("updated_at must be a datetime")
        if not isinstance(self.retrieval_eligible, bool):
            raise TypeError("retrieval_eligible must be a boolean")
        expected_eligibility = {
            ValidationStatus.SYSTEM_GENERATED: False,
            ValidationStatus.USER_APPROVED: True,
            ValidationStatus.COMPLETED: True,
            ValidationStatus.REJECTED: False,
        }[self.validation_status]
        if self.retrieval_eligible != expected_eligibility:
            raise ValueError("retrieval_eligible must match validation_status")
        if self.source_type is not EpisodeSourceType.SYSTEM_GENERATED_CHAT_TASK:
            raise ValueError("source_type must be system_generated_chat_task")
        if self.model_id is not None:
            _require_string(self.model_id, "model_id")
        if self.prompt_version is not None:
            _require_string(self.prompt_version, "prompt_version")
        if self.confidence is not None and (
            isinstance(self.confidence, bool) or not isinstance(self.confidence, int | float)
        ):
            raise TypeError("confidence must be a number or None")

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        _reject_raw_email_shaped_keys(data)
        expected_fields = {
            "episode_id",
            "record_id",
            "tenant_id",
            "user_id",
            "chat_session_id",
            "chat_turn_id",
            "creation_reason",
            "task_title",
            "minimal_request_paraphrase",
            "action_plan",
            "rag_citations",
            "missing_information",
            "validation_status",
            "retrieval_eligible",
            "source_type",
            "created_at",
            "updated_at",
            "pipeline_version",
            "model_id",
            "prompt_version",
            "confidence",
        }
        unexpected_fields = set(data).difference(expected_fields)
        if unexpected_fields:
            raise ValueError(f"unexpected field(s) for TaskEpisode: {sorted(unexpected_fields)}")
        citations = tuple(
            EpisodeCitation.from_dict(_as_mapping(item, "rag_citations item"))
            for item in _as_sequence(data["rag_citations"], "rag_citations")
        )
        action_plan = tuple(
            _require_string(item, "action_plan item")
            for item in _as_sequence(data["action_plan"], "action_plan")
        )
        missing_information = tuple(
            _require_string(item, "missing_information item")
            for item in _as_sequence(data["missing_information"], "missing_information")
        )
        eligible = data["retrieval_eligible"]
        if not isinstance(eligible, bool):
            raise TypeError("retrieval_eligible must be a boolean")
        confidence = data["confidence"]
        if confidence is not None and (
            isinstance(confidence, bool) or not isinstance(confidence, int | float)
        ):
            raise TypeError("confidence must be a number or None")
        model_id = data["model_id"]
        prompt_version = data["prompt_version"]
        return cls(
            episode_id=_require_string(data["episode_id"], "episode_id"),
            record_id=_require_string(data["record_id"], "record_id"),
            tenant_id=_require_string(data["tenant_id"], "tenant_id"),
            user_id=_require_string(data["user_id"], "user_id"),
            chat_session_id=_require_string(data["chat_session_id"], "chat_session_id"),
            chat_turn_id=_require_string(data["chat_turn_id"], "chat_turn_id"),
            creation_reason=cast(
                Literal["explicit_user_task_request"],
                _require_string(data["creation_reason"], "creation_reason"),
            ),
            task_title=_require_bounded_string(
                data["task_title"], "task_title", MAX_TASK_TITLE_LENGTH
            ),
            minimal_request_paraphrase=_require_bounded_string(
                data["minimal_request_paraphrase"],
                "minimal_request_paraphrase",
                MAX_TASK_REQUEST_PARAPHRASE_LENGTH,
            ),
            action_plan=action_plan,
            rag_citations=citations,
            missing_information=missing_information,
            validation_status=_as_enum(
                data["validation_status"], ValidationStatus, "validation_status"
            ),
            retrieval_eligible=eligible,
            source_type=_as_enum(data["source_type"], EpisodeSourceType, "source_type"),
            created_at=_as_datetime(data["created_at"], "created_at"),
            updated_at=_as_datetime(data["updated_at"], "updated_at"),
            pipeline_version=_require_string(data["pipeline_version"], "pipeline_version"),
            model_id=_require_string(model_id, "model_id") if model_id is not None else None,
            prompt_version=(
                _require_string(prompt_version, "prompt_version")
                if prompt_version is not None
                else None
            ),
            confidence=float(confidence) if confidence is not None else None,
        )


@dataclass(frozen=True, slots=True)
class ChatSummaryEpisode:
    """Bounded, system-generated chat summary prepared for later episodic storage."""

    episode_id: str
    record_id: str
    tenant_id: str
    user_id: str
    chat_session_id: str
    chat_turn_id: str
    summary: str
    validation_status: ValidationStatus
    retrieval_eligible: bool
    source_type: EpisodeSourceType
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    pipeline_version: str
    model_id: str | None
    prompt_version: str | None
    confidence: float | None

    def __post_init__(self) -> None:
        for name in (
            "episode_id",
            "record_id",
            "tenant_id",
            "user_id",
            "chat_session_id",
            "chat_turn_id",
            "summary",
            "pipeline_version",
        ):
            _require_string(getattr(self, name), name)
        if len(self.summary) > MAX_CHAT_SUMMARY_LENGTH:
            raise ValueError(f"summary must not exceed {MAX_CHAT_SUMMARY_LENGTH} characters")
        if self.validation_status is not ValidationStatus.SYSTEM_GENERATED:
            raise ValueError("validation_status must be system_generated")
        if self.retrieval_eligible is not False:
            raise ValueError("retrieval_eligible must be False for chat summaries")
        if self.source_type is not EpisodeSourceType.SYSTEM_GENERATED_CHAT_SUMMARY:
            raise ValueError("source_type must be system_generated_chat_summary")
        if not isinstance(self.created_at, datetime) or not isinstance(self.updated_at, datetime):
            raise TypeError("created_at and updated_at must be datetimes")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be earlier than created_at")
        if self.expires_at is not None:
            if not isinstance(self.expires_at, datetime):
                raise TypeError("expires_at must be a datetime or None")
            if self.expires_at <= self.created_at:
                raise ValueError("expires_at must be later than created_at")
        if self.model_id is not None:
            _require_string(self.model_id, "model_id")
        if self.prompt_version is not None:
            _require_string(self.prompt_version, "prompt_version")
        if self.confidence is not None and (
            isinstance(self.confidence, bool) or not isinstance(self.confidence, int | float)
        ):
            raise TypeError("confidence must be a number or None")

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        _reject_raw_email_shaped_keys(data)
        confidence = data["confidence"]
        if confidence is not None and (
            isinstance(confidence, bool) or not isinstance(confidence, int | float)
        ):
            raise TypeError("confidence must be a number or None")
        model_id = data["model_id"]
        prompt_version = data["prompt_version"]
        expires_at = data["expires_at"]
        retrieval_eligible = data["retrieval_eligible"]
        if not isinstance(retrieval_eligible, bool):
            raise TypeError("retrieval_eligible must be a boolean")
        return cls(
            episode_id=_require_string(data["episode_id"], "episode_id"),
            record_id=_require_string(data["record_id"], "record_id"),
            tenant_id=_require_string(data["tenant_id"], "tenant_id"),
            user_id=_require_string(data["user_id"], "user_id"),
            chat_session_id=_require_string(data["chat_session_id"], "chat_session_id"),
            chat_turn_id=_require_string(data["chat_turn_id"], "chat_turn_id"),
            summary=_require_string(data["summary"], "summary"),
            validation_status=_as_enum(
                data["validation_status"], ValidationStatus, "validation_status"
            ),
            retrieval_eligible=retrieval_eligible,
            source_type=_as_enum(data["source_type"], EpisodeSourceType, "source_type"),
            created_at=_as_datetime(data["created_at"], "created_at"),
            updated_at=_as_datetime(data["updated_at"], "updated_at"),
            expires_at=_as_datetime(expires_at, "expires_at") if expires_at is not None else None,
            pipeline_version=_require_string(data["pipeline_version"], "pipeline_version"),
            model_id=_require_string(model_id, "model_id") if model_id is not None else None,
            prompt_version=(
                _require_string(prompt_version, "prompt_version")
                if prompt_version is not None
                else None
            ),
            confidence=float(confidence) if confidence is not None else None,
        )


@dataclass(frozen=True, slots=True)
class ChatTurn:
    """Transient, bounded session-turn value for the later working-memory buffer."""

    turn_id: str
    session_id: str
    user_message: str
    assistant_message: str | None
    created_at: datetime

    def __post_init__(self) -> None:
        _require_string(self.turn_id, "turn_id")
        _require_string(self.session_id, "session_id")
        _require_string(self.user_message, "user_message")
        if self.assistant_message is not None:
            _require_string(self.assistant_message, "assistant_message")

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        assistant_message = data["assistant_message"]
        return cls(
            turn_id=_require_string(data["turn_id"], "turn_id"),
            session_id=_require_string(data["session_id"], "session_id"),
            user_message=_require_string(data["user_message"], "user_message"),
            assistant_message=(
                _require_string(assistant_message, "assistant_message")
                if assistant_message is not None
                else None
            ),
            created_at=_as_datetime(data["created_at"], "created_at"),
        )


class MemoryProvenanceSource(StrEnum):
    """Trusted origin categories carried by memory writes and citations."""

    EXPLICIT_USER_CONFIG = "explicit_user_config"
    SYSTEM_GENERATED_CHAT_TASK = "system_generated_chat_task"
    ENTERPRISE_CORPUS = "enterprise_corpus"


PROFILE_PREFERENCE_FIELDS = ("language", "timezone", "assistant_persona", "response_tone")


@dataclass(frozen=True, slots=True)
class DeclarativeProfile:
    """Explicit-only profile DTO (PRD-v2 FR-03), narrowed to the first UI slice."""

    profile_id: str
    tenant_id: str
    user_id: str
    language: str | None
    timezone: str | None
    assistant_persona: str | None
    response_tone: str | None
    created_at: datetime
    updated_at: datetime
    source_type: MemoryProvenanceSource = MemoryProvenanceSource.EXPLICIT_USER_CONFIG
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_string(self.profile_id, "profile_id")
        _require_string(self.tenant_id, "tenant_id")
        _require_string(self.user_id, "user_id")
        for name in PROFILE_PREFERENCE_FIELDS:
            value = getattr(self, name)
            if value is not None:
                _require_string(value, name)
        if self.source_type is not MemoryProvenanceSource.EXPLICIT_USER_CONFIG:
            raise ValueError("declarative profiles are explicit-only (FR-04)")

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        _reject_raw_email_shaped_keys(data)
        nullable_strings = {name: data[name] for name in PROFILE_PREFERENCE_FIELDS}
        expires_at = data.get("expires_at")
        return cls(
            profile_id=_require_string(data["profile_id"], "profile_id"),
            tenant_id=_require_string(data["tenant_id"], "tenant_id"),
            user_id=_require_string(data["user_id"], "user_id"),
            language=(
                _require_string(nullable_strings["language"], "language")
                if nullable_strings["language"] is not None
                else None
            ),
            timezone=(
                _require_string(nullable_strings["timezone"], "timezone")
                if nullable_strings["timezone"] is not None
                else None
            ),
            assistant_persona=(
                _require_string(nullable_strings["assistant_persona"], "assistant_persona")
                if nullable_strings["assistant_persona"] is not None
                else None
            ),
            response_tone=(
                _require_string(nullable_strings["response_tone"], "response_tone")
                if nullable_strings["response_tone"] is not None
                else None
            ),
            created_at=_as_datetime(data["created_at"], "created_at"),
            updated_at=_as_datetime(data["updated_at"], "updated_at"),
            source_type=_as_enum(
                data.get("source_type", MemoryProvenanceSource.EXPLICIT_USER_CONFIG),
                MemoryProvenanceSource,
                "source_type",
            ),
            expires_at=(
                _as_datetime(expires_at, "expires_at") if expires_at is not None else None
            ),
        )


@dataclass(frozen=True, slots=True)
class EpisodeTransition:
    """One allowed validation transition with its code-enforced eligibility result."""

    episode_id: str
    namespace: MemoryNamespace
    from_status: ValidationStatus
    to_status: ValidationStatus
    retrieval_eligible: bool
    transitioned_at: datetime

    def __post_init__(self) -> None:
        _require_string(self.episode_id, "episode_id")
        if self.namespace.memory_type is not MemoryType.EPISODIC:
            raise ValueError("episode transitions require an episodic namespace")
        allowed = {
            ValidationStatus.SYSTEM_GENERATED: {
                ValidationStatus.USER_APPROVED,
                ValidationStatus.COMPLETED,
                ValidationStatus.REJECTED,
            },
            ValidationStatus.USER_APPROVED: {
                ValidationStatus.COMPLETED,
                ValidationStatus.REJECTED,
            },
            ValidationStatus.COMPLETED: set(),
            ValidationStatus.REJECTED: set(),
        }
        if self.to_status not in allowed[self.from_status]:
            raise ValueError("invalid episode validation-status transition")
        expected_eligibility = {
            ValidationStatus.USER_APPROVED: True,
            ValidationStatus.COMPLETED: True,
            ValidationStatus.REJECTED: False,
            ValidationStatus.SYSTEM_GENERATED: False,
        }[self.to_status]
        if self.retrieval_eligible != expected_eligibility:
            raise ValueError("retrieval_eligible must match to_status")

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        eligible = data["retrieval_eligible"]
        if not isinstance(eligible, bool):
            raise TypeError("retrieval_eligible must be a boolean")
        return cls(
            episode_id=_require_string(data["episode_id"], "episode_id"),
            namespace=MemoryNamespace.from_dict(_as_mapping(data["namespace"], "namespace")),
            from_status=_as_enum(data["from_status"], ValidationStatus, "from_status"),
            to_status=_as_enum(data["to_status"], ValidationStatus, "to_status"),
            retrieval_eligible=eligible,
            transitioned_at=_as_datetime(data["transitioned_at"], "transitioned_at"),
        )


@dataclass(frozen=True, slots=True)
class MemoryProvenance:
    """Body-free source metadata shared by later profile and episode operations."""

    source_type: MemoryProvenanceSource
    source_id: str
    chat_turn_id: str | None
    pipeline_version: str | None
    model_id: str | None
    prompt_version: str | None

    def __post_init__(self) -> None:
        _require_string(self.source_id, "source_id")
        for name in ("chat_turn_id", "pipeline_version", "model_id", "prompt_version"):
            value = getattr(self, name)
            if value is not None:
                _require_string(value, name)

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        _reject_raw_email_shaped_keys(data)
        expected_fields = {
            "source_type",
            "source_id",
            "chat_turn_id",
            "pipeline_version",
            "model_id",
            "prompt_version",
        }
        unexpected_fields = set(data).difference(expected_fields)
        if unexpected_fields:
            raise ValueError(
                f"unexpected field(s) for MemoryProvenance: {sorted(unexpected_fields)}"
            )
        values = {
            name: data[name]
            for name in ("chat_turn_id", "pipeline_version", "model_id", "prompt_version")
        }
        return cls(
            source_type=_as_enum(data["source_type"], MemoryProvenanceSource, "source_type"),
            source_id=_require_string(data["source_id"], "source_id"),
            chat_turn_id=(
                _require_string(values["chat_turn_id"], "chat_turn_id")
                if values["chat_turn_id"] is not None
                else None
            ),
            pipeline_version=(
                _require_string(values["pipeline_version"], "pipeline_version")
                if values["pipeline_version"] is not None
                else None
            ),
            model_id=(
                _require_string(values["model_id"], "model_id")
                if values["model_id"] is not None
                else None
            ),
            prompt_version=(
                _require_string(values["prompt_version"], "prompt_version")
                if values["prompt_version"] is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class MemoryContextResponse:
    """Typed optional context with explicit graceful-degradation metadata."""

    turns: tuple[ChatTurn, ...]
    profile: DeclarativeProfile | None
    episodes: tuple[TaskEpisode, ...]
    semantic_context: Mapping[str, object] | None
    degraded: bool
    degraded_sources: tuple[DegradedMemorySource, ...]

    def __post_init__(self) -> None:
        if self.semantic_context is not None:
            object.__setattr__(
                self, "semantic_context", _frozen_mapping(self.semantic_context, "semantic_context")
            )
        if not isinstance(self.degraded, bool):
            raise TypeError("degraded must be a boolean")
        if self.degraded != bool(self.degraded_sources):
            raise ValueError("degraded must match whether degraded_sources is empty")

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        profile = data["profile"]
        semantic_context = data["semantic_context"]
        degraded = data["degraded"]
        if not isinstance(degraded, bool):
            raise TypeError("degraded must be a boolean")
        return cls(
            turns=tuple(
                ChatTurn.from_dict(_as_mapping(item, "turns item"))
                for item in _as_sequence(data["turns"], "turns")
            ),
            profile=(
                DeclarativeProfile.from_dict(_as_mapping(profile, "profile"))
                if profile is not None
                else None
            ),
            episodes=tuple(
                TaskEpisode.from_dict(_as_mapping(item, "episodes item"))
                for item in _as_sequence(data["episodes"], "episodes")
            ),
            semantic_context=(
                _as_mapping(semantic_context, "semantic_context")
                if semantic_context is not None
                else None
            ),
            degraded=degraded,
            degraded_sources=tuple(
                _as_enum(item, DegradedMemorySource, "degraded_sources")
                for item in _as_sequence(data["degraded_sources"], "degraded_sources")
            ),
        )
