"""Chat request, stream-event, and turn contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Self

from ._chat_contracts_common import (
    MAX_CHAT_MESSAGE_LENGTH,
    ChatEventType,
    ChatToolChoice,
    MemoryCitationType,
    _as_enum,
    _as_mapping,
    _as_sequence,
    _frozen_mapping,
    _require_bounded_string,
    _require_string,
    _to_dict,
)


@dataclass(frozen=True, slots=True)
class ChatMessageRequest:
    """One idempotent user turn submitted to a pre-existing chat session."""

    session_id: str
    user_message: str
    tool_choices: tuple[ChatToolChoice, ...]
    idempotency_key: str

    def __post_init__(self) -> None:
        _require_string(self.session_id, "session_id")
        _require_bounded_string(self.user_message, "user_message", MAX_CHAT_MESSAGE_LENGTH)
        _require_string(self.idempotency_key, "idempotency_key")

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        return cls(
            session_id=_require_string(data["session_id"], "session_id"),
            user_message=_require_bounded_string(
                data["user_message"], "user_message", MAX_CHAT_MESSAGE_LENGTH
            ),
            tool_choices=tuple(
                _as_enum(item, ChatToolChoice, "tool_choices")
                for item in _as_sequence(data["tool_choices"], "tool_choices")
            ),
            idempotency_key=_require_string(data["idempotency_key"], "idempotency_key"),
        )


@dataclass(frozen=True, slots=True)
class ChatMessageStreamEvent:
    """A fail-closed discriminated event emitted to one chat session (§6.4)."""

    event_id: str
    session_id: str
    turn_id: str
    event_type: ChatEventType
    text: str | None = None
    name: ChatToolChoice | None = None
    call_id: str | None = None
    action_plan: Mapping[str, object] | None = None
    memory_type: MemoryCitationType | None = None
    source_id: str | None = None
    code: str | None = None
    safe_message: str | None = None

    def __post_init__(self) -> None:
        _require_string(self.event_id, "event_id")
        _require_string(self.session_id, "session_id")
        _require_string(self.turn_id, "turn_id")
        if self.action_plan is not None:
            object.__setattr__(
                self, "action_plan", _frozen_mapping(self.action_plan, "action_plan")
            )
        self._validate_variant()

    def _validate_variant(self) -> None:
        payloads = {
            "text": self.text,
            "name": self.name,
            "call_id": self.call_id,
            "action_plan": self.action_plan,
            "memory_type": self.memory_type,
            "source_id": self.source_id,
            "code": self.code,
            "safe_message": self.safe_message,
        }
        required: dict[ChatEventType, tuple[str, ...]] = {
            ChatEventType.DELTA: ("text",),
            ChatEventType.TOOL_CALL: ("name", "call_id"),
            ChatEventType.TOOL_OUTPUT: ("action_plan",),
            ChatEventType.MEMORY_CITATION: ("memory_type", "source_id"),
            ChatEventType.COMPLETED: (),
            ChatEventType.ERROR: ("code", "safe_message"),
        }
        expected = required[self.event_type]
        for name, value in payloads.items():
            if (name in expected) != (value is not None):
                raise ValueError(
                    f"{self.event_type.value} events require only {', '.join(expected)}"
                )
        for name in expected:
            value = payloads[name]
            if isinstance(value, str):
                _require_string(value, name)

    @classmethod
    def delta(cls, *, event_id: str, session_id: str, turn_id: str, text: str) -> Self:
        return cls(event_id, session_id, turn_id, ChatEventType.DELTA, text=text)

    @classmethod
    def tool_call(
        cls,
        *,
        event_id: str,
        session_id: str,
        turn_id: str,
        name: ChatToolChoice,
        call_id: str,
    ) -> Self:
        return cls(
            event_id,
            session_id,
            turn_id,
            ChatEventType.TOOL_CALL,
            name=name,
            call_id=call_id,
        )

    @classmethod
    def tool_output(
        cls,
        *,
        event_id: str,
        session_id: str,
        turn_id: str,
        action_plan: Mapping[str, object],
    ) -> Self:
        return cls(
            event_id,
            session_id,
            turn_id,
            ChatEventType.TOOL_OUTPUT,
            action_plan=action_plan,
        )

    @classmethod
    def memory_citation(
        cls,
        *,
        event_id: str,
        session_id: str,
        turn_id: str,
        memory_type: MemoryCitationType,
        source_id: str,
    ) -> Self:
        return cls(
            event_id,
            session_id,
            turn_id,
            ChatEventType.MEMORY_CITATION,
            memory_type=memory_type,
            source_id=source_id,
        )

    @classmethod
    def completed(cls, *, event_id: str, session_id: str, turn_id: str) -> Self:
        return cls(event_id, session_id, turn_id, ChatEventType.COMPLETED)

    @classmethod
    def error(
        cls,
        *,
        event_id: str,
        session_id: str,
        turn_id: str,
        code: str,
        safe_message: str,
    ) -> Self:
        return cls(
            event_id,
            session_id,
            turn_id,
            ChatEventType.ERROR,
            code=code,
            safe_message=safe_message,
        )

    def to_dict(self) -> dict[str, object]:
        return {key: value for key, value in _to_dict(self).items() if value is not None}


def stream_event_from_dict(data: Mapping[str, object]) -> ChatMessageStreamEvent:
    """Restore and validate one typed stream event from an SSE-compatible mapping."""

    event_type = _as_enum(data["event_type"], ChatEventType, "event_type")
    raw_text = data.get("text")
    raw_call_id = data.get("call_id")
    raw_source_id = data.get("source_id")
    raw_code = data.get("code")
    raw_safe_message = data.get("safe_message")
    return ChatMessageStreamEvent(
        event_id=_require_string(data["event_id"], "event_id"),
        session_id=_require_string(data["session_id"], "session_id"),
        turn_id=_require_string(data["turn_id"], "turn_id"),
        event_type=event_type,
        text=raw_text if isinstance(raw_text, str) else None,
        name=(
            _as_enum(data["name"], ChatToolChoice, "name") if data.get("name") is not None else None
        ),
        call_id=raw_call_id if isinstance(raw_call_id, str) else None,
        action_plan=(
            _as_mapping(data["action_plan"], "action_plan")
            if data.get("action_plan") is not None
            else None
        ),
        memory_type=(
            _as_enum(data["memory_type"], MemoryCitationType, "memory_type")
            if data.get("memory_type") is not None
            else None
        ),
        source_id=raw_source_id if isinstance(raw_source_id, str) else None,
        code=raw_code if isinstance(raw_code, str) else None,
        safe_message=raw_safe_message if isinstance(raw_safe_message, str) else None,
    )

