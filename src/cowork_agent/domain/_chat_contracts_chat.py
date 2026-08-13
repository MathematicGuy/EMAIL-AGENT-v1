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
    ChatRagEvidence,
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
    _require_bounded_string(proposal["task_title"], "proposal.task_title", MAX_TASK_TITLE_LENGTH)
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
            f"proposal.rag_citations must not contain more than {MAX_TASK_RAG_CITATIONS} items"
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
    document_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_string(self.session_id, "session_id")
        _require_bounded_string(self.user_message, "user_message", MAX_CHAT_MESSAGE_LENGTH)
        _require_string(self.idempotency_key, "idempotency_key")
        if len(self.document_ids) > 50 or len(set(self.document_ids)) != len(self.document_ids):
            raise ValueError("document_ids must contain at most 50 unique identifiers")
        for document_id in self.document_ids:
            _require_string(document_id, "document_ids item")

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        expected_fields = {"session_id", "user_message", "idempotency_key", "document_ids"}
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
            document_ids=tuple(
                _require_string(item, "document_ids item")
                for item in _as_sequence(data.get("document_ids", ()), "document_ids")
            ),
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
    citation_scope: str | None = None
    project_id: str | None = None
    document_id: str | None = None
    document_title: str | None = None
    section: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    rag_evidence: tuple[ChatRagEvidence, ...] = ()
    retrieval_status: str | None = None

    def __post_init__(self) -> None:
        _require_string(self.event_id, "event_id")
        _require_string(self.session_id, "session_id")
        _require_string(self.turn_id, "turn_id")
        if self.proposal is not None:
            object.__setattr__(self, "proposal", _validated_task_proposal(self.proposal))
        if len(self.rag_evidence) > 5 or not all(
            isinstance(item, ChatRagEvidence) for item in self.rag_evidence
        ):
            raise ValueError("rag_evidence must contain at most five ChatRagEvidence items")
        self._validate_variant()

    def _validate_variant(self) -> None:
        payloads = {
            "text": self.text,
            "memory_type": self.memory_type,
            "source_id": self.source_id,
            "code": self.code,
            "safe_message": self.safe_message,
            "proposal": self.proposal,
            "citation_scope": self.citation_scope,
            "project_id": self.project_id,
            "document_id": self.document_id,
            "document_title": self.document_title,
            "section": self.section,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "rag_evidence": self.rag_evidence or None,
            "retrieval_status": self.retrieval_status,
        }
        required: dict[ChatEventType, tuple[str, ...]] = {
            ChatEventType.DELTA: ("text",),
            ChatEventType.MEMORY_CITATION: ("memory_type", "source_id"),
            ChatEventType.TASK_PROPOSAL: ("proposal",),
            ChatEventType.COMPLETED: (),
            ChatEventType.ERROR: ("code", "safe_message"),
        }
        expected = required[self.event_type]
        if self.event_type is not ChatEventType.COMPLETED and (
            self.rag_evidence or self.retrieval_status is not None
        ):
            raise ValueError("rag_evidence is supported only on completed events")
        citation_fields = {
            "citation_scope",
            "project_id",
            "document_id",
            "document_title",
            "section",
            "page_start",
            "page_end",
        }
        for name, value in payloads.items():
            if (
                name in {"rag_evidence", "retrieval_status"}
                and self.event_type is ChatEventType.COMPLETED
            ):
                continue
            if name in citation_fields and self.event_type is ChatEventType.MEMORY_CITATION:
                continue
            if (name in expected) != (value is not None):
                raise ValueError(
                    f"{self.event_type.value} events require only {', '.join(expected)}"
                )
        for name in expected:
            value = payloads[name]
            if isinstance(value, str):
                _require_string(value, name)
        coordinate_values = (
            self.project_id,
            self.document_id,
            self.document_title,
            self.section,
            self.page_start,
            self.page_end,
        )
        if self.citation_scope is None and any(value is not None for value in coordinate_values):
            raise ValueError("citation coordinates require citation_scope")
        if self.citation_scope is not None:
            if self.event_type is not ChatEventType.MEMORY_CITATION:
                raise ValueError("citation coordinates require a memory_citation event")
            if self.citation_scope != "project_document":
                raise ValueError("unsupported citation_scope")
            for name in ("project_id", "document_id", "document_title"):
                _require_string(getattr(self, name), name)
            if self.page_start is None or self.page_end is None:
                raise ValueError("project citations require a page range")
            if self.page_start < 1 or self.page_end < self.page_start:
                raise ValueError("project citation page range is invalid")

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
        citation_scope: str | None = None,
        project_id: str | None = None,
        document_id: str | None = None,
        document_title: str | None = None,
        section: str | None = None,
        page_start: int | None = None,
        page_end: int | None = None,
    ) -> Self:
        return cls(
            event_id,
            session_id,
            turn_id,
            ChatEventType.MEMORY_CITATION,
            memory_type=memory_type,
            source_id=source_id,
            citation_scope=citation_scope,
            project_id=project_id,
            document_id=document_id,
            document_title=document_title,
            section=section,
            page_start=page_start,
            page_end=page_end,
        )

    @classmethod
    def completed(
        cls, *, event_id: str, session_id: str, turn_id: str,
        rag_evidence: tuple[ChatRagEvidence, ...] = (), retrieval_status: str | None = None,
    ) -> Self:
        return cls(event_id, session_id, turn_id, ChatEventType.COMPLETED,
                   rag_evidence=rag_evidence, retrieval_status=retrieval_status)

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
        "citation_scope",
        "project_id",
        "document_id",
        "document_title",
        "section",
        "page_start",
        "page_end",
        "rag_evidence",
        "retrieval_status",
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
        proposal=(_as_mapping(raw_proposal, "proposal") if raw_proposal is not None else None),
        citation_scope=(
            str(data["citation_scope"]) if data.get("citation_scope") is not None else None
        ),
        project_id=(str(data["project_id"]) if data.get("project_id") is not None else None),
        document_id=(str(data["document_id"]) if data.get("document_id") is not None else None),
        document_title=(
            str(data["document_title"]) if data.get("document_title") is not None else None
        ),
        section=(str(data["section"]) if data.get("section") is not None else None),
        page_start=_optional_int(data.get("page_start"), "page_start"),
        page_end=_optional_int(data.get("page_end"), "page_end"),
        rag_evidence=tuple(
            ChatRagEvidence.from_dict(_as_mapping(item, "rag evidence"))
            for item in _as_sequence(data.get("rag_evidence", ()), "rag_evidence")
        ),
        retrieval_status=(
            str(data["retrieval_status"])
            if data.get("retrieval_status") is not None
            else None
        ),
    )


def _optional_int(value: object, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer or null")
    return value
