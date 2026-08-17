"""Application-layer ports owned by the AI Chat feature."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatMessageRequest,
    ChatSummaryEpisode,
    ChatTurn,
    DeclarativeProfile,
    EpisodeCitation,
    EpisodeTransition,
    EpisodicMemoryQuery,
    IntentClassifierInput,
    IntentDecision,
    MemoryNamespace,
    ReadyDocumentRef,
    SemanticMemoryQuery,
    TaskEpisode,
)

if TYPE_CHECKING:
    from .generation_context import GenerationContext


@dataclass(frozen=True, slots=True)
class ChatTaskProposal:
    """Provider-supplied bounded proposal fields; server owns episode identity and state."""

    task_title: str
    minimal_request_paraphrase: str
    action_plan: tuple[str, ...]
    rag_citations: tuple[EpisodeCitation, ...]
    missing_information: tuple[str, ...]
    model_id: str | None
    prompt_version: str | None
    confidence: float | None


@dataclass(frozen=True, slots=True)
class ChatReplyChunk:
    """One typed assistant chunk with an optional proposal from the configured reply provider."""

    text: str
    task_proposal: ChatTaskProposal | None = None
    citation_ids: tuple[str, ...] = ()
    conversation_title: str | None = None


class ChatReplyPort(Protocol):
    """Stream one assistant reply from already-bounded, typed chat context."""

    def stream_reply(
        self,
        request: ChatMessageRequest,
        context: GenerationContext,
    ) -> AsyncIterator[str | ChatReplyChunk]: ...


class IntentClassifierPort(Protocol):
    """Return one structured routing decision for one bounded chat turn."""

    async def classify(self, classifier_input: IntentClassifierInput) -> IntentDecision: ...


class ReadyDocumentCatalogPort(Protocol):
    """Expose ready document metadata only; never bytes, page text, or chunks."""

    async def list_ready(
        self, scope: ChatMemoryScope, *, at: datetime
    ) -> tuple[ReadyDocumentRef, ...]: ...


class ChatSessionBufferPort(Protocol):
    def append(self, namespace: MemoryNamespace, turn: ChatTurn) -> None: ...
    def read(self, namespace: MemoryNamespace) -> tuple[ChatTurn, ...]: ...
    def clear(self, namespace: MemoryNamespace) -> None: ...


class ChatHistoryPort(Protocol):
    """Durable, UI-facing lifecycle record for chat turns."""

    async def begin_turn(
        self,
        scope: ChatMemoryScope,
        turn: ChatTurn,
        *,
        idempotency_key: str,
        title: str,
    ) -> ChatTurn: ...

    async def update_turn(
        self,
        scope: ChatMemoryScope,
        turn: ChatTurn,
        *,
        title: str | None = None,
    ) -> ChatTurn: ...

    async def write_turn(
        self, scope: ChatMemoryScope, turn: ChatTurn, *, title: str
    ) -> None: ...

    async def list_turns(
        self, scope: ChatMemoryScope, *, connection: object | None = None
    ) -> tuple[ChatTurn, ...]: ...

    async def titles_for(
        self, scopes: Sequence[ChatMemoryScope]
    ) -> Mapping[str, str]: ...

    async def latest_turns_for(
        self, scopes: Sequence[ChatMemoryScope]
    ) -> Mapping[str, ChatTurn]: ...


class DeclarativeMemoryPort(Protocol):
    async def read_profile(self, namespace: MemoryNamespace) -> DeclarativeProfile | None: ...

    async def write_profile(
        self, namespace: MemoryNamespace, profile: DeclarativeProfile
    ) -> DeclarativeProfile: ...

    async def delete_profile(self, namespace: MemoryNamespace) -> bool: ...


class EpisodicMemoryPort(Protocol):
    async def read_task_episode(
        self, namespace: MemoryNamespace, *, episode_id: str
    ) -> TaskEpisode | None: ...

    async def read_episodes(
        self, namespace: MemoryNamespace, query: EpisodicMemoryQuery
    ) -> tuple[TaskEpisode, ...]: ...

    async def list_episodes(
        self, namespace: MemoryNamespace, *, limit: int = 100
    ) -> tuple[TaskEpisode, ...]: ...

    async def write_chat_summary(
        self, namespace: MemoryNamespace, episode: ChatSummaryEpisode
    ) -> ChatSummaryEpisode: ...

    async def write_task_episode(
        self,
        namespace: MemoryNamespace,
        episode: TaskEpisode,
        *,
        expires_at: datetime | None,
    ) -> TaskEpisode: ...

    async def transition_task_episode(
        self, transition: EpisodeTransition
    ) -> TaskEpisode | None: ...

    async def delete_task_episode(self, namespace: MemoryNamespace, *, episode_id: str) -> bool: ...

    async def delete_chat_summary(self, namespace: MemoryNamespace) -> bool: ...

    async def delete_all_for_user(self, namespace: MemoryNamespace) -> int: ...


class SemanticChatMemoryPort(Protocol):
    async def read_semantic_context(
        self, namespace: MemoryNamespace, query: SemanticMemoryQuery
    ) -> Mapping[str, object] | None: ...
