"""Fail-closed Memory Gateway facade for the AI Chat Controller."""

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatTurn,
    DeclarativeProfile,
    DegradedMemorySource,
    MemoryContextRequest,
    MemoryContextResponse,
    MemoryNamespace,
    MemoryProvenance,
    MemoryType,
    TaskEpisode,
)
from cowork_agent.domain.target_contracts import ValidationStatus

from .ports import (
    ChatSessionBufferPort,
    DeclarativeMemoryPort,
    EpisodicMemoryPort,
    SemanticChatMemoryPort,
)
from .profile_policy import authorize_profile_write


class NamespaceAccessDenied(ValueError):
    """The requested memory scope does not match the verified chat scope."""


class MemorySourceUnavailableError(RuntimeError):
    """An optional memory adapter could not serve a bounded read."""


class MemoryGateway:
    """Only feature-level facade for namespaced AI Chat memory access."""

    def __init__(
        self,
        *,
        scope: ChatMemoryScope,
        session_buffer: ChatSessionBufferPort,
        declarative_memory: DeclarativeMemoryPort | None = None,
        episodic_memory: EpisodicMemoryPort | None = None,
        semantic_memory: SemanticChatMemoryPort | None = None,
    ) -> None:
        self._scope = scope
        self._session_buffer = session_buffer
        self._declarative_memory = declarative_memory
        self._episodic_memory = episodic_memory
        self._semantic_memory = semantic_memory

    def append_turn(self, turn: ChatTurn) -> None:
        if turn.session_id != self._scope.session_id:
            raise NamespaceAccessDenied("turn scope does not match the verified chat scope")
        self._session_buffer.append(self._namespace(MemoryType.SHORT_TERM), turn)

    async def read_context(self, request: MemoryContextRequest) -> MemoryContextResponse:
        self._require_scope(request.scope)
        turns = (
            self._session_buffer.read(self._namespace(MemoryType.SHORT_TERM))
            if request.reads.short_term
            else ()
        )
        degraded: list[DegradedMemorySource] = []

        profile = None
        if request.reads.long_term:
            if self._declarative_memory is None:
                degraded.append(DegradedMemorySource.LONG_TERM)
            else:
                try:
                    profile = await self._declarative_memory.read_profile(
                        self._namespace(MemoryType.LONG_TERM)
                    )
                except MemorySourceUnavailableError:
                    degraded.append(DegradedMemorySource.LONG_TERM)
                if profile is not None and (
                    profile.tenant_id != self._scope.tenant_id
                    or profile.user_id != self._scope.user_id
                ):
                    raise NamespaceAccessDenied("profile scope does not match the verified scope")

        episodes: tuple[TaskEpisode, ...] = ()
        if request.reads.episodic.enabled:
            if self._episodic_memory is None:
                degraded.append(DegradedMemorySource.EPISODIC)
            else:
                try:
                    candidates = await self._episodic_memory.read_episodes(
                        self._namespace(MemoryType.EPISODIC),
                        max_items=request.reads.episodic.max_items,
                    )
                except MemorySourceUnavailableError:
                    degraded.append(DegradedMemorySource.EPISODIC)
                else:
                    episodes = tuple(
                        episode
                        for episode in candidates
                        if episode.retrieval_eligible
                        and episode.validation_status
                        in {ValidationStatus.USER_APPROVED, ValidationStatus.COMPLETED}
                        and episode.tenant_id == self._scope.tenant_id
                        and episode.user_id == self._scope.user_id
                    )[: request.reads.episodic.max_items]

        semantic_context = None
        if request.reads.semantic.enabled:
            if self._semantic_memory is None:
                degraded.append(DegradedMemorySource.SEMANTIC)
            else:
                try:
                    semantic_context = await self._semantic_memory.read_semantic_context(
                        self._namespace(MemoryType.SEMANTIC)
                    )
                except MemorySourceUnavailableError:
                    degraded.append(DegradedMemorySource.SEMANTIC)

        return MemoryContextResponse(
            turns=turns,
            profile=profile,
            episodes=episodes,
            semantic_context=semantic_context,
            degraded=bool(degraded),
            degraded_sources=tuple(degraded),
        )

    async def write_profile(
        self, profile: DeclarativeProfile, *, provenance: MemoryProvenance
    ) -> DeclarativeProfile:
        """Persist an explicit profile; refuses anything but an authorized write.

        Unlike the per-turn read, a write failure is not degraded away: the user
        asked for it, so ``MemorySourceUnavailableError`` propagates.
        """

        adapter = self._require_declarative_memory()
        namespace = self._namespace(MemoryType.LONG_TERM)
        if profile.tenant_id != self._scope.tenant_id or profile.user_id != self._scope.user_id:
            raise NamespaceAccessDenied("profile scope does not match the verified chat scope")
        authorize_profile_write(namespace, profile, provenance)
        return await adapter.write_profile(namespace, profile)

    async def delete_profile(self) -> bool:
        """Delete the in-scope profile (FR-15); later reads must miss."""

        return await self._require_declarative_memory().delete_profile(
            self._namespace(MemoryType.LONG_TERM)
        )

    def _require_declarative_memory(self) -> DeclarativeMemoryPort:
        if self._declarative_memory is None:
            raise MemorySourceUnavailableError("no declarative memory adapter is configured")
        return self._declarative_memory

    def clear_session(self) -> None:
        self._session_buffer.clear(self._namespace(MemoryType.SHORT_TERM))

    def _require_scope(self, requested: ChatMemoryScope) -> None:
        if requested != self._scope:
            raise NamespaceAccessDenied("requested scope does not match the verified chat scope")

    def _namespace(self, memory_type: MemoryType) -> MemoryNamespace:
        return MemoryNamespace(
            scope=self._scope,
            memory_type=memory_type,
            record_id=self._scope.session_id if memory_type is MemoryType.SHORT_TERM else None,
            source_id=None,
        )
