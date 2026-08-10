"""Memory namespace, context, profile, episode, and provenance contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, Self, cast

from ._chat_contracts_common import (
    AI_CHAT_FEATURE,
    ChatToolChoice,
    DegradedMemorySource,
    EpisodeSourceType,
    MemoryType,
    _as_datetime,
    _as_enum,
    _as_mapping,
    _as_sequence,
    _frozen_mapping,
    _reject_raw_email_shaped_keys,
    _require_key_component,
    _require_string,
    _to_dict,
)
from .target_contracts import ValidationStatus


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
    """Bounded, eligibility-filtered episodic read intent."""

    enabled: bool
    retrieval_eligible_only: Literal[True]
    max_items: int

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be a boolean")
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
    """Selective semantic-memory read intent; querying is never unconditional."""

    enabled: bool
    condition: Literal["chat_intent_requires_enterprise_context"] = (
        "chat_intent_requires_enterprise_context"
    )

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be a boolean")
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


@dataclass(frozen=True, slots=True)
class MemoryReadOptions:
    """The four memory reads a Chat Controller may request through the gateway."""

    short_term: bool
    long_term: bool
    episodic: EpisodicMemoryRead
    semantic: SemanticMemoryRead

    def __post_init__(self) -> None:
        if not isinstance(self.short_term, bool) or not isinstance(self.long_term, bool):
            raise TypeError("short_term and long_term must be booleans")

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        short_term = data["short_term"]
        long_term = data["long_term"]
        if not isinstance(short_term, bool) or not isinstance(long_term, bool):
            raise TypeError("short_term and long_term must be booleans")
        return cls(
            short_term=short_term,
            long_term=long_term,
            episodic=EpisodicMemoryRead.from_dict(_as_mapping(data["episodic"], "episodic")),
            semantic=SemanticMemoryRead.from_dict(_as_mapping(data["semantic"], "semantic")),
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
        return _to_dict(self)

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
        _require_string(self.document_id, "document_id")
        _require_string(self.document_title, "document_title")
        _require_string(self.source_url, "source_url")
        if self.section is not None:
            _require_string(self.section, "section")

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        section = data["section"]
        return cls(
            document_id=_require_string(data["document_id"], "document_id"),
            document_title=_require_string(data["document_title"], "document_title"),
            section=_require_string(section, "section") if section is not None else None,
            source_url=_require_string(data["source_url"], "source_url"),
        )


@dataclass(frozen=True, slots=True)
class TaskEpisode:
    """Derived, body-free `@Email` output prepared for later episodic storage."""

    episode_id: str
    record_id: str
    tenant_id: str
    user_id: str
    run_id: str
    chat_session_id: str
    chat_turn_id: str
    source_tool: Literal["@Email"]
    gmail_message_id: str
    gmail_url: str
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
            "run_id",
            "chat_session_id",
            "chat_turn_id",
            "gmail_message_id",
            "gmail_url",
            "task_title",
            "minimal_request_paraphrase",
            "pipeline_version",
        ):
            _require_string(getattr(self, name), name)
        if self.source_tool != ChatToolChoice.EMAIL.value:
            raise ValueError("source_tool must be '@Email'")
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
            run_id=_require_string(data["run_id"], "run_id"),
            chat_session_id=_require_string(data["chat_session_id"], "chat_session_id"),
            chat_turn_id=_require_string(data["chat_turn_id"], "chat_turn_id"),
            source_tool=cast(
                Literal["@Email"], _require_string(data["source_tool"], "source_tool")
            ),
            gmail_message_id=_require_string(data["gmail_message_id"], "gmail_message_id"),
            gmail_url=_require_string(data["gmail_url"], "gmail_url"),
            task_title=_require_string(data["task_title"], "task_title"),
            minimal_request_paraphrase=_require_string(
                data["minimal_request_paraphrase"], "minimal_request_paraphrase"
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
    SYSTEM_GENERATED_CHAT_TOOL_OUTPUT = "system_generated_chat_tool_output"
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
    source_tool: ChatToolChoice | None
    run_id: str | None
    chat_turn_id: str | None
    pipeline_version: str | None
    model_id: str | None
    prompt_version: str | None

    def __post_init__(self) -> None:
        _require_string(self.source_id, "source_id")
        for name in ("run_id", "chat_turn_id", "pipeline_version", "model_id", "prompt_version"):
            value = getattr(self, name)
            if value is not None:
                _require_string(value, name)
        if (
            self.source_type is MemoryProvenanceSource.SYSTEM_GENERATED_CHAT_TOOL_OUTPUT
            and self.source_tool is not ChatToolChoice.EMAIL
        ):
            raise ValueError("system-generated tool provenance requires source_tool='@Email'")

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        _reject_raw_email_shaped_keys(data)
        values = {
            name: data[name]
            for name in ("run_id", "chat_turn_id", "pipeline_version", "model_id", "prompt_version")
        }
        source_tool = data["source_tool"]
        return cls(
            source_type=_as_enum(data["source_type"], MemoryProvenanceSource, "source_type"),
            source_id=_require_string(data["source_id"], "source_id"),
            source_tool=(
                _as_enum(source_tool, ChatToolChoice, "source_tool")
                if source_tool is not None
                else None
            ),
            run_id=(
                _require_string(values["run_id"], "run_id")
                if values["run_id"] is not None
                else None
            ),
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
