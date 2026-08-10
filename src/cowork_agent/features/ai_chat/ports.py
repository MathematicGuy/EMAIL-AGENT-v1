"""Application-layer ports owned by the AI Chat memory feature."""

from collections.abc import Mapping
from typing import Protocol

from cowork_agent.domain.chat_contracts import (
    ChatTurn,
    DeclarativeProfile,
    MemoryNamespace,
    TaskEpisode,
)


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
        self, namespace: MemoryNamespace, *, max_items: int
    ) -> tuple[TaskEpisode, ...]: ...


class SemanticChatMemoryPort(Protocol):
    async def read_semantic_context(
        self, namespace: MemoryNamespace
    ) -> Mapping[str, object] | None: ...
