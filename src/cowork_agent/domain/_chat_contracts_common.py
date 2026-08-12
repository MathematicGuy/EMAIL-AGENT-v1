"""Private shared helpers for V2-M1 chat and memory contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum, StrEnum
from types import MappingProxyType
from typing import TypeVar

CHAT_CONTRACTS_VERSION = "2.0.0"
AI_CHAT_FEATURE = "ai_chat"
MAX_CHAT_SUMMARY_LENGTH = 500
MAX_CHAT_MESSAGE_LENGTH = 4_000
MAX_RETRIEVAL_QUERY_LENGTH = 2_000
MAX_EPISODIC_RETRIEVAL_ITEMS = 20
MAX_SEMANTIC_RETRIEVAL_ITEMS = 20
MAX_RETRIEVAL_TIMEOUT_MS = 10_000


class ChatEventType(StrEnum):
    """Discriminant for one server-sent chat event."""

    DELTA = "delta"
    MEMORY_CITATION = "memory_citation"
    TASK_PROPOSAL = "task_proposal"
    COMPLETED = "completed"
    ERROR = "error"


class MemoryType(StrEnum):
    """Memory stores isolated by the Memory Gateway (PRD-v2 FR-02)."""

    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"


class MemoryCitationType(StrEnum):
    """Memory types which can be cited in the chat stream (§6.4)."""

    DECLARATIVE = "declarative"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"


class DegradedMemorySource(StrEnum):
    """Optional memory source unavailable while a chat turn still proceeds."""

    LONG_TERM = "long_term"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"


class EpisodeSourceType(StrEnum):
    """Provenance for a durable task-derived episode."""

    SYSTEM_GENERATED_CHAT_TASK = "system_generated_chat_task"
    SYSTEM_GENERATED_CHAT_SUMMARY = "system_generated_chat_summary"
    USER_APPROVED_TASK = "user_approved_task"


_E = TypeVar("_E", bound=Enum)


def _require_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_bounded_string(value: object, name: str, maximum_length: int) -> str:
    value = _require_string(value, name)
    if len(value) > maximum_length:
        raise ValueError(f"{name} must not exceed {maximum_length} characters")
    return value


def _require_key_component(value: object, name: str) -> str:
    value = _require_string(value, name)
    if "/" in value:
        raise ValueError(f"{name} must not contain '/' because it is a logical-key component")
    return value


def _as_mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    if not all(isinstance(key, str) for key in value):
        raise TypeError(f"{name} keys must be strings")
    return value


def _as_sequence(value: object, name: str) -> Sequence[object]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise TypeError(f"{name} must be a sequence")
    return value


def _as_enum(value: object, enum_type: type[_E], name: str) -> _E:
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a {enum_type.__name__}")
    return enum_type(value)


def _as_datetime(value: object, name: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError(f"{name} must be a datetime or ISO-8601 string")


def _jsonable(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return _to_dict(value)
    return value


def _to_dict(instance: object) -> dict[str, object]:
    if not is_dataclass(instance) or isinstance(instance, type):
        raise TypeError(f"Expected a dataclass instance, got {type(instance).__name__}")
    return {field.name: _jsonable(getattr(instance, field.name)) for field in fields(instance)}


def _frozen_mapping(value: object, name: str) -> Mapping[str, object]:
    mapping = _as_mapping(value, name)
    return MappingProxyType({key: _freeze_value(item) for key, item in mapping.items()})


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("nested context mappings must have JSON-compatible string keys")
        return MappingProxyType(
            {key: _freeze_value(item) for key, item in value.items()}
        )
    if isinstance(value, tuple | list):
        return tuple(_freeze_value(item) for item in value)
    if value is None or isinstance(value, str | int | float | bool | Enum | datetime):
        return value
    raise TypeError("context values must be JSON-compatible")


_RAW_EMAIL_SHAPED_KEYS = frozenset(
    {
        "body",
        "email_body",
        "raw_email",
        "raw_email_body",
        "normalized_body",
        "attachment_content",
        "attachment_contents",
    }
)

_TOOL_PAYLOAD_SHAPED_KEYS = frozenset(
    {
        "tool_payload",
        "tool_call",
        "tool_output",
        "tool_result",
    }
)


def _reject_raw_email_shaped_keys(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if isinstance(key, str):
                normalized_key = key.lower().replace("-", "_")
                if normalized_key in _RAW_EMAIL_SHAPED_KEYS:
                    raise ValueError("raw email-shaped keys are not allowed in memory contracts")
                if normalized_key in _TOOL_PAYLOAD_SHAPED_KEYS:
                    raise ValueError("tool payload-shaped keys are not allowed in memory contracts")
            _reject_raw_email_shaped_keys(nested)
    elif isinstance(value, Sequence) and not isinstance(value, str):
        for nested in value:
            _reject_raw_email_shaped_keys(nested)
