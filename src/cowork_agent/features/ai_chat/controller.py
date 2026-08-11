"""Framework-free V2-M4A Chat Controller and in-memory session ownership."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatMessageRequest,
    ChatMessageStreamEvent,
    ChatTurn,
    MemoryContextRequest,
    MemoryContextResponse,
)

from .memory_gateway import MemoryGateway
from .ports import ChatReplyPort
from .retrieval_policy import select_memory_reads

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


class UnavailableChatReply:
    """Fail-closed runtime default until a chat-capable LLM adapter is configured."""

    async def stream_reply(
        self,
        request: ChatMessageRequest,
        context: MemoryContextResponse,
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
            if request.tool_choices:
                yield self._error(
                    turn_id=turn_id,
                    code="tool_not_available",
                    safe_message="Tools are not available in this chat version.",
                )
                return
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
            try:
                async for chunk in self._reply.stream_reply(request, context):
                    if await is_cancelled():
                        return
                    if not chunk:
                        continue
                    chunks.append(chunk)
                    event = ChatMessageStreamEvent.delta(
                        event_id=self._new_id(),
                        session_id=self._scope.session_id,
                        turn_id=turn_id,
                        text=chunk,
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
            completed = ChatMessageStreamEvent.completed(
                event_id=self._new_id(),
                session_id=self._scope.session_id,
                turn_id=turn_id,
            )
            emitted.append(completed)
            completed_stream = tuple(emitted)
            self._completed[request.idempotency_key] = (request, completed_stream)
            yield completed

    def _context_request(self, request: ChatMessageRequest) -> MemoryContextRequest:
        return MemoryContextRequest(
            session_id=self._scope.session_id,
            scope=self._scope,
            reads=select_memory_reads(request),
        )

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
