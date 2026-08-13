"""Framework-free contracts for classifier-gated AI Chat routing (V3-M4)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Self, TypeVar

from ._chat_contracts_common import (
    MAX_RETRIEVAL_QUERY_LENGTH,
    _as_mapping,
    _as_sequence,
    _require_bounded_string,
    _require_string,
)
from ._chat_contracts_memory import ChatTurn

MAX_READY_DOCUMENTS = 50
MAX_READY_DOCUMENT_ID_LENGTH = 256
MAX_READY_DOCUMENT_TITLE_LENGTH = 300
MAX_CLASSIFIER_TURNS = 8
MAX_TOOL_NAME_LENGTH = 128
MAX_PROMPT_VERSION_LENGTH = 128

_RoutingEnum = TypeVar("_RoutingEnum", bound=StrEnum)


class ChatIntent(StrEnum):
    CHAT = "chat"
    KNOWLEDGE_QUERY = "knowledge_query"
    ACTION_REQUEST = "action_request"


class ChatRoute(StrEnum):
    CHAT = "chat"
    RAG = "rag"
    TOOL = "tool"
    RAG_TOOL = "rag_tool"
    CLARIFY = "clarify"


class IntentReasonCode(StrEnum):
    GENERAL_CHAT = "general_chat"
    USER_DOCUMENT_REQUIRED = "user_document_required"
    EXPLICIT_DOCUMENT_REFERENCE = "explicit_document_reference"
    EXTERNAL_ACTION_REQUESTED = "external_action_requested"
    MISSING_INFORMATION = "missing_information"
    CLASSIFIER_UNAVAILABLE = "classifier_unavailable"
    NO_READY_DOCUMENTS = "no_ready_documents"
    TOOL_REQUESTED_BUT_DISABLED = "tool_requested_but_disabled"


CLASSIFIER_REASON_CODES = frozenset(
    {
        IntentReasonCode.GENERAL_CHAT,
        IntentReasonCode.USER_DOCUMENT_REQUIRED,
        IntentReasonCode.EXPLICIT_DOCUMENT_REFERENCE,
        IntentReasonCode.EXTERNAL_ACTION_REQUESTED,
        IntentReasonCode.MISSING_INFORMATION,
    }
)


@dataclass(frozen=True, slots=True)
class ReadyDocumentRef:
    document_id: str
    title: str

    def __post_init__(self) -> None:
        _require_bounded_string(self.document_id, "document_id", MAX_READY_DOCUMENT_ID_LENGTH)
        _require_bounded_string(self.title, "title", MAX_READY_DOCUMENT_TITLE_LENGTH)

    def to_dict(self) -> dict[str, str]:
        return {"document_id": self.document_id, "title": self.title}

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        _require_exact_fields(data, {"document_id", "title"}, "ReadyDocumentRef")
        return cls(
            document_id=_require_bounded_string(
                data["document_id"], "document_id", MAX_READY_DOCUMENT_ID_LENGTH
            ),
            title=_require_bounded_string(data["title"], "title", MAX_READY_DOCUMENT_TITLE_LENGTH),
        )


@dataclass(frozen=True, slots=True)
class IntentClassifierInput:
    current_message: str
    recent_turns: tuple[ChatTurn, ...]
    ready_documents: tuple[ReadyDocumentRef, ...]

    def __post_init__(self) -> None:
        _require_string(self.current_message, "current_message")
        if len(self.recent_turns) > MAX_CLASSIFIER_TURNS:
            raise ValueError(f"recent_turns must not exceed {MAX_CLASSIFIER_TURNS}")
        if len(self.ready_documents) > MAX_READY_DOCUMENTS:
            raise ValueError(f"ready_documents must not exceed {MAX_READY_DOCUMENTS}")
        if len({item.document_id for item in self.ready_documents}) != len(self.ready_documents):
            raise ValueError("ready_documents must have unique document ids")


@dataclass(frozen=True, slots=True)
class IntentDecision:
    intent: ChatIntent
    needs_rag: bool
    needs_tool: bool
    tool_name: str | None
    needs_clarification: bool
    retrieval_query: str | None
    confidence: float
    reason_codes: tuple[IntentReasonCode, ...]

    def __post_init__(self) -> None:
        for name in ("needs_rag", "needs_tool", "needs_clarification"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a boolean")
        if self.needs_rag:
            _require_bounded_string(
                self.retrieval_query, "retrieval_query", MAX_RETRIEVAL_QUERY_LENGTH
            )
        elif self.retrieval_query is not None:
            _require_bounded_string(
                self.retrieval_query, "retrieval_query", MAX_RETRIEVAL_QUERY_LENGTH
            )
        if self.needs_tool:
            _require_bounded_string(self.tool_name, "tool_name", MAX_TOOL_NAME_LENGTH)
        elif self.tool_name is not None:
            raise ValueError("tool_name must be null when needs_tool is false")
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, int | float)
            or not isfinite(self.confidence)
            or not 0 <= self.confidence <= 1
        ):
            raise ValueError("confidence must be a finite number in [0, 1]")
        if not self.reason_codes:
            raise ValueError("reason_codes must not be empty")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("reason_codes must not contain duplicates")

    def to_dict(self) -> dict[str, object]:
        return {
            "intent": self.intent.value,
            "needs_rag": self.needs_rag,
            "needs_tool": self.needs_tool,
            "tool_name": self.tool_name,
            "needs_clarification": self.needs_clarification,
            "retrieval_query": self.retrieval_query,
            "confidence": float(self.confidence),
            "reason_codes": [code.value for code in self.reason_codes],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        expected = {
            "intent",
            "needs_rag",
            "needs_tool",
            "tool_name",
            "needs_clarification",
            "retrieval_query",
            "confidence",
            "reason_codes",
        }
        _require_exact_fields(data, expected, "IntentDecision")
        intent = _enum_value(data["intent"], ChatIntent, "intent")
        reason_codes = tuple(
            _enum_value(item, IntentReasonCode, "reason_codes")
            for item in _as_sequence(data["reason_codes"], "reason_codes")
        )
        confidence = data["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, int | float):
            raise TypeError("confidence must be numeric")
        tool_name = data["tool_name"]
        retrieval_query = data["retrieval_query"]
        return cls(
            intent=intent,
            needs_rag=_require_bool(data["needs_rag"], "needs_rag"),
            needs_tool=_require_bool(data["needs_tool"], "needs_tool"),
            tool_name=(
                _require_bounded_string(tool_name, "tool_name", MAX_TOOL_NAME_LENGTH)
                if tool_name is not None
                else None
            ),
            needs_clarification=_require_bool(data["needs_clarification"], "needs_clarification"),
            retrieval_query=(
                _require_bounded_string(
                    retrieval_query,
                    "retrieval_query",
                    MAX_RETRIEVAL_QUERY_LENGTH,
                )
                if retrieval_query is not None
                else None
            ),
            confidence=float(confidence),
            reason_codes=reason_codes,
        )


@dataclass(frozen=True, slots=True)
class RoutingOutcome:
    decision: IntentDecision
    route: ChatRoute
    effective_needs_rag: bool
    effective_needs_tool: bool
    effective_needs_clarification: bool
    retrieval_query: str | None
    reason_codes: tuple[IntentReasonCode, ...]
    classifier_retried: bool
    fallback_used: bool
    prompt_version: str

    def __post_init__(self) -> None:
        for name in (
            "effective_needs_rag",
            "effective_needs_tool",
            "effective_needs_clarification",
            "classifier_retried",
            "fallback_used",
        ):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a boolean")
        if self.retrieval_query is not None:
            _require_bounded_string(
                self.retrieval_query, "retrieval_query", MAX_RETRIEVAL_QUERY_LENGTH
            )
        if not self.reason_codes or len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("reason_codes must be nonempty and unique")
        _require_bounded_string(self.prompt_version, "prompt_version", MAX_PROMPT_VERSION_LENGTH)


def classifier_decision_from_dict(data: Mapping[str, object]) -> IntentDecision:
    """Parse provider output while excluding system-owned reason codes."""

    decision = IntentDecision.from_dict(_as_mapping(data, "intent decision"))
    if not set(decision.reason_codes).issubset(CLASSIFIER_REASON_CODES):
        raise ValueError("classifier output contains a system-owned reason code")
    return decision


def _require_exact_fields(data: Mapping[str, object], expected: set[str], name: str) -> None:
    missing = expected.difference(data)
    unexpected = set(data).difference(expected)
    if missing or unexpected:
        raise ValueError(
            f"{name} fields mismatch; missing={sorted(missing)}, unexpected={sorted(unexpected)}"
        )


def _require_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a boolean")
    return value


def _enum_value(value: object, enum_type: type[_RoutingEnum], name: str) -> _RoutingEnum:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    try:
        return enum_type(value)
    except ValueError:
        raise ValueError(f"{name} has an unsupported value") from None
