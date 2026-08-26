"""Framework-free V2-M4A Chat Controller and in-memory session ownership."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Literal, Protocol, cast
from uuid import uuid4

from langfuse import observe

from cowork_agent.domain.chat_contracts import (
    MAX_CHAT_RAG_EVIDENCE_ITEMS,
    MAX_EXECUTION_REASONING_LENGTH,
    ChatActivity,
    ChatActivityCode,
    ChatActivityDetail,
    ChatActivityOutcome,
    ChatActivityStatus,
    ChatExecutionTrace,
    ChatMemoryScope,
    ChatMessageRequest,
    ChatMessageStreamEvent,
    ChatRagEvidence,
    ChatRoute,
    ChatTurn,
    ChatTurnStatus,
    MemoryCitationType,
    MemoryContextRequest,
    RoutingOutcome,
    TaskEpisode,
    transition_activity_snapshot,
)
from cowork_agent.domain.project_documents import ProjectDocumentEvidence, ProjectDocumentResponse
from cowork_agent.domain.report_artifacts import (
    DEFAULT_REPORT_STEM,
    ReportArtifact,
    ReportArtifactStore,
    ReportFilename,
)
from cowork_agent.domain.target_contracts import ValidationStatus

from .generation_context import (
    ChatResponseMode,
    GenerationContext,
    assemble_generation_context,
)
from .intent.service import ChatRoutingService
from .memory_gateway import MemoryGateway
from .ports import (
    ChatHistoryPort,
    ChatReplyChunk,
    ChatReplyPort,
    ChatTaskProposal,
    GeneratedReportArtifact,
)
from .retrieval_policy import (
    clarification_memory_reads,
    is_explicit_task_request,
    select_memory_reads,
)
from .task_episode_settlement import (
    PendingTaskEpisode,
    TaskEpisodeSettler,
    TurnAborted,
)
from .tools.runner import ChatToolRunner
from .turn_journal import (
    CancellationCheck,
    CancellationGuard,
    Clock,
    IdFactory,
    TurnJournal,
    never_cancelled,
)

logger = logging.getLogger(__name__)


def _new_id() -> str:
    return str(uuid4())


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _fallback_conversation_title(message: str) -> str:
    """Keep the sidebar useful if a configured title-capable LLM is unavailable."""

    normalized = " ".join(message.split())
    return normalized[:120] or "New chat"


def _rag_evidence(
    context: GenerationContext, project_documents: ProjectDocumentResponse | None
) -> tuple[
    tuple[ChatRagEvidence, ...],
    Literal["success", "no_results", "timeout", "unavailable"] | None,
]:
    """Project the exact retrieval payload into persisted chat evidence."""

    if project_documents is not None:
        if project_documents.degraded:
            return (), "unavailable"
        if not project_documents.evidence:
            return (), "no_results"
        return (
            tuple(
                _project_evidence_to_rag_evidence(item)
                for item in project_documents.evidence[:MAX_CHAT_RAG_EVIDENCE_ITEMS]
            ),
            "success",
        )

    labeled_evidence = context.current_company_evidence
    if labeled_evidence is None:
        return (), None

    company_evidence = labeled_evidence.value
    raw_retrieval_status = company_evidence.retrieval_status
    if raw_retrieval_status not in {"success", "no_results", "timeout", "unavailable"}:
        return (), None
    retrieval_status = cast(
        Literal["success", "no_results", "timeout", "unavailable"], raw_retrieval_status
    )
    if retrieval_status != "success":
        return (), retrieval_status

    evidence: list[ChatRagEvidence] = []
    for chunk in company_evidence.chunks[:5]:
        item = _company_chunk_to_rag_evidence(chunk)
        if item is not None:
            evidence.append(item)
    return tuple(evidence), retrieval_status


def _project_evidence_to_rag_evidence(evidence: ProjectDocumentEvidence) -> ChatRagEvidence:
    """Map the returned project chunk without changing its retrieval score or rank."""

    content = evidence.text[:16_000]
    preview = " ".join(content.split())[:400]
    return ChatRagEvidence(
        source="project_document",
        retrieval_status="success",
        chunk_id=evidence.chunk_id,
        document_id=evidence.document_id,
        document_title=evidence.title,
        section=evidence.section,
        source_url=None,
        relevance_score=evidence.score,
        rerank_score=None,
        preview=preview,
        content=content,
    )


def _company_chunk_to_rag_evidence(chunk: Mapping[str, object]) -> ChatRagEvidence | None:
    """Create display evidence without re-querying or altering retrieval rank."""

    chunk_id = chunk.get("chunk_id")
    document_id = chunk.get("document_id")
    document_title = chunk.get("document_title")
    section = chunk.get("section")
    text = chunk.get("text")
    source_url = chunk.get("source_url")
    relevance_score = chunk.get("relevance_score")
    rerank_score = chunk.get("rerank_score")
    if not isinstance(chunk_id, str):
        return None
    if not isinstance(document_id, str):
        return None
    if not isinstance(document_title, str):
        return None
    if not isinstance(text, str):
        return None
    if not isinstance(source_url, str):
        return None
    if section is not None and not isinstance(section, str):
        return None
    if isinstance(relevance_score, bool) or not isinstance(relevance_score, int | float):
        return None
    if rerank_score is not None and (
        isinstance(rerank_score, bool) or not isinstance(rerank_score, int | float)
    ):
        return None

    content = text[:16_000]
    preview = " ".join(content.split())[:400]
    if not content or not preview:
        return None
    return ChatRagEvidence(
        source="company_knowledge",
        retrieval_status="success",
        chunk_id=chunk_id,
        document_id=document_id,
        document_title=document_title,
        section=section,
        source_url=source_url,
        relevance_score=float(relevance_score),
        rerank_score=float(rerank_score) if rerank_score is not None else None,
        preview=preview,
        content=content,
    )


class ChatScopeMismatch(ValueError):
    """The message does not belong to the controller's verified session."""


class ChatSessionAccessDenied(LookupError):
    """The session is absent or belongs to another verified principal."""


class ChatSessionRegistryPort(Protocol):
    """Durable or local authority for chat-session ownership."""

    async def create(
        self,
        *,
        user_id: str,
        tenant_id: str = "local",
        project_id: str = "default-project",
    ) -> ChatMemoryScope: ...

    async def require(
        self,
        session_id: str,
        *,
        user_id: str,
        tenant_id: str = "local",
        connection: object | None = None,
    ) -> ChatMemoryScope: ...

    async def list_for(
        self,
        *,
        user_id: str,
        tenant_id: str = "local",
        project_id: str | None = None,
    ) -> tuple[ChatMemoryScope, ...]: ...

    async def delete(
        self, session_id: str, *, user_id: str, tenant_id: str = "local"
    ) -> bool: ...

    async def delete_project(
        self, *, user_id: str, project_id: str, tenant_id: str = "local"
    ) -> tuple[str, ...]: ...


class ChatReplyUnavailable(RuntimeError):
    """The configured chat-response provider cannot serve this turn."""


class ChatResponseInvalid(ChatReplyUnavailable):
    """The provider answered, but the answer broke the response contract.

    A subclass rather than a sibling so every existing handler still catches it
    and the turn still fails closed. What changes is what the failure is called:
    the provider being down and the model returning an uncitable answer produce
    the same empty turn, and calling both "provider unavailable" sent the memory
    evaluation's triage looking for a network fault that was never there. It
    also fed a circuit breaker that exists to stop spending calls on a dead
    provider — a breaker a validation bug must not trip.
    """


class UnavailableChatReply:
    """Fail-closed runtime default until a chat-capable LLM adapter is configured."""

    async def stream_reply(
        self,
        request: ChatMessageRequest,
        context: GenerationContext,
    ) -> AsyncIterator[str]:
        del request, context
        raise ChatReplyUnavailable("no chat reply adapter is configured")
        yield  # pragma: no cover - keeps the method an async iterator


class InMemoryChatSessionRegistry:
    """Bind opaque session IDs to one verified tenant/user scope."""

    def __init__(self, *, new_id: IdFactory = _new_id) -> None:
        self._new_id = new_id
        self._sessions: dict[str, ChatMemoryScope] = {}
        self._lock = threading.Lock()

    async def create(
        self, *, user_id: str, tenant_id: str = "local", project_id: str = "default-project"
    ) -> ChatMemoryScope:
        with self._lock:
            session_id = self._new_id()
            while session_id in self._sessions:
                session_id = self._new_id()
            scope = ChatMemoryScope(
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                project_id=project_id,
            )
            self._sessions[session_id] = scope
            return scope

    async def require(
        self,
        session_id: str,
        *,
        user_id: str,
        tenant_id: str = "local",
        connection: object | None = None,
    ) -> ChatMemoryScope:
        del connection
        with self._lock:
            scope = self._sessions.get(session_id)
        if scope is None or scope.tenant_id != tenant_id or scope.user_id != user_id:
            raise ChatSessionAccessDenied(session_id)
        return scope

    async def register(self, scope: ChatMemoryScope) -> ChatMemoryScope:
        """Restore one durable scope into the process-local controller registry."""
        with self._lock:
            existing = self._sessions.get(scope.session_id)
            if existing is not None and existing != scope:
                raise ChatSessionAccessDenied(scope.session_id)
            self._sessions[scope.session_id] = scope
        return scope

    async def list_for(
        self, *, user_id: str, tenant_id: str = "local", project_id: str | None = None
    ) -> tuple[ChatMemoryScope, ...]:
        """Owned scopes in creation order (GET /sessions read contract)."""
        with self._lock:
            return tuple(
                scope
                for scope in self._sessions.values()
                if scope.tenant_id == tenant_id
                and scope.user_id == user_id
                and (project_id is None or scope.project_id == project_id)
            )

    async def delete_project(
        self, *, user_id: str, project_id: str, tenant_id: str = "local"
    ) -> tuple[str, ...]:
        with self._lock:
            removed = tuple(
                session_id
                for session_id, scope in self._sessions.items()
                if scope.tenant_id == tenant_id
                and scope.user_id == user_id
                and scope.project_id == project_id
            )
            for session_id in removed:
                del self._sessions[session_id]
        return removed

    async def delete(
        self, session_id: str, *, user_id: str, tenant_id: str = "local"
    ) -> bool:
        with self._lock:
            scope = self._sessions.get(session_id)
            if scope is None or scope.tenant_id != tenant_id or scope.user_id != user_id:
                return False
            del self._sessions[session_id]
        return True


class ChatController:
    """Assemble bounded context and stream one non-tool chat turn."""

    def __init__(
        self,
        *,
        scope: ChatMemoryScope,
        memory: MemoryGateway,
        reply: ChatReplyPort,
        new_id: IdFactory = _new_id,
        clock: Clock = _utc_now,
        episode_retention_seconds: int | None = None,
        routing: ChatRoutingService | None = None,
        company_rag_enabled: bool = True,
        history: ChatHistoryPort | None = None,
        reports: ReportArtifactStore | None = None,
        tools: ChatToolRunner | None = None,
    ) -> None:
        self._scope = scope
        self._memory = memory
        self._reply = reply
        self._reports = reports
        self._new_id = new_id
        self._clock = clock
        self._episode_retention_seconds = episode_retention_seconds
        self._routing = routing
        self._company_rag_enabled = company_rag_enabled
        self._history = history
        self._tools = tools
        self._completed: dict[
            str, tuple[ChatMessageRequest, tuple[ChatMessageStreamEvent, ...]]
        ] = {}
        self._task_episodes: dict[str, TaskEpisode] = {}
        self._routing_outcomes: dict[str, RoutingOutcome] = {}
        self._turns_by_id: dict[str, ChatTurn] = {}
        self._cancelled_turn_ids: set[str] = set()
        self._turn_lock = asyncio.Lock()
        self._settler = TaskEpisodeSettler(
            scope=scope,
            memory=memory,
            new_id=new_id,
            clock=clock,
            episode_retention_seconds=episode_retention_seconds,
            episodes=self._task_episodes,
            completed=self._completed,
            turns_by_id=self._turns_by_id,
        )

    async def cancel_turn(self, turn_id: str) -> bool:
        """Cancel only the named active turn; navigation never calls this method."""

        turn = self._turns_by_id.get(turn_id)
        if turn is None or turn.status is not ChatTurnStatus.GENERATING:
            return False
        self._cancelled_turn_ids.add(turn_id)
        cancelled = self._terminal_turn(
            turn, status=ChatTurnStatus.CANCELLED, error_code="cancelled"
        )
        if self._history is not None:
            try:
                cancelled = await self._history.update_turn(self._scope, cancelled)
            except Exception:
                self._cancelled_turn_ids.discard(turn_id)
                logger.exception("Unable to persist cancelled chat turn")
                raise
        self._turns_by_id[turn_id] = cancelled
        return True

    async def cancel_turn_by_idempotency_key(self, idempotency_key: str) -> bool:
        """Cancel a pending turn before its streamed ``turn_id`` reaches the client."""

        turn = next(
            (
                candidate
                for candidate in self._turns_by_id.values()
                if candidate.idempotency_key == idempotency_key
                and candidate.status is ChatTurnStatus.GENERATING
            ),
            None,
        )
        if turn is None:
            return False
        return await self.cancel_turn(turn.turn_id)

    async def _fail_turn(self, turn: ChatTurn, *, code: str) -> ChatTurn:
        current = self._turns_by_id.get(turn.turn_id, turn)
        failed = self._terminal_turn(current, status=ChatTurnStatus.FAILED, error_code=code)
        self._turns_by_id[turn.turn_id] = failed
        if self._history is not None:
            try:
                failed = await self._history.update_turn(self._scope, failed)
            except Exception:
                logger.exception("Unable to persist failed chat turn")
        self._turns_by_id[turn.turn_id] = failed
        return failed

    def _terminal_turn(
        self,
        turn: ChatTurn,
        *,
        status: ChatTurnStatus,
        error_code: str | None,
    ) -> ChatTurn:
        at = self._clock()
        activities = turn.activities
        for activity in tuple(activities):
            if activity.status is ChatActivityStatus.RUNNING:
                target = (
                    ChatActivityStatus.CANCELLED
                    if status is ChatTurnStatus.CANCELLED
                    else ChatActivityStatus.FAILED
                )
                activities = transition_activity_snapshot(
                    activities, activity.code, target, at=at
                )
            elif activity.status is ChatActivityStatus.PENDING:
                activities = transition_activity_snapshot(
                    activities, activity.code, ChatActivityStatus.SKIPPED, at=at
                )
        return replace(
            turn,
            activities=activities,
            completed_at=at,
            status=status,
            error_code=error_code,
        )

    async def _persist_completed_turn(self, turn: ChatTurn, *, title: str) -> ChatTurn:
        """Write the finished turn, or abort the stream if the store refuses it.

        Both completion paths -- a normal turn and a task turn -- end here, so
        the failure they report to the client cannot drift apart again.
        """

        if self._history is None:
            return turn
        try:
            return await self._history.update_turn(self._scope, turn, title=title)
        except Exception:
            logger.exception("Unable to persist completed chat turn")
            raise TurnAborted(
                self._error(
                    turn_id=turn.turn_id,
                    code="chat_history_unavailable",
                    safe_message="Không thể lưu câu trả lời. Vui lòng thử lại.",
                )
            ) from None

    @observe(name="chat_stream_message")
    async def stream_message(
        self,
        request: ChatMessageRequest,
        *,
        is_cancelled: CancellationCheck = never_cancelled,
    ) -> AsyncIterator[ChatMessageStreamEvent]:
        """Persist the user turn first, then stream and update that durable row."""

        if request.session_id != self._scope.session_id:
            raise ChatScopeMismatch("message session does not match the verified chat scope")

        guard = CancellationGuard(is_cancelled, self._cancelled_turn_ids)
        try:
            async for event in self._stream_turn(request, guard):
                yield event
        except TurnAborted as abort:
            # A durable write refused the turn: report it once, and stop.
            yield abort.event

    async def _stream_turn(
        self, request: ChatMessageRequest, guard: CancellationGuard
    ) -> AsyncIterator[ChatMessageStreamEvent]:
        async with self._turn_lock:
            pending = self._settler.pending_for(request.idempotency_key)
            if pending is not None:
                if pending.request != request:
                    yield self._error(
                        turn_id=self._new_id(),
                        code="idempotency_conflict",
                        safe_message="Khóa idempotency này đã được dùng cho một tin nhắn khác.",
                    )
                    return
                async for event in self._settler.replay(pending, guard):
                    yield event
                return

            cached = self._completed.get(request.idempotency_key)
            if cached is not None:
                cached_request, cached_events = cached
                if cached_request != request:
                    yield self._error(
                        turn_id=self._new_id(),
                        code="idempotency_conflict",
                        safe_message="Khóa idempotency này đã được dùng cho một tin nhắn khác.",
                    )
                    return
                for event in cached_events:
                    if await guard.tripped():
                        return
                    yield event
                return

            turn_id = self._new_id()
            if await guard.tripped():
                return

            temporary_title = _fallback_conversation_title(request.user_message)
            pending_turn = ChatTurn(
                turn_id=turn_id,
                session_id=self._scope.session_id,
                user_message=request.user_message,
                assistant_message=None,
                created_at=self._clock(),
                status=ChatTurnStatus.GENERATING,
                idempotency_key=request.idempotency_key,
                activities=(
                    ChatActivity.pending(ChatActivityCode.UNDERSTANDING_REQUEST).transition(
                        ChatActivityStatus.RUNNING, at=self._clock()
                    ),
                ),
            )
            if self._history is not None:
                try:
                    pending_turn = await self._history.begin_turn(
                        self._scope,
                        pending_turn,
                        idempotency_key=request.idempotency_key,
                        title=temporary_title,
                    )
                except Exception:
                    logger.exception("Unable to persist pending chat turn")
                    yield self._error(
                        turn_id=turn_id,
                        code="chat_history_unavailable",
                        safe_message="Không thể lưu tin nhắn. Vui lòng thử lại.",
                    )
                    return
            turn_id = pending_turn.turn_id
            guard.watch(turn_id)
            replay_completed = pending_turn.status is ChatTurnStatus.COMPLETED
            if pending_turn.status not in {
                ChatTurnStatus.GENERATING,
                ChatTurnStatus.COMPLETED,
            }:
                pending_turn = replace(
                    pending_turn,
                    assistant_message=None,
                    status=ChatTurnStatus.GENERATING,
                    error_code=None,
                    completed_at=None,
                    activities=(
                        ChatActivity.pending(
                            ChatActivityCode.UNDERSTANDING_REQUEST
                        ).transition(ChatActivityStatus.RUNNING, at=self._clock()),
                    ),
                )
                if self._history is not None:
                    pending_turn = await self._history.update_turn(
                        self._scope, pending_turn, title=temporary_title
                    )
            journal = TurnJournal(
                pending_turn,
                scope=self._scope,
                history=self._history,
                clock=self._clock,
                new_id=self._new_id,
                registry=self._turns_by_id,
            )
            started = ChatMessageStreamEvent.started(
                event_id=self._new_id(),
                session_id=self._scope.session_id,
                turn_id=turn_id,
            )
            emitted: list[ChatMessageStreamEvent] = [started]
            yield started

            if await guard.tripped():
                return
            if journal.turn.activities:
                initial_activity = journal.activity_event()
                emitted.append(initial_activity)
                yield initial_activity

            if replay_completed and journal.turn.assistant_message is not None:
                # A durable replay carries its canonical terminal snapshot.
                delta = ChatMessageStreamEvent.delta(
                    event_id=self._new_id(),
                    session_id=self._scope.session_id,
                    turn_id=turn_id,
                    text=journal.turn.assistant_message,
                )
                completed = ChatMessageStreamEvent.completed(
                    event_id=self._new_id(),
                    session_id=self._scope.session_id,
                    turn_id=turn_id,
                    rag_evidence=journal.turn.rag_evidence,
                    retrieval_status=journal.turn.retrieval_status,
                    execution_trace=journal.turn.execution_trace,
                )
                emitted.extend((delta, completed))
                self._completed[request.idempotency_key] = (request, tuple(emitted))
                yield delta
                yield completed
                return

            if await guard.tripped():
                return

            routing_outcome = await self._route_turn(request)
            context_request = self._context_request(request, routing_outcome)
            searches_information = (
                routing_outcome is not None and routing_outcome.route is ChatRoute.RAG
            ) or context_request.reads.semantic.enabled
            final_activity = (
                ChatActivityCode.PREPARING_ACTION_PLAN
                if is_explicit_task_request(request)
                else ChatActivityCode.PREPARING_RESPONSE
            )
            planned = (
                *(
                    (ChatActivityCode.SEARCHING_RELEVANT_INFORMATION,)
                    if searches_information
                    else ()
                ),
                ChatActivityCode.REVIEWING_CONTEXT,
                final_activity,
            )
            activity_event = await journal.record(
                ChatActivityCode.UNDERSTANDING_REQUEST,
                ChatActivityStatus.COMPLETED,
                outcome=ChatActivityOutcome.SUCCESS,
                append=planned,
            )
            emitted.append(activity_event)
            yield activity_event
            response_mode = (
                ChatResponseMode.CLARIFY
                if routing_outcome is not None and routing_outcome.route is ChatRoute.CLARIFY
                else ChatResponseMode.NORMAL
            )
            project_documents: ProjectDocumentResponse | None = None
            activity_event = await journal.record(
                ChatActivityCode.REVIEWING_CONTEXT,
                ChatActivityStatus.RUNNING,
            )
            emitted.append(activity_event)
            yield activity_event
            if searches_information:
                activity_event = await journal.record(
                    ChatActivityCode.SEARCHING_RELEVANT_INFORMATION,
                    ChatActivityStatus.RUNNING,
                )
                emitted.append(activity_event)
                yield activity_event
            if routing_outcome is not None and routing_outcome.route is ChatRoute.RAG:
                project_documents = await self._memory.read_project_documents(
                    query=routing_outcome.retrieval_query or request.user_message,
                    document_ids=request.document_ids,
                )
                if project_documents.degraded:
                    response_mode = ChatResponseMode.EVIDENCE_UNAVAILABLE
                elif not project_documents.evidence:
                    if request.document_ids:
                        response_mode = ChatResponseMode.INSUFFICIENT_EVIDENCE
                    else:
                        response_mode = ChatResponseMode.CLARIFY
            tool_result: str | None = None
            if (
                routing_outcome is not None
                and routing_outcome.route is ChatRoute.TOOL
                and self._tools is not None
            ):
                # One tool, once. A failure degrades the turn into an
                # ordinary reply that says what did not happen; it never
                # fails the turn.
                outcome = await self._tools.run_for_turn(
                    routing_outcome.decision.tool_name or "",
                    user_message=request.user_message,
                    recent_turns=self._memory.read_active_turns(),
                    idempotency_key=request.idempotency_key,
                    now=self._clock(),
                    user_id=self._scope.user_id,
                )
                tool_result = outcome.text
            context = await self._memory.read_context(context_request)
            if searches_information:
                rag_evidence, retrieval_status = _rag_evidence(
                    assemble_generation_context(
                        request,
                        context,
                        response_mode=response_mode,
                        project_documents=project_documents,
                    ),
                    project_documents,
                )
                search_outcome = {
                    "success": ChatActivityOutcome.SUCCESS,
                    "no_results": ChatActivityOutcome.NO_RESULTS,
                    "timeout": ChatActivityOutcome.DEGRADED,
                    "unavailable": ChatActivityOutcome.DEGRADED,
                    None: ChatActivityOutcome.NO_RESULTS,
                }[retrieval_status]
                activity_event = await journal.record(
                    ChatActivityCode.SEARCHING_RELEVANT_INFORMATION,
                    ChatActivityStatus.COMPLETED,
                    outcome=search_outcome,
                    detail=ChatActivityDetail(
                        kind="documents_found", current=len(rag_evidence)
                    ),
                )
                emitted.append(activity_event)
                yield activity_event
            activity_event = await journal.record(
                ChatActivityCode.REVIEWING_CONTEXT,
                ChatActivityStatus.COMPLETED,
                outcome=(
                    ChatActivityOutcome.DEGRADED
                    if context.degraded
                    else ChatActivityOutcome.SUCCESS
                ),
            )
            emitted.append(activity_event)
            yield activity_event
            if context.degraded:
                warning = self._error(
                    turn_id=turn_id,
                    code="optional_memory_degraded",
                    safe_message="Một phần bộ nhớ tùy chọn hiện không khả dụng.",
                )
                emitted.append(warning)
                yield warning
            if project_documents is not None and project_documents.degraded:
                warning = self._error(
                    turn_id=turn_id,
                    code="project_documents_degraded",
                    safe_message="Bằng chứng từ tài liệu dự án tạm thời không khả dụng.",
                )
                emitted.append(warning)
                yield warning

            chunks: list[str] = []
            task_proposal: ChatTaskProposal | None = None
            generated_report: GeneratedReportArtifact | None = None
            conversation_title: str | None = None
            selected_citation_ids: list[str] = []
            trace_provider: str | None = None
            trace_model: str | None = None
            trace_mode: str | None = None
            reasoning_parts: list[str] = []
            pending_task_episode: PendingTaskEpisode | None = None
            generation_context = assemble_generation_context(
                request,
                context,
                response_mode=response_mode,
                project_documents=project_documents,
                tool_result=tool_result,
            )
            activity_event = await journal.record(
                final_activity,
                ChatActivityStatus.RUNNING,
            )
            emitted.append(activity_event)
            yield activity_event
            try:
                async for chunk in self._reply.stream_reply(request, generation_context):
                    if await guard.tripped():
                        return
                    if isinstance(chunk, ChatReplyChunk):
                        if chunk.task_proposal is not None:
                            task_proposal = chunk.task_proposal
                        if chunk.generated_report is not None:
                            generated_report = chunk.generated_report
                        if chunk.conversation_title is not None:
                            conversation_title = chunk.conversation_title
                        trace_provider = chunk.provider or trace_provider
                        trace_model = chunk.model or trace_model
                        trace_mode = chunk.reasoning_mode or trace_mode
                        if chunk.reasoning:
                            reasoning_parts.append(chunk.reasoning)
                        for citation_id in chunk.citation_ids:
                            if citation_id not in selected_citation_ids:
                                selected_citation_ids.append(citation_id)
                        text = chunk.text
                    else:
                        text = chunk
                    if not text:
                        continue
                    chunks.append(text)
                    event = ChatMessageStreamEvent.delta(
                        event_id=self._new_id(),
                        session_id=self._scope.session_id,
                        turn_id=turn_id,
                        text=text,
                    )
                    emitted.append(event)
                    yield event
            except ChatReplyUnavailable as exc:
                # The user-facing message is the same either way; the code is
                # not. A validation failure that reads as an outage sends the
                # next person to the wrong system.
                code = (
                    "chat_response_invalid"
                    if isinstance(exc, ChatResponseInvalid)
                    else "chat_provider_unavailable"
                )
                failed = await self._fail_turn(journal.turn, code=code)
                yield journal.activity_event(failed)
                yield self._error(
                    turn_id=turn_id,
                    code=code,
                    safe_message="Dịch vụ sinh câu trả lời hiện không khả dụng.",
                )
                return

            if await guard.tripped():
                return
            assistant_message = "".join(chunks)
            if not assistant_message:
                failed = await self._fail_turn(journal.turn, code="empty_chat_response")
                yield journal.activity_event(failed)
                yield self._error(
                    turn_id=turn_id,
                    code="empty_chat_response",
                    safe_message="Câu trả lời trả về rỗng.",
                )
                return

            generated_artifact_refs: tuple[Mapping[str, object], ...] = ()
            if (
                generated_report is None
                and _is_report_request(request.user_message)
                and len(assistant_message.strip()) > 50
            ):
                generated_report = GeneratedReportArtifact(
                    filename=_fallback_report_filename(
                        request.user_message, conversation_title
                    ),
                    title=conversation_title or "Báo cáo tổng hợp",
                    content=assistant_message,
                )

            if generated_report is not None and self._reports is not None:
                # ``sanitize``, not ``parse``: the provider names this file, so an
                # unusable name has to degrade to a safe slug rather than drop a
                # report the user asked for. Either way the name that reaches the
                # store cannot address anything outside the report folder.
                filename = ReportFilename.sanitize(generated_report.filename)
                try:
                    stored = await self._reports.save(
                        ReportArtifact(
                            filename=filename,
                            content=generated_report.content,
                            title=generated_report.title,
                        )
                    )
                except (OSError, ValueError) as save_err:
                    logger.warning("Failed to save generated report artifact: %s", save_err)
                else:
                    generated_artifact_refs = (
                        {
                            "ref_id": stored.filename.value,
                            "checksum": "",
                            "provenance": {
                                "upload_filename": stored.filename.value,
                                "title": generated_report.title,
                            },
                        },
                    )

            rag_evidence, retrieval_status = _rag_evidence(
                generation_context, project_documents
            )
            reasoning = "\n".join(reasoning_parts).strip() or None
            reasoning_truncated = bool(
                reasoning and len(reasoning) > MAX_EXECUTION_REASONING_LENGTH
            )
            if reasoning_truncated:
                assert reasoning is not None
                reasoning = reasoning[:MAX_EXECUTION_REASONING_LENGTH]
            execution_trace = (
                ChatExecutionTrace(
                    provider=trace_provider,
                    model=trace_model,
                    mode=cast(Literal["fast", "reasoning"], trace_mode),
                    reasoning=reasoning,
                    reasoning_truncated=reasoning_truncated,
                    retrieved_filenames=tuple(
                        dict.fromkeys(item.document_title for item in rag_evidence)
                    ),
                )
                if trace_provider is not None and trace_model is not None and trace_mode is not None
                else None
            )
            task_requested = (
                response_mode is ChatResponseMode.NORMAL
                and is_explicit_task_request(request)
            )
            if not task_requested:
                activity_event = await journal.record(
                    final_activity,
                    ChatActivityStatus.COMPLETED,
                    outcome=ChatActivityOutcome.SUCCESS,
                )
                emitted.append(activity_event)
                yield activity_event
            turn = replace(
                    journal.turn,
                    assistant_message=assistant_message,
                    status=(
                        ChatTurnStatus.GENERATING
                        if task_requested
                        else ChatTurnStatus.COMPLETED
                    ),
                    error_code=None,
                    citation_coordinates=tuple(
                        {
                            "citation_scope": "project_document",
                            "project_id": item.project_id,
                            "document_id": item.document_id,
                            "document_title": item.title,
                            "section": item.section,
                            "page_start": item.page_start,
                            "page_end": item.page_end,
                        }
                        for item in (
                            project_documents.evidence if project_documents is not None else ()
                        )
                        if item.citation_id in selected_citation_ids
                    ),
                    rag_evidence=rag_evidence,
                    retrieval_status=retrieval_status,
                    execution_trace=execution_trace,
                    artifact_refs=generated_artifact_refs,
                    completed_at=None if task_requested else self._clock(),
            )
            turn = await self._persist_completed_turn(
                turn,
                title=(
                    conversation_title or _fallback_conversation_title(request.user_message)
                ),
            )
            if not task_requested:
                self._memory.append_turn(turn)
            journal.adopt(turn)
            try:
                from langfuse import get_client

                get_client().update_current_span(
                    output={
                        "assistant_message": assistant_message,
                        "status": "completed",
                        "turn_id": turn_id,
                    }
                )
            except Exception:
                pass
            if project_documents is not None:
                evidence_by_id = {item.citation_id: item for item in project_documents.evidence}
                for citation_id in selected_citation_ids:
                    evidence = evidence_by_id.get(citation_id)
                    if evidence is None:
                        continue
                    citation = ChatMessageStreamEvent.memory_citation(
                        event_id=self._new_id(),
                        session_id=self._scope.session_id,
                        turn_id=turn_id,
                        memory_type=MemoryCitationType.SEMANTIC,
                        source_id=evidence.citation_id,
                        citation_scope="project_document",
                        project_id=evidence.project_id,
                        document_id=evidence.document_id,
                        document_title=evidence.title,
                        section=evidence.section,
                        page_start=evidence.page_start,
                        page_end=evidence.page_end,
                    )
                    emitted.append(citation)
                    yield citation
            if task_requested:
                settlement = await self._settler.settle(
                    request=request,
                    turn_id=turn_id,
                    proposal=task_proposal,
                    replay_prefix=tuple(emitted),
                )
                pending_task_episode = settlement.pending
                for event in settlement.events:
                    emitted.append(event)
                    yield event
                activity_event = await journal.record(
                    final_activity,
                    ChatActivityStatus.COMPLETED,
                    outcome=(
                        ChatActivityOutcome.DEGRADED
                        if settlement.degraded
                        else ChatActivityOutcome.SUCCESS
                    ),
                )
                turn = replace(
                    journal.turn,
                    status=ChatTurnStatus.COMPLETED,
                    completed_at=self._clock(),
                )
                turn = await self._persist_completed_turn(
                    turn,
                    title=(
                        conversation_title
                        or _fallback_conversation_title(request.user_message)
                    ),
                )
                journal.adopt(turn)
                self._memory.append_turn(turn)
                emitted.append(activity_event)
                yield activity_event
                if pending_task_episode is not None:
                    pending_task_episode = replace(
                        pending_task_episode,
                        replay_prefix=(*pending_task_episode.replay_prefix, activity_event),
                    )
            completed = ChatMessageStreamEvent.completed(
                event_id=self._new_id(),
                session_id=self._scope.session_id,
                turn_id=turn_id,
                rag_evidence=rag_evidence,
                retrieval_status=retrieval_status,
                execution_trace=turn.execution_trace,
                artifact_refs=turn.artifact_refs,
            )
            emitted.append(completed)
            completed_stream = tuple(emitted)
            self._completed[request.idempotency_key] = (request, completed_stream)
            if pending_task_episode is not None:
                self._settler.remember_pending(request.idempotency_key, pending_task_episode)
            yield completed

    async def approve_task_episode(self, episode_id: str) -> TaskEpisode | None:
        return await self._transition_task_episode(episode_id, ValidationStatus.USER_APPROVED)

    async def complete_task_episode(self, episode_id: str) -> TaskEpisode | None:
        return await self._transition_task_episode(episode_id, ValidationStatus.COMPLETED)

    async def reject_task_episode(self, episode_id: str) -> TaskEpisode | None:
        return await self._transition_task_episode(episode_id, ValidationStatus.REJECTED)

    async def delete_task_episode(self, episode_id: str) -> bool:
        episode = await self._task_episode_for_id(episode_id)
        if episode is None:
            return False
        deleted = await self._memory.delete_task_episode(
            record_id=episode.record_id,
            chat_turn_id=episode.chat_turn_id,
            episode_id=episode.episode_id,
        )
        if deleted:
            del self._task_episodes[episode_id]
        return deleted

    async def _route_turn(self, request: ChatMessageRequest) -> RoutingOutcome | None:
        if self._routing is None:
            return None
        cached = self._routing_outcomes.get(request.idempotency_key)
        if cached is not None:
            return cached
        outcome = await self._routing.route(
            scope=self._scope,
            request=request,
            recent_turns=self._memory.read_active_turns(),
        )
        self._routing_outcomes[request.idempotency_key] = outcome
        return outcome

    def _context_request(
        self,
        request: ChatMessageRequest,
        routing_outcome: RoutingOutcome | None = None,
    ) -> MemoryContextRequest:
        reads = (
            clarification_memory_reads()
            if routing_outcome is not None and routing_outcome.route is ChatRoute.CLARIFY
            else select_memory_reads(request, company_rag_enabled=self._company_rag_enabled)
        )
        return MemoryContextRequest(
            session_id=self._scope.session_id,
            scope=self._scope,
            reads=reads,
        )

    async def _transition_task_episode(
        self, episode_id: str, to_status: ValidationStatus
    ) -> TaskEpisode | None:
        episode = await self._task_episode_for_id(episode_id)
        if episode is None:
            return None
        transitioned = await self._memory.transition_task_episode(
            record_id=episode.record_id,
            chat_turn_id=episode.chat_turn_id,
            episode_id=episode.episode_id,
            from_status=episode.validation_status,
            to_status=to_status,
            transitioned_at=self._clock(),
        )
        if transitioned is not None:
            self._task_episodes[episode_id] = transitioned
        return transitioned

    async def _task_episode_for_id(self, episode_id: str) -> TaskEpisode | None:
        episode = self._task_episodes.get(episode_id)
        if episode is not None:
            return episode
        episode = await self._memory.read_task_episode(episode_id)
        if episode is not None:
            self._task_episodes[episode_id] = episode
        return episode

    def _error(
        self,
        *,
        turn_id: str,
        code: str,
        safe_message: str,
    ) -> ChatMessageStreamEvent:
        return ChatMessageStreamEvent.error(
            event_id=self._new_id(),
            session_id=self._scope.session_id,
            turn_id=turn_id,
            code=code,
            safe_message=safe_message,
        )


def _is_report_request(message: str) -> bool:
    lowered = message.lower()
    return any(
        kw in lowered
        for kw in (
            "tạo báo cáo",
            "lập báo cáo",
            "xuất báo cáo",
            "tổng hợp báo cáo",
            "viết báo cáo",
            "tạo artifact",
            "generate report",
            "create report",
        )
    )


def _fallback_report_filename(message: str, title: str | None) -> str:
    """Name a report the provider produced without naming.

    The slug rule lives in ``ReportFilename.sanitize``; this only adds the
    ``bao-cao-`` prefix the artifacts view sorts on, and keeps the stem short
    enough to stay readable in the file list.
    """
    stem = ReportFilename.sanitize(title or message).value.removesuffix(".md")[:40].strip("-")
    if not stem:
        stem = DEFAULT_REPORT_STEM
    if not stem.startswith("bao-cao"):
        stem = f"bao-cao-{stem}"
    return f"{stem}.md"

