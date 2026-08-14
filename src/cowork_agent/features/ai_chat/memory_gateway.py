"""Fail-closed Memory Gateway facade for the AI Chat Controller."""

from collections.abc import Callable
from datetime import datetime
from time import monotonic

from langfuse import observe

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
from cowork_agent.domain.project_documents import (
    ProjectDocumentQuery,
    ProjectDocumentResponse,
)
from cowork_agent.domain.target_contracts import ValidationStatus
from cowork_agent.features.user_documents.ports import ProjectDocumentRetrievalPort

from .deletion import MemoryDeletionReport
from .episode_policy import (
    TaskEpisodeTransitionRejected,
    authorize_chat_summary_write,
    authorize_task_episode_write,
    build_task_episode_transition,
)
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
        project_documents: ProjectDocumentRetrievalPort | None = None,
        memory_operation_sink: MemoryOperationSink | None = None,
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        self._scope = scope
        self._session_buffer = session_buffer
        self._declarative_memory = declarative_memory
        self._episodic_memory = episodic_memory
        self._semantic_memory = semantic_memory
        self._project_documents = project_documents
        self._memory_operation_sink = memory_operation_sink or NullMemoryOperationSink()
        self._monotonic_clock = monotonic_clock

    def append_turn(self, turn: ChatTurn) -> bool:
        if turn.session_id != self._scope.session_id:
            raise NamespaceAccessDenied("turn scope does not match the verified chat scope")
        self._session_buffer.append(self._namespace(MemoryType.SHORT_TERM), turn)
        return True

    def _read_active_turns(self) -> tuple[ChatTurn, ...]:
        """Read the verified session buffer for bounded classifier evidence."""

        return self._session_buffer.read(self._namespace(MemoryType.SHORT_TERM))

    async def _read_project_documents(
        self,
        *,
        query: str,
        document_ids: tuple[str, ...] = (),
        top_k: int = 8,
        min_score: float = 0.6,
        timeout_ms: int = 3_000,
    ) -> ProjectDocumentResponse:
        if self._project_documents is None:
            return ProjectDocumentResponse(
                (), degraded=True, reason_code="project_document_store_not_configured"
            )
        return await self._project_documents.retrieve(
            ProjectDocumentQuery(
                tenant_id=self._scope.tenant_id,
                user_id=self._scope.user_id,
                project_id=self._scope.project_id,
                query=query,
                document_ids=document_ids,
                top_k=top_k,
                min_score=min_score,
                timeout_ms=timeout_ms,
            )
        )

    @observe(as_type="retriever", name="chat_memory_read_context")
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
        degraded: list[DegradedMemorySource] = []
        turns: tuple[ChatTurn, ...] = ()
        if request.reads.short_term:
            started = self._monotonic_clock()
            turns = self._session_buffer.read(self._namespace(MemoryType.SHORT_TERM))
            self._emit(
                MemoryType.SHORT_TERM,
                MemoryOperation.READ,
                MemoryOutcome.SUCCESS,
                result_count=len(turns),
                started=started,
            )

        profile = None
        if request.reads.long_term:
            started = self._monotonic_clock()
            if self._declarative_memory is None:
                degraded.append(DegradedMemorySource.LONG_TERM)
                self._emit(
                    MemoryType.LONG_TERM,
                    MemoryOperation.READ,
                    MemoryOutcome.DEGRADED,
                    "not_configured",
                    started=started,
                )
            else:
                try:
                    profile = await self._declarative_memory.read_profile(
                        self._namespace(MemoryType.LONG_TERM)
                    )
                except MemorySourceUnavailableError:
                    degraded.append(DegradedMemorySource.LONG_TERM)
                    self._emit(
                        MemoryType.LONG_TERM,
                        MemoryOperation.READ,
                        MemoryOutcome.DEGRADED,
                        "unavailable",
                        started=started,
                    )
                else:
                    if profile is not None and (
                        profile.user_id != self._scope.user_id
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
                    MemoryType.EPISODIC,
                    MemoryOperation.READ,
                    MemoryOutcome.DEGRADED,
                    "not_configured",
                    started=started,
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
                        MemoryType.EPISODIC,
                        MemoryOperation.READ,
                        MemoryOutcome.DEGRADED,
                        "unavailable",
                        started=started,
                    )
                else:
                    episodes = tuple(
                        episode
                        for episode in candidates
                        if episode.retrieval_eligible
                        and episode.validation_status
                        in {ValidationStatus.USER_APPROVED, ValidationStatus.COMPLETED}
                        and episode.user_id == self._scope.user_id
                    )[: request.reads.episodic.max_items]
                    self._emit(
                        MemoryType.EPISODIC,
                        MemoryOperation.READ,
                        MemoryOutcome.SUCCESS,
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
                    MemoryType.SEMANTIC,
                    MemoryOperation.READ,
                    MemoryOutcome.DEGRADED,
                    "not_configured",
                    started=started,
                )
            else:
                try:
                    semantic_context = await self._semantic_memory.read_semantic_context(
                        self._namespace(MemoryType.SEMANTIC), request.reads.semantic
                    )
                except MemorySourceUnavailableError:
                    degraded.append(DegradedMemorySource.SEMANTIC)
                    self._emit(
                        MemoryType.SEMANTIC,
                        MemoryOperation.READ,
                        MemoryOutcome.DEGRADED,
                        "unavailable",
                        started=started,
                    )
                else:
                    self._emit(
                        MemoryType.SEMANTIC,
                        MemoryOperation.READ,
                        MemoryOutcome.SUCCESS,
                        result_count=int(semantic_context is not None),
                        started=started,
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
        if profile.user_id != self._scope.user_id:
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
            MemoryType.LONG_TERM,
            MemoryOperation.DELETE,
            MemoryOutcome.SUCCESS,
            result_count=int(result),
        )
        return result

    async def write_chat_summary(self, episode: ChatSummaryEpisode) -> ChatSummaryEpisode:
        """Persist only a bounded, system-generated, retrieval-ineligible chat summary."""

        if (
            episode.user_id != self._scope.user_id
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

    async def write_task_episode(
        self, episode: TaskEpisode, *, expires_at: datetime | None
    ) -> TaskEpisode:
        """Persist an authorized initial task episode at its supplied identity."""

        if (
            episode.user_id != self._scope.user_id
            or episode.chat_session_id != self._scope.session_id
        ):
            raise NamespaceAccessDenied("task episode scope does not match the verified chat scope")
        namespace = MemoryNamespace(
            scope=self._scope,
            memory_type=MemoryType.EPISODIC,
            record_id=episode.record_id,
            source_id=episode.chat_turn_id,
        )
        trusted_episode = authorize_task_episode_write(namespace, episode, expires_at=expires_at)
        result = await self._require_episodic_memory().write_task_episode(
            namespace, trusted_episode, expires_at=expires_at
        )
        self._emit(MemoryType.EPISODIC, MemoryOperation.WRITE, MemoryOutcome.SUCCESS)
        return result

    async def _read_task_episode(self, episode_id: str) -> TaskEpisode | None:
        """Load one originating-session episode for a request-scoped controller."""

        namespace = self._namespace(MemoryType.EPISODIC)
        result = await self._require_episodic_memory().read_task_episode(
            namespace, episode_id=episode_id
        )
        if result is not None and (
            result.user_id != self._scope.user_id
            or result.chat_session_id != self._scope.session_id
        ):
            raise NamespaceAccessDenied("task episode scope does not match the verified scope")
        return result

    async def transition_task_episode(
        self,
        *,
        record_id: str,
        chat_turn_id: str,
        episode_id: str,
        from_status: ValidationStatus,
        to_status: ValidationStatus,
        transitioned_at: datetime,
    ) -> TaskEpisode | None:
        """Transition one originating-session task episode without caller eligibility control."""

        namespace = MemoryNamespace(
            scope=self._scope,
            memory_type=MemoryType.EPISODIC,
            record_id=record_id,
            source_id=chat_turn_id,
        )
        transition = build_task_episode_transition(
            namespace,
            episode_id=episode_id,
            from_status=from_status,
            to_status=to_status,
            transitioned_at=transitioned_at,
        )
        self._emit(MemoryType.EPISODIC, MemoryOperation.WRITE, MemoryOutcome.REQUESTED)
        result = await self._require_episodic_memory().transition_task_episode(transition)
        self._emit(
            MemoryType.EPISODIC,
            MemoryOperation.WRITE,
            MemoryOutcome.SUCCESS,
            result_count=int(result is not None),
        )
        return result

    async def delete_task_episode(
        self, *, record_id: str, chat_turn_id: str, episode_id: str
    ) -> bool:
        """Delete exactly one originating-session task episode; a miss is idempotent."""

        namespace = MemoryNamespace(
            scope=self._scope,
            memory_type=MemoryType.EPISODIC,
            record_id=record_id,
            source_id=chat_turn_id,
        )
        if not isinstance(episode_id, str) or not episode_id.strip():
            raise TaskEpisodeTransitionRejected("episode_id must be a nonempty string")
        self._emit(MemoryType.EPISODIC, MemoryOperation.DELETE, MemoryOutcome.REQUESTED)
        result = await self._require_episodic_memory().delete_task_episode(
            namespace, episode_id=episode_id
        )
        self._emit(
            MemoryType.EPISODIC,
            MemoryOperation.DELETE,
            MemoryOutcome.SUCCESS,
            result_count=int(result),
        )
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
            MemoryType.EPISODIC,
            MemoryOperation.DELETE,
            MemoryOutcome.SUCCESS,
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
            MemoryType.LONG_TERM,
            MemoryOperation.DELETE,
            MemoryOutcome.SUCCESS,
            result_count=int(profile_deleted),
        )
        self._emit(
            MemoryType.EPISODIC,
            MemoryOperation.DELETE,
            MemoryOutcome.SUCCESS,
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
                int((self._monotonic_clock() - started) * 1000) if started is not None else 0
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
