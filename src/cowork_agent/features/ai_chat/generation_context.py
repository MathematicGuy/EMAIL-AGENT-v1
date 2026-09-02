"""Typed, framework-free context assembly for a future chat reply adapter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Generic, TypeVar

from cowork_agent.domain.chat_contracts import (
    ChatMessageRequest,
    ChatTurn,
    DeclarativeProfile,
    MemoryContextResponse,
    TaskEpisode,
)
from cowork_agent.domain.project_documents import (
    ProjectDocumentEvidence,
    ProjectDocumentResponse,
)
from cowork_agent.domain.target_contracts import ValidationStatus

_T = TypeVar("_T")
_MAX_ACTIVE_SESSION_TURNS = 8


class ContextSource(StrEnum):
    CURRENT_INSTRUCTION = "current_instruction"
    ACTIVE_SESSION_TURNS = "active_session_turns"
    CURRENT_COMPANY_EVIDENCE = "current_company_evidence"
    CURRENT_PROJECT_EVIDENCE = "current_project_evidence"
    STORED_PREFERENCE = "stored_preference"
    ADVISORY_EPISODE = "advisory_episode"


class ChatResponseMode(StrEnum):
    NORMAL = "normal"
    CLARIFY = "clarify"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    EVIDENCE_UNAVAILABLE = "evidence_unavailable"


@dataclass(frozen=True, slots=True)
class LabeledSection(Generic[_T]):
    label: ContextSource
    value: _T
    advisory: bool = False


@dataclass(frozen=True, slots=True)
class CompanyEvidence:
    source_label: str
    retrieval_status: str
    chunks: tuple[Mapping[str, object], ...]
    citations: tuple[Mapping[str, object], ...]
    scores: tuple[Mapping[str, object], ...]


@dataclass(frozen=True, slots=True)
class GenerationContext:
    current_instruction: LabeledSection[str]
    active_session_turns: LabeledSection[tuple[ChatTurn, ...]] | None
    current_company_evidence: LabeledSection[CompanyEvidence] | None
    stored_preference: LabeledSection[DeclarativeProfile] | None
    advisory_episodes: LabeledSection[tuple[TaskEpisode, ...]] | None
    conflict_precedence: tuple[ContextSource, ...]
    current_project_evidence: LabeledSection[tuple[ProjectDocumentEvidence, ...]] | None = None
    response_mode: ChatResponseMode = ChatResponseMode.NORMAL
    # What a tool did this turn, already rendered for the model. Failures
    # arrive in this same field so the reply says the event was not created
    # rather than claiming it was. No ContextSource member and no
    # precedence rank: one tool does not justify reworking that table.
    tool_result: str | None = None


_CONFLICT_PRECEDENCE = (
    ContextSource.CURRENT_INSTRUCTION,
    ContextSource.CURRENT_COMPANY_EVIDENCE,
    ContextSource.STORED_PREFERENCE,
    ContextSource.ADVISORY_EPISODE,
)

_PROJECT_CONFLICT_PRECEDENCE = (
    ContextSource.CURRENT_INSTRUCTION,
    ContextSource.CURRENT_PROJECT_EVIDENCE,
    ContextSource.CURRENT_COMPANY_EVIDENCE,
    ContextSource.STORED_PREFERENCE,
    ContextSource.ADVISORY_EPISODE,
)


def assemble_generation_context(
    request: ChatMessageRequest,
    memory: MemoryContextResponse,
    *,
    response_mode: ChatResponseMode = ChatResponseMode.NORMAL,
    project_documents: ProjectDocumentResponse | None = None,
    tool_result: str | None = None,
) -> GenerationContext:
    """Assemble only present, typed memory into explicitly-labeled reply context."""

    turns = tuple(turn for turn in memory.turns if turn.session_id == request.session_id)[
        -_MAX_ACTIVE_SESSION_TURNS:
    ]
    episodes = tuple(
        episode
        for episode in memory.episodes
        if episode.retrieval_eligible
        and episode.validation_status
        in {ValidationStatus.USER_APPROVED, ValidationStatus.COMPLETED}
    )
    company_evidence = _company_evidence(memory.semantic_context)
    return GenerationContext(
        current_instruction=LabeledSection(ContextSource.CURRENT_INSTRUCTION, request.user_message),
        active_session_turns=(
            LabeledSection(ContextSource.ACTIVE_SESSION_TURNS, turns) if turns else None
        ),
        current_company_evidence=(
            LabeledSection(ContextSource.CURRENT_COMPANY_EVIDENCE, company_evidence)
            if company_evidence is not None
            else None
        ),
        stored_preference=(
            LabeledSection(ContextSource.STORED_PREFERENCE, memory.profile)
            if memory.profile is not None
            else None
        ),
        advisory_episodes=(
            LabeledSection(ContextSource.ADVISORY_EPISODE, episodes, advisory=True)
            if episodes
            else None
        ),
        conflict_precedence=(
            _PROJECT_CONFLICT_PRECEDENCE
            if project_documents is not None and project_documents.evidence
            else _CONFLICT_PRECEDENCE
        ),
        current_project_evidence=(
            LabeledSection(
                ContextSource.CURRENT_PROJECT_EVIDENCE,
                project_documents.evidence,
            )
            if project_documents is not None and project_documents.evidence
            else None
        ),
        response_mode=response_mode,
        tool_result=tool_result,
    )


def _company_evidence(value: Mapping[str, object] | None) -> CompanyEvidence | None:
    if not isinstance(value, Mapping):
        return None
    source_label = value.get("source_label")
    retrieval_status = value.get("retrieval_status")
    chunks = _mapping_sequence(value.get("chunks"))
    citations = _mapping_sequence(value.get("citations"))
    scores = _mapping_sequence(value.get("scores"))
    if (
        source_label != ContextSource.CURRENT_COMPANY_EVIDENCE.value
        or not isinstance(retrieval_status, str)
        or chunks is None
        or citations is None
        or scores is None
    ):
        return None
    return CompanyEvidence(source_label, retrieval_status, chunks, citations, scores)


def _mapping_sequence(value: object) -> tuple[Mapping[str, object], ...] | None:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return None
    if not all(isinstance(item, Mapping) for item in value):
        return None
    return tuple(MappingProxyType(dict(item)) for item in value)
