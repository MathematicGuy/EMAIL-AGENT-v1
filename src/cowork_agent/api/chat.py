"""FastAPI adapter for V2-M4A chat sessions and typed SSE events."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatMessageRequest,
    ChatMessageStreamEvent,
)
from cowork_agent.features.ai_chat.controller import (
    ChatController,
    ChatSessionAccessDenied,
    InMemoryChatSessionRegistry,
)
from cowork_agent.identity import VerifiedPrincipal

PrincipalResolver = Callable[[Request], Awaitable[VerifiedPrincipal]]
ControllerFactory = Callable[[ChatMemoryScope], ChatController]


def create_chat_router() -> APIRouter:
    """Create the transport-only router; runtime dependencies live on app.state."""

    router = APIRouter(prefix="/v1/cowork/chat", tags=["chat"])

    @router.post("/sessions", status_code=201)
    async def create_session(request: Request) -> dict[str, str]:
        principal = await _verified_principal(request)
        sessions = _sessions(request)
        scope = sessions.create(
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
        )
        controller = _controller_factory(request)(scope)
        _controllers(request)[scope.session_id] = controller
        return {"session_id": scope.session_id, "feature": scope.feature}

    @router.post("/sessions/{session_id}/messages")
    async def create_message(
        session_id: str,
        payload: ChatMessageRequest,
        request: Request,
    ) -> StreamingResponse:
        if payload.session_id != session_id:
            raise HTTPException(
                status_code=422,
                detail="Payload session_id must match the path session_id",
            )
        principal = await _verified_principal(request)
        try:
            _sessions(request).require(
                session_id,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
            )
        except ChatSessionAccessDenied as exc:
            raise HTTPException(status_code=404, detail="Chat session not found") from exc
        controller = _controllers(request).get(session_id)
        if controller is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
        return StreamingResponse(
            _sse_events(controller, payload, request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return router


async def _sse_events(
    controller: ChatController,
    payload: ChatMessageRequest,
    request: Request,
) -> AsyncIterator[str]:
    async for event in controller.stream_message(
        payload,
        is_cancelled=request.is_disconnected,
    ):
        yield _serialize_sse(event)


def _serialize_sse(event: ChatMessageStreamEvent) -> str:
    data = json.dumps(
        event.to_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"id: {event.event_id}\nevent: {event.event_type.value}\ndata: {data}\n\n"


async def _verified_principal(request: Request) -> VerifiedPrincipal:
    resolver = cast(
        PrincipalResolver | None,
        getattr(request.app.state, "chat_principal_resolver", None),
    )
    if resolver is None:
        raise HTTPException(status_code=503, detail="Chat identity is unavailable")
    principal = await resolver(request)
    if not isinstance(principal, VerifiedPrincipal):
        raise HTTPException(status_code=503, detail="Chat identity is unavailable")
    return principal


def _sessions(request: Request) -> InMemoryChatSessionRegistry:
    return cast(InMemoryChatSessionRegistry, request.app.state.chat_sessions)


def _controllers(request: Request) -> dict[str, ChatController]:
    return cast(dict[str, ChatController], request.app.state.chat_controllers)


def _controller_factory(request: Request) -> ControllerFactory:
    return cast(ControllerFactory, request.app.state.chat_controller_factory)


__all__ = ["create_chat_router"]
