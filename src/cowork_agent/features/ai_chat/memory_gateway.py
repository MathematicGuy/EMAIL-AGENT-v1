"""Fail-closed Memory Gateway facade for the AI Chat Controller."""

from collections.abc import Callable
from time import monotonic

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatSummaryEpisode,
    ChatTurn,
    DeclarativeProfile,
    DegradedMemorySource,
    EpisodicMemoryQuery,
    MemoryContextRequest,
    MemoryContextResponse,
    MemoryNamespace,
    MemoryProvenance,
    MemoryType,
    SemanticMemoryQuery,
    TaskEpisode,
)
from cowork_agent.domain.target_contracts import ValidationStatus

from .deletion import MemoryDeletionReport
from .episode_policy import authorize_chat_summary_write
from .memory_observability import (
    MemoryOperation,
    MemoryOperationEvent,
    MemoryOperationSink,
    MemoryOutcome,
    NullMemoryOperationSink,
)
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
        memory_operation_sink: MemoryOperationSink | None = None,
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        self._scope = scope
        self._session_buffer = session_buffer
        self._declarative_memory = declarative_memory
        self._episodic_memory = episodic_memory
        self._semantic_memory = semantic_memory
        self._memory_operation_sink = memory_operation_sink or NullMemoryOperationSink()
        self._monotonic_clock = monotonic_clock

    def append_turn(self, turn: ChatTurn) -> None:
        if turn.session_id != self._scope.session_id:
            raise NamespaceAccessDenied("turn scope does not match the verified chat scope")
        self._session_buffer.append(self._namespace(MemoryType.SHORT_TERM), turn)

    async def read_context(self, request: MemoryContextRequest) -> MemoryContextResponse:
        try:
            self._require_scope(request.scope)
        except NamespaceAccessDenied:
            self._emit(
                MemoryType.SHORT_TERM,
                MemoryOperation.READ,
                MemoryOutcome.DENIED,
                "scope_denied",
            )
            raise
        turns = (
            self._session_buffer.read(self._namespace(MemoryType.SHORT_TERM))
            if request.reads.short_term
            else ()
        )
        degraded: list[DegradedMemorySource] = []

        profile = None
        if request.reads.long_term:
            started = self._monotonic_clock()
            if self._declarative_memory is None:
                degraded.append(DegradedMemorySource.LONG_TERM)
                self._emit(
                    MemoryType.LONG_TERM, MemoryOperation.READ, MemoryOutcome.DEGRADED,
                    "not_configured", started=started,
                )
            else:
                try:
                    profile = await self._declarative_memory.read_profile(
                        self._namespace(MemoryType.LONG_TERM)
                    )
                except MemorySourceUnavailableError:
                    degraded.append(DegradedMemorySource.LONG_TERM)
                    self._emit(
                        MemoryType.LONG_TERM, MemoryOperation.READ, MemoryOutcome.DEGRADED,
                        "unavailable", started=started,
                    )
                else:
                    if profile is not None and (
                        profile.tenant_id != self._scope.tenant_id
                        or profile.user_id != self._scope.user_id
                    ):
                        self._emit(
                            MemoryType.LONG_TERM,
                            MemoryOperation.READ,
                            MemoryOutcome.DENIED,
                            "profile_scope_denied",
                            started=started,
                        )
                        raise NamespaceAccessDenied(
                            "profile scope does not match the verified scope"
                        )
                    self._emit(
                        MemoryType.LONG_TERM,
                        MemoryOperation.READ,
                        MemoryOutcome.SUCCESS,
                        result_count=int(profile is not None),
                        started=started,
                    )

        episodes: tuple[TaskEpisode, ...] = ()
        if isinstance(request.reads.episodic, EpisodicMemoryQuery):
            started = self._monotonic_clock()
            self._emit(MemoryType.EPISODIC, MemoryOperation.READ, MemoryOutcome.REQUESTED)
            if self._episodic_memory is None:
                degraded.append(DegradedMemorySource.EPISODIC)
                self._emit(
                    MemoryType.EPISODIC, MemoryOperation.READ, MemoryOutcome.DEGRADED,
                    "not_configured", started=started,
                )
            else:
                try:
                    candidates = await self._episodic_memory.read_episodes(
                        self._namespace(MemoryType.EPISODIC),
                        request.reads.episodic,
                    )
                except MemorySourceUnavailableError:
                    degraded.append(DegradedMemorySource.EPISODIC)
                    self._emit(
                        MemoryType.EPISODIC, MemoryOperation.READ, MemoryOutcome.DEGRADED,
                        "unavailable", started=started,
                    )
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
                    self._emit(
                        MemoryType.EPISODIC, MemoryOperation.READ, MemoryOutcome.SUCCESS,
                        result_count=len(episodes),
                        filtered_count=max(0, len(candidates) - len(episodes)),
                        started=started,
                    )

        semantic_context = None
        if isinstance(request.reads.semantic, SemanticMemoryQuery):
            started = self._monotonic_clock()
            self._emit(MemoryType.SEMANTIC, MemoryOperation.READ, MemoryOutcome.REQUESTED)
            if self._semantic_memory is None:
                degraded.append(DegradedMemorySource.SEMANTIC)
                self._emit(
                    MemoryType.SEMANTIC, MemoryOperation.READ, MemoryOutcome.DEGRADED,
                    "not_configured", started=started,
                )
            else:
                try:
                    semantic_context = await self._semantic_memory.read_semantic_context(
                        self._namespace(MemoryType.SEMANTIC), request.reads.semantic
                    )
                except MemorySourceUnavailableError:
                    degraded.append(DegradedMemorySource.SEMANTIC)
                    self._emit(
                        MemoryType.SEMANTIC, MemoryOperation.READ, MemoryOutcome.DEGRADED,
                        "unavailable", started=started,
                    )
                else:
                    self._emit(
                        MemoryType.SEMANTIC, MemoryOperation.READ, MemoryOutcome.SUCCESS,
                        result_count=int(semantic_context is not None), started=started,
                    )

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
        result = await adapter.write_profile(namespace, profile)
        self._emit(MemoryType.LONG_TERM, MemoryOperation.WRITE, MemoryOutcome.SUCCESS)
        return result

    async def delete_profile(self) -> bool:
        """Delete the in-scope profile (FR-15); later reads must miss."""

        self._emit(MemoryType.LONG_TERM, MemoryOperation.DELETE, MemoryOutcome.REQUESTED)
        result = await self._require_declarative_memory().delete_profile(
            self._namespace(MemoryType.LONG_TERM)
        )
        self._emit(
            MemoryType.LONG_TERM, MemoryOperation.DELETE, MemoryOutcome.SUCCESS,
            result_count=int(result),
        )
        return result

    async def write_chat_summary(self, episode: ChatSummaryEpisode) -> ChatSummaryEpisode:
        """Persist only a bounded, system-generated, retrieval-ineligible chat summary."""

        if (
            episode.tenant_id != self._scope.tenant_id
            or episode.user_id != self._scope.user_id
            or episode.chat_session_id != self._scope.session_id
        ):
            raise NamespaceAccessDenied("summary scope does not match the verified chat scope")
        namespace = MemoryNamespace(
            scope=self._scope,
            memory_type=MemoryType.EPISODIC,
            record_id=episode.record_id,
            source_id=episode.chat_turn_id,
        )
        authorize_chat_summary_write(namespace, episode)
        result = await self._require_episodic_memory().write_chat_summary(namespace, episode)
        self._emit(MemoryType.EPISODIC, MemoryOperation.WRITE, MemoryOutcome.SUCCESS)
        return result

    async def delete_chat_summary(self, record_id: str) -> bool:
        """Delete exactly one in-scope system-generated chat-summary record."""

        namespace = MemoryNamespace(
            scope=self._scope,
            memory_type=MemoryType.EPISODIC,
            record_id=record_id,
            source_id=None,
        )
        self._emit(MemoryType.EPISODIC, MemoryOperation.DELETE, MemoryOutcome.REQUESTED)
        result = await self._require_episodic_memory().delete_chat_summary(namespace)
        self._emit(
            MemoryType.EPISODIC, MemoryOperation.DELETE, MemoryOutcome.SUCCESS,
            result_count=int(result),
        )
        return result

    async def delete_all_memory(self) -> MemoryDeletionReport:
        """Delete only this user's implemented Chat memory; never company RAG."""

        declarative = self._require_declarative_memory()
        episodic = self._require_episodic_memory()
        profile_namespace = self._namespace(MemoryType.LONG_TERM)
        episodic_namespace = self._namespace(MemoryType.EPISODIC)
        self._emit(MemoryType.LONG_TERM, MemoryOperation.DELETE, MemoryOutcome.REQUESTED)
        profile_deleted = await declarative.delete_profile(profile_namespace)
        self._emit(MemoryType.EPISODIC, MemoryOperation.DELETE, MemoryOutcome.REQUESTED)
        episodic_deleted_count = await episodic.delete_all_for_user(episodic_namespace)
        self._session_buffer.clear(self._namespace(MemoryType.SHORT_TERM))
        self._emit(
            MemoryType.LONG_TERM, MemoryOperation.DELETE, MemoryOutcome.SUCCESS,
            result_count=int(profile_deleted),
        )
        self._emit(
            MemoryType.EPISODIC, MemoryOperation.DELETE, MemoryOutcome.SUCCESS,
            result_count=episodic_deleted_count,
        )
        return MemoryDeletionReport(True, profile_deleted, episodic_deleted_count, True)

    def _emit(
        self,
        memory_type: MemoryType,
        operation: MemoryOperation,
        outcome: MemoryOutcome,
        reason_code: str | None = None,
        *,
        result_count: int = 0,
        filtered_count: int = 0,
        started: float | None = None,
    ) -> None:
        try:
            latency_ms = (
                int((self._monotonic_clock() - started) * 1000)
                if started is not None
                else 0
            )
            self._memory_operation_sink.emit(
                MemoryOperationEvent(
                    memory_type,
                    operation,
                    outcome,
                    min(10_000, max(0, result_count)),
                    min(10_000, max(0, filtered_count)),
                    min(10_000, max(0, latency_ms)),
                    reason_code,
                )
            )
        except Exception:
            # Observability is optional and must never alter memory semantics.
            return

    def _require_declarative_memory(self) -> DeclarativeMemoryPort:
        if self._declarative_memory is None:
            raise MemorySourceUnavailableError("no declarative memory adapter is configured")
        return self._declarative_memory

    def _require_episodic_memory(self) -> EpisodicMemoryPort:
        if self._episodic_memory is None:
            raise MemorySourceUnavailableError("no episodic memory adapter is configured")
        return self._episodic_memory

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
