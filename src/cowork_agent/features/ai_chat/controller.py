"""Framework-free V2-M4A Chat Controller and in-memory session ownership."""

from __future__ import annotations

import asyncio
import hashlib
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol, cast
from uuid import uuid4

from langfuse import observe

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatMessageRequest,
    ChatMessageStreamEvent,
    ChatRagEvidence,
    ChatRoute,
    ChatTurn,
    EpisodeSourceType,
    MemoryCitationType,
    MemoryContextRequest,
    RoutingOutcome,
    TaskEpisode,
)
from cowork_agent.domain.project_documents import ProjectDocumentEvidence, ProjectDocumentResponse
from cowork_agent.domain.target_contracts import ValidationStatus

from .generation_context import (
    ChatResponseMode,
    GenerationContext,
    assemble_generation_context,
)
from .intent.service import ChatRoutingService
from .memory_gateway import MemoryGateway, MemorySourceUnavailableError
from .ports import (
    ArtifactRef,
    ArtifactStoragePort,
    ChatReplyChunk,
    ChatReplyPort,
    ChatTaskProposal,
    extract_stem_from_content,
)
from .retention import compute_expires_at
from .retrieval_policy import (
    clarification_memory_reads,
    is_explicit_task_request,
    select_memory_reads,
)

IdFactory = Callable[[], str]
Clock = Callable[[], datetime]
CancellationCheck = Callable[[], Awaitable[bool]]


def _new_id() -> str:
    return str(uuid4())


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def _never_cancelled() -> bool:
    return False


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
                for item in project_documents.evidence[:5]
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
        self, session_id: str, *, user_id: str, tenant_id: str = "local"
    ) -> ChatMemoryScope: ...

    async def list_for(
        self,
        *,
        user_id: str,
        tenant_id: str = "local",
        project_id: str | None = None,
    ) -> tuple[ChatMemoryScope, ...]: ...

    async def delete_project(
        self, *, user_id: str, project_id: str, tenant_id: str = "local"
    ) -> tuple[str, ...]: ...


class ChatReplyUnavailable(RuntimeError):
    """The configured chat-response provider cannot serve this turn."""


@dataclass(frozen=True, slots=True)
class _PendingTaskEpisode:
    request: ChatMessageRequest
    episode: TaskEpisode
    replay_prefix: tuple[ChatMessageStreamEvent, ...]
    expires_at: datetime | None = None


def _proposal_payload(episode: TaskEpisode) -> dict[str, object]:
    """Frontend-safe structured proposal for the task_proposal SSE event."""
    return {
        "episode_id": episode.episode_id,
        "task_title": episode.task_title,
        "minimal_request_paraphrase": episode.minimal_request_paraphrase,
        "action_plan": list(episode.action_plan),
        "missing_information": list(episode.missing_information),
        "rag_citations": [citation.to_dict() for citation in episode.rag_citations],
        "validation_status": episode.validation_status.value,
        "retrieval_eligible": episode.retrieval_eligible,
    }


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
    ) -> ChatMemoryScope:
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
        storage: ArtifactStoragePort | None = None,
    ) -> None:
        self._scope = scope
        self._memory = memory
        self._reply = reply
        self._new_id = new_id
        self._clock = clock
        self._episode_retention_seconds = episode_retention_seconds
        self._routing = routing
        self._company_rag_enabled = company_rag_enabled
        self._storage = storage

        self._completed: dict[
            str, tuple[ChatMessageRequest, tuple[ChatMessageStreamEvent, ...]]
        ] = {}
        self._pending_task_episodes: dict[str, _PendingTaskEpisode] = {}
        self._task_episodes: dict[str, TaskEpisode] = {}
        self._routing_outcomes: dict[str, RoutingOutcome] = {}
        self._turn_lock = asyncio.Lock()

    @observe(name="chat_stream_message")
    async def stream_message(
        self,
        request: ChatMessageRequest,
        *,
        is_cancelled: CancellationCheck = _never_cancelled,
    ) -> AsyncIterator[ChatMessageStreamEvent]:
        """Stream typed events; append a turn only after a complete reply."""

        if request.session_id != self._scope.session_id:
            raise ChatScopeMismatch("message session does not match the verified chat scope")

        async with self._turn_lock:
            pending = self._pending_task_episodes.get(request.idempotency_key)
            if pending is not None:
                if pending.request != request:
                    yield self._error(
                        turn_id=self._new_id(),
                        code="idempotency_conflict",
                        safe_message="The idempotency key was already used for another message.",
                    )
                    return
                async for event in self._retry_pending_task_episode(pending, is_cancelled):
                    yield event
                return

            cached = self._completed.get(request.idempotency_key)
            if cached is not None:
                cached_request, cached_events = cached
                if cached_request != request:
                    yield self._error(
                        turn_id=self._new_id(),
                        code="idempotency_conflict",
                        safe_message="The idempotency key was already used for another message.",
                    )
                    return
                for event in cached_events:
                    if await is_cancelled():
                        return
                    yield event
                return

            turn_id = self._new_id()
            if await is_cancelled():
                return

            routing_outcome = await self._route_turn(request)
            response_mode = (
                ChatResponseMode.CLARIFY
                if routing_outcome is not None and routing_outcome.route is ChatRoute.CLARIFY
                else ChatResponseMode.NORMAL
            )
            project_documents: ProjectDocumentResponse | None = None
            if routing_outcome is not None and routing_outcome.route is ChatRoute.RAG:
                project_documents = await self._memory._read_project_documents(
                    query=routing_outcome.retrieval_query or request.user_message,
                    document_ids=request.document_ids,
                )
                if project_documents.degraded:
                    response_mode = ChatResponseMode.EVIDENCE_UNAVAILABLE
                elif not project_documents.evidence:
                    response_mode = ChatResponseMode.INSUFFICIENT_EVIDENCE
            context = await self._memory.read_context(
                self._context_request(request, routing_outcome)
            )
            emitted: list[ChatMessageStreamEvent] = []
            if context.degraded:
                warning = self._error(
                    turn_id=turn_id,
                    code="optional_memory_degraded",
                    safe_message="Some optional memory was unavailable.",
                )
                emitted.append(warning)
                yield warning
            if project_documents is not None and project_documents.degraded:
                warning = self._error(
                    turn_id=turn_id,
                    code="project_documents_degraded",
                    safe_message="Project document evidence is temporarily unavailable.",
                )
                emitted.append(warning)
                yield warning

            chunks: list[str] = []
            task_proposal: ChatTaskProposal | None = None
            selected_citation_ids: list[str] = []
            collected_artifact_refs: list[object] = []
            pending_task_episode: _PendingTaskEpisode | None = None
            generation_context = assemble_generation_context(
                request,
                context,
                response_mode=response_mode,
                project_documents=project_documents,
            )
            try:
                async for chunk in self._reply.stream_reply(request, generation_context):
                    if await is_cancelled():
                        return
                    if isinstance(chunk, ChatReplyChunk):
                        if chunk.task_proposal is not None:
                            task_proposal = chunk.task_proposal
                        for citation_id in chunk.citation_ids:
                            if citation_id not in selected_citation_ids:
                                selected_citation_ids.append(citation_id)
                        if chunk.artifact_refs:
                            collected_artifact_refs.extend(chunk.artifact_refs)
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
            except ChatReplyUnavailable:
                yield self._error(
                    turn_id=turn_id,
                    code="chat_provider_unavailable",
                    safe_message="The chat response provider is unavailable.",
                )
                return

            if collected_artifact_refs:
                storage = self._storage
                if storage is None:
                    error_evt = self._error(
                        turn_id=turn_id,
                        code="storage_unavailable",
                        safe_message=(
                            "Private storage is unavailable. Artifact could not be saved."
                        ),
                    )
                    emitted.append(error_evt)
                    yield error_evt
                    return

                saved_refs: list[dict[str, object]] = []
                for art_ref in collected_artifact_refs:
                    raw_filename = (
                        art_ref.filename if isinstance(art_ref, ArtifactRef) else "report.md"
                    )
                    content_str = (
                        art_ref.content if isinstance(art_ref, ArtifactRef) else ""
                    )
                    stem = extract_stem_from_content(content_str, raw_filename)
                    ref_id = f"{stem}.md"
                    object_key = f"reports/{self._scope.user_id}/{ref_id}"

                    try:
                        await storage.upload_bytes(
                            object_key,
                            content_str.encode("utf-8"),
                            content_type="text/markdown",
                        )
                    except Exception:
                        error_evt = self._error(
                            turn_id=turn_id,
                            code="storage_unavailable",
                            safe_message=(
                                "Private storage is unavailable. Artifact could not be saved."
                            ),
                        )
                        emitted.append(error_evt)
                        yield error_evt
                        return

                    saved_refs.append(
                        {
                            "ref_id": ref_id,
                            "filename": ref_id,
                            "title": stem,
                        }
                    )

                art_event = ChatMessageStreamEvent.artifact_refs_event(
                    event_id=self._new_id(),
                    session_id=self._scope.session_id,
                    turn_id=turn_id,
                    artifact_refs=tuple(saved_refs),
                )
                emitted.append(art_event)
                yield art_event


            if await is_cancelled():
                return
            assistant_message = "".join(chunks)
            if not assistant_message:
                yield self._error(
                    turn_id=turn_id,
                    code="empty_chat_response",
                    safe_message="The chat response was empty.",
                )
                return

            rag_evidence, retrieval_status = _rag_evidence(
                generation_context, project_documents
            )
            self._memory.append_turn(
                ChatTurn(
                    turn_id=turn_id,
                    session_id=self._scope.session_id,
                    user_message=request.user_message,
                    assistant_message=assistant_message,
                    created_at=self._clock(),
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
                )
            )
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
            if response_mode is ChatResponseMode.NORMAL and is_explicit_task_request(request):
                if task_proposal is None:
                    warning = self._error(
                        turn_id=turn_id,
                        code="task_episode_unavailable",
                        safe_message="The task proposal could not be saved.",
                    )
                    emitted.append(warning)
                    yield warning
                else:
                    episode = self._new_task_episode(turn_id, task_proposal)
                    expires_at = compute_expires_at(self._clock(), self._episode_retention_seconds)
                    try:
                        episode = await self._memory.write_task_episode(
                            episode, expires_at=expires_at
                        )
                    except MemorySourceUnavailableError:
                        pending_task_episode = _PendingTaskEpisode(
                            request=request,
                            episode=episode,
                            replay_prefix=tuple(emitted),
                            expires_at=expires_at,
                        )
                        warning = self._error(
                            turn_id=turn_id,
                            code="task_episode_unavailable",
                            safe_message="The task proposal could not be saved.",
                        )
                        emitted.append(warning)
                        yield warning
                    except ValueError:
                        warning = self._error(
                            turn_id=turn_id,
                            code="task_episode_unavailable",
                            safe_message="The task proposal could not be saved.",
                        )
                        emitted.append(warning)
                        yield warning
                    else:
                        self._task_episodes[episode.episode_id] = episode
                        citation = ChatMessageStreamEvent.memory_citation(
                            event_id=self._new_id(),
                            session_id=self._scope.session_id,
                            turn_id=turn_id,
                            memory_type=MemoryCitationType.EPISODIC,
                            source_id=episode.episode_id,
                        )
                        emitted.append(citation)
                        yield citation
                        proposal_event = ChatMessageStreamEvent.task_proposal(
                            event_id=self._new_id(),
                            session_id=self._scope.session_id,
                            turn_id=turn_id,
                            proposal=_proposal_payload(episode),
                        )
                        emitted.append(proposal_event)
                        yield proposal_event
            completed = ChatMessageStreamEvent.completed(
                event_id=self._new_id(),
                session_id=self._scope.session_id,
                turn_id=turn_id,
                rag_evidence=rag_evidence,
                retrieval_status=retrieval_status,
            )
            emitted.append(completed)
            completed_stream = tuple(emitted)
            self._completed[request.idempotency_key] = (request, completed_stream)
            if pending_task_episode is not None:
                self._pending_task_episodes[request.idempotency_key] = pending_task_episode
            yield completed

    async def _retry_pending_task_episode(
        self,
        pending: _PendingTaskEpisode,
        is_cancelled: CancellationCheck,
    ) -> AsyncIterator[ChatMessageStreamEvent]:
        if await is_cancelled():
            return
        try:
            episode = await self._memory.write_task_episode(
                pending.episode, expires_at=pending.expires_at
            )
        except MemorySourceUnavailableError:
            _, cached_events = self._completed[pending.request.idempotency_key]
            for event in cached_events:
                if await is_cancelled():
                    return
                yield event
            return
        except ValueError:
            del self._pending_task_episodes[pending.request.idempotency_key]
            _, cached_events = self._completed[pending.request.idempotency_key]
            for event in cached_events:
                if await is_cancelled():
                    return
                yield event
            return

        self._task_episodes[episode.episode_id] = episode
        citation = ChatMessageStreamEvent.memory_citation(
            event_id=self._new_id(),
            session_id=self._scope.session_id,
            turn_id=episode.chat_turn_id,
            memory_type=MemoryCitationType.EPISODIC,
            source_id=episode.episode_id,
        )
        proposal_event = ChatMessageStreamEvent.task_proposal(
            event_id=self._new_id(),
            session_id=self._scope.session_id,
            turn_id=episode.chat_turn_id,
            proposal=_proposal_payload(episode),
        )
        completed = ChatMessageStreamEvent.completed(
            event_id=self._new_id(),
            session_id=self._scope.session_id,
            turn_id=episode.chat_turn_id,
        )
        replay = (*pending.replay_prefix, citation, proposal_event, completed)
        self._completed[pending.request.idempotency_key] = (pending.request, replay)
        del self._pending_task_episodes[pending.request.idempotency_key]
        for event in replay:
            if await is_cancelled():
                return
            yield event

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
            recent_turns=self._memory._read_active_turns(),
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

    def _new_task_episode(self, turn_id: str, proposal: ChatTaskProposal) -> TaskEpisode:
        """Build a body-free task record from trusted scope and turn metadata only."""

        created_at = self._clock()
        record_input = "\x1f".join(
            (self._scope.user_id, self._scope.session_id, turn_id)
        )
        return TaskEpisode(
            episode_id=self._new_id(),
            record_id=hashlib.sha256(record_input.encode("utf-8")).hexdigest(),
            user_id=self._scope.user_id,
            chat_session_id=self._scope.session_id,
            chat_turn_id=turn_id,
            creation_reason="explicit_user_task_request",
            task_title=proposal.task_title,
            minimal_request_paraphrase=proposal.minimal_request_paraphrase,
            action_plan=proposal.action_plan,
            rag_citations=proposal.rag_citations,
            missing_information=proposal.missing_information,
            validation_status=ValidationStatus.SYSTEM_GENERATED,
            retrieval_eligible=False,
            source_type=EpisodeSourceType.SYSTEM_GENERATED_CHAT_TASK,
            created_at=created_at,
            updated_at=created_at,
            pipeline_version="v2-m4",
            model_id=proposal.model_id,
            prompt_version=proposal.prompt_version,
            confidence=proposal.confidence,
            project_id=self._scope.project_id,
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
        episode = await self._memory._read_task_episode(episode_id)
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
