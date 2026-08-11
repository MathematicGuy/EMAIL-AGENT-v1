"""Application-layer ports owned by the AI Chat feature."""

from collections.abc import AsyncIterator, Mapping
from datetime import datetime
from typing import Protocol

from cowork_agent.domain.chat_contracts import (
    ChatMessageRequest,
    ChatSummaryEpisode,
    ChatTurn,
    DeclarativeProfile,
    EpisodeTransition,
    EpisodicMemoryQuery,
    MemoryContextResponse,
    MemoryNamespace,
    SemanticMemoryQuery,
    TaskEpisode,
)


class ChatReplyPort(Protocol):
    """Stream one assistant reply from already-bounded, typed chat context."""

    def stream_reply(
        self,
        request: ChatMessageRequest,
        context: MemoryContextResponse,
    ) -> AsyncIterator[str]: ...


class ChatSessionBufferPort(Protocol):
    def append(self, namespace: MemoryNamespace, turn: ChatTurn) -> None: ...
    def read(self, namespace: MemoryNamespace) -> tuple[ChatTurn, ...]: ...
    def clear(self, namespace: MemoryNamespace) -> None: ...


class DeclarativeMemoryPort(Protocol):
    async def read_profile(
        self, namespace: MemoryNamespace
    ) -> DeclarativeProfile | None: ...

    async def write_profile(
        self, namespace: MemoryNamespace, profile: DeclarativeProfile
    ) -> DeclarativeProfile: ...

    async def delete_profile(self, namespace: MemoryNamespace) -> bool: ...


class EpisodicMemoryPort(Protocol):
    async def read_episodes(
        self, namespace: MemoryNamespace, query: EpisodicMemoryQuery
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

    async def delete_task_episode(
        self, namespace: MemoryNamespace, *, episode_id: str
    ) -> bool: ...

    async def delete_chat_summary(self, namespace: MemoryNamespace) -> bool: ...

    async def delete_all_for_user(self, namespace: MemoryNamespace) -> int: ...


class SemanticChatMemoryPort(Protocol):
    async def read_semantic_context(
        self, namespace: MemoryNamespace, query: SemanticMemoryQuery
    ) -> Mapping[str, object] | None: ...
