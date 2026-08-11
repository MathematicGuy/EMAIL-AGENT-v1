"""Framework-free V2-M4A Chat Controller and in-memory session ownership."""

from __future__ import annotations

import asyncio
import hashlib
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatMessageRequest,
    ChatMessageStreamEvent,
    ChatTurn,
    EpisodeSourceType,
    MemoryCitationType,
    MemoryContextRequest,
    TaskEpisode,
)
from cowork_agent.domain.target_contracts import ValidationStatus

from .generation_context import GenerationContext, assemble_generation_context
from .memory_gateway import MemoryGateway, MemorySourceUnavailableError
from .ports import ChatReplyChunk, ChatReplyPort, ChatTaskProposal
from .retrieval_policy import is_explicit_task_request, select_memory_reads

IdFactory = Callable[[], str]
Clock = Callable[[], datetime]
CancellationCheck = Callable[[], Awaitable[bool]]


def _new_id() -> str:
    return str(uuid4())


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def _never_cancelled() -> bool:
    return False


class ChatScopeMismatch(ValueError):
    """The message does not belong to the controller's verified session."""


class ChatSessionAccessDenied(LookupError):
    """The session is absent or belongs to another verified principal."""


class ChatReplyUnavailable(RuntimeError):
    """The configured chat-response provider cannot serve this turn."""


@dataclass(frozen=True, slots=True)
class _PendingTaskEpisode:
    request: ChatMessageRequest
    episode: TaskEpisode
    replay_prefix: tuple[ChatMessageStreamEvent, ...]


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

    def create(self, *, tenant_id: str, user_id: str) -> ChatMemoryScope:
        with self._lock:
            session_id = self._new_id()
            while session_id in self._sessions:
                session_id = self._new_id()
            scope = ChatMemoryScope(
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
            )
            self._sessions[session_id] = scope
            return scope

    def require(
        self,
        session_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> ChatMemoryScope:
        with self._lock:
            scope = self._sessions.get(session_id)
        if scope is None or scope.tenant_id != tenant_id or scope.user_id != user_id:
            raise ChatSessionAccessDenied(session_id)
        return scope


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
    ) -> None:
        self._scope = scope
        self._memory = memory
        self._reply = reply
        self._new_id = new_id
        self._clock = clock
        self._completed: dict[
            str, tuple[ChatMessageRequest, tuple[ChatMessageStreamEvent, ...]]
        ] = {}
        self._pending_task_episodes: dict[str, _PendingTaskEpisode] = {}
        self._task_episodes: dict[str, TaskEpisode] = {}
        self._turn_lock = asyncio.Lock()

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

            context = await self._memory.read_context(self._context_request(request))
            emitted: list[ChatMessageStreamEvent] = []
            if context.degraded:
                warning = self._error(
                    turn_id=turn_id,
                    code="optional_memory_degraded",
                    safe_message="Some optional memory was unavailable.",
                )
                emitted.append(warning)
                yield warning

            chunks: list[str] = []
            task_proposal: ChatTaskProposal | None = None
            pending_task_episode: _PendingTaskEpisode | None = None
            generation_context = assemble_generation_context(request, context)
            try:
                async for chunk in self._reply.stream_reply(request, generation_context):
                    if await is_cancelled():
                        return
                    if isinstance(chunk, ChatReplyChunk):
                        if chunk.task_proposal is not None:
                            task_proposal = chunk.task_proposal
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

            self._memory.append_turn(
                ChatTurn(
                    turn_id=turn_id,
                    session_id=self._scope.session_id,
                    user_message=request.user_message,
                    assistant_message=assistant_message,
                    created_at=self._clock(),
                )
            )
            if is_explicit_task_request(request):
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
                    try:
                        episode = await self._memory.write_task_episode(
                            episode, expires_at=None
                        )
                    except MemorySourceUnavailableError:
                        pending_task_episode = _PendingTaskEpisode(
                            request=request,
                            episode=episode,
                            replay_prefix=tuple(emitted),
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
            completed = ChatMessageStreamEvent.completed(
                event_id=self._new_id(),
                session_id=self._scope.session_id,
                turn_id=turn_id,
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
            episode = await self._memory.write_task_episode(pending.episode, expires_at=None)
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
        completed = ChatMessageStreamEvent.completed(
            event_id=self._new_id(),
            session_id=self._scope.session_id,
            turn_id=episode.chat_turn_id,
        )
        replay = (*pending.replay_prefix, citation, completed)
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
        episode = self._task_episodes.get(episode_id)
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

    def _context_request(self, request: ChatMessageRequest) -> MemoryContextRequest:
        return MemoryContextRequest(
            session_id=self._scope.session_id,
            scope=self._scope,
            reads=select_memory_reads(request),
        )

    def _new_task_episode(
        self, turn_id: str, proposal: ChatTaskProposal
    ) -> TaskEpisode:
        """Build a body-free task record from trusted scope and turn metadata only."""

        created_at = self._clock()
        record_input = "\x1f".join(
            (self._scope.tenant_id, self._scope.user_id, self._scope.session_id, turn_id)
        )
        return TaskEpisode(
            episode_id=self._new_id(),
            record_id=hashlib.sha256(record_input.encode("utf-8")).hexdigest(),
            tenant_id=self._scope.tenant_id,
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
        )

    async def _transition_task_episode(
        self, episode_id: str, to_status: ValidationStatus
    ) -> TaskEpisode | None:
        episode = self._task_episodes.get(episode_id)
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
