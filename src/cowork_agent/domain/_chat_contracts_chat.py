"""Chat request, stream-event, and turn contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Self

from ._chat_contracts_common import (
    MAX_CHAT_MESSAGE_LENGTH,
    ChatEventType,
    MemoryCitationType,
    _as_enum,
    _as_mapping,
    _as_sequence,
    _frozen_mapping,
    _reject_raw_email_shaped_keys,
    _require_bounded_string,
    _require_string,
    _to_dict,
)
from ._chat_contracts_memory import (
    MAX_TASK_ACTION_PLAN_ITEM_LENGTH,
    MAX_TASK_ACTION_PLAN_ITEMS,
    MAX_TASK_MISSING_INFORMATION_ITEM_LENGTH,
    MAX_TASK_MISSING_INFORMATION_ITEMS,
    MAX_TASK_RAG_CITATIONS,
    MAX_TASK_REQUEST_PARAPHRASE_LENGTH,
    MAX_TASK_TITLE_LENGTH,
    EpisodeCitation,
)
from .target_contracts import ValidationStatus

_TASK_PROPOSAL_FIELDS = frozenset(
    {
        "episode_id",
        "task_title",
        "minimal_request_paraphrase",
        "action_plan",
        "missing_information",
        "rag_citations",
        "validation_status",
        "retrieval_eligible",
    }
)


def _validated_task_proposal(value: object) -> Mapping[str, object]:
    proposal = _as_mapping(value, "proposal")
    _reject_raw_email_shaped_keys(proposal)
    if set(proposal) != _TASK_PROPOSAL_FIELDS:
        raise ValueError("proposal must contain exactly the frontend-safe fields")

    _require_string(proposal["episode_id"], "proposal.episode_id")
    _require_bounded_string(
        proposal["task_title"], "proposal.task_title", MAX_TASK_TITLE_LENGTH
    )
    _require_bounded_string(
        proposal["minimal_request_paraphrase"],
        "proposal.minimal_request_paraphrase",
        MAX_TASK_REQUEST_PARAPHRASE_LENGTH,
    )

    for field, max_items, max_item_length in (
        (
            "action_plan",
            MAX_TASK_ACTION_PLAN_ITEMS,
            MAX_TASK_ACTION_PLAN_ITEM_LENGTH,
        ),
        (
            "missing_information",
            MAX_TASK_MISSING_INFORMATION_ITEMS,
            MAX_TASK_MISSING_INFORMATION_ITEM_LENGTH,
        ),
    ):
        items = _as_sequence(proposal[field], f"proposal.{field}")
        if len(items) > max_items:
            raise ValueError(f"proposal.{field} must not contain more than {max_items} items")
        for item in items:
            _require_bounded_string(item, f"proposal.{field} item", max_item_length)

    citations = _as_sequence(proposal["rag_citations"], "proposal.rag_citations")
    if len(citations) > MAX_TASK_RAG_CITATIONS:
        raise ValueError(
            "proposal.rag_citations must not contain more than "
            f"{MAX_TASK_RAG_CITATIONS} items"
        )
    for citation in citations:
        EpisodeCitation.from_dict(_as_mapping(citation, "proposal.rag_citations item"))

    validation_status = _as_enum(
        proposal["validation_status"], ValidationStatus, "proposal.validation_status"
    )
    retrieval_eligible = proposal["retrieval_eligible"]
    if not isinstance(retrieval_eligible, bool):
        raise TypeError("proposal.retrieval_eligible must be a boolean")
    expected_eligibility = {
        ValidationStatus.SYSTEM_GENERATED: False,
        ValidationStatus.USER_APPROVED: True,
        ValidationStatus.COMPLETED: True,
        ValidationStatus.REJECTED: False,
    }[validation_status]
    if retrieval_eligible != expected_eligibility:
        raise ValueError("proposal.retrieval_eligible must match validation_status")
    return _frozen_mapping(proposal, "proposal")


@dataclass(frozen=True, slots=True)
class ChatMessageRequest:
    """One idempotent user turn submitted to a pre-existing chat session."""

    session_id: str
    user_message: str
    idempotency_key: str

    def __post_init__(self) -> None:
        _require_string(self.session_id, "session_id")
        _require_bounded_string(self.user_message, "user_message", MAX_CHAT_MESSAGE_LENGTH)
        _require_string(self.idempotency_key, "idempotency_key")

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        expected_fields = {"session_id", "user_message", "idempotency_key"}
        unexpected_fields = set(data).difference(expected_fields)
        if unexpected_fields:
            raise ValueError(
                f"unexpected field(s) for ChatMessageRequest: {sorted(unexpected_fields)}"
            )
        return cls(
            session_id=_require_string(data["session_id"], "session_id"),
            user_message=_require_bounded_string(
                data["user_message"], "user_message", MAX_CHAT_MESSAGE_LENGTH
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
    memory_type: MemoryCitationType | None = None
    source_id: str | None = None
    code: str | None = None
    safe_message: str | None = None
    proposal: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        _require_string(self.event_id, "event_id")
        _require_string(self.session_id, "session_id")
        _require_string(self.turn_id, "turn_id")
        if self.proposal is not None:
            object.__setattr__(self, "proposal", _validated_task_proposal(self.proposal))
        self._validate_variant()

    def _validate_variant(self) -> None:
        payloads = {
            "text": self.text,
            "memory_type": self.memory_type,
            "source_id": self.source_id,
            "code": self.code,
            "safe_message": self.safe_message,
            "proposal": self.proposal,
        }
        required: dict[ChatEventType, tuple[str, ...]] = {
            ChatEventType.DELTA: ("text",),
            ChatEventType.MEMORY_CITATION: ("memory_type", "source_id"),
            ChatEventType.TASK_PROPOSAL: ("proposal",),
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
    def task_proposal(
        cls,
        *,
        event_id: str,
        session_id: str,
        turn_id: str,
        proposal: Mapping[str, object],
    ) -> Self:
        return cls(
            event_id,
            session_id,
            turn_id,
            ChatEventType.TASK_PROPOSAL,
            proposal=proposal,
        )

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

    expected_fields = {
        "event_id",
        "session_id",
        "turn_id",
        "event_type",
        "text",
        "memory_type",
        "source_id",
        "code",
        "safe_message",
        "proposal",
    }
    unexpected_fields = set(data).difference(expected_fields)
    if unexpected_fields:
        raise ValueError(
            f"unexpected field(s) for ChatMessageStreamEvent: {sorted(unexpected_fields)}"
        )
    event_type = _as_enum(data["event_type"], ChatEventType, "event_type")
    raw_text = data.get("text")
    raw_source_id = data.get("source_id")
    raw_code = data.get("code")
    raw_safe_message = data.get("safe_message")
    raw_proposal = data.get("proposal")
    return ChatMessageStreamEvent(
        event_id=_require_string(data["event_id"], "event_id"),
        session_id=_require_string(data["session_id"], "session_id"),
        turn_id=_require_string(data["turn_id"], "turn_id"),
        event_type=event_type,
        text=raw_text if isinstance(raw_text, str) else None,
        memory_type=(
            _as_enum(data["memory_type"], MemoryCitationType, "memory_type")
            if data.get("memory_type") is not None
            else None
        ),
        source_id=raw_source_id if isinstance(raw_source_id, str) else None,
        code=raw_code if isinstance(raw_code, str) else None,
        safe_message=raw_safe_message if isinstance(raw_safe_message, str) else None,
        proposal=(
            _as_mapping(raw_proposal, "proposal") if raw_proposal is not None else None
        ),
    )

