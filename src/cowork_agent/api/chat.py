"""FastAPI adapter for V2-M4A chat sessions and typed SSE events."""

import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from langfuse import observe
from pydantic import BaseModel, ConfigDict

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatMessageRequest,
    ChatMessageStreamEvent,
    DeclarativeProfile,
    MemoryNamespace,
    MemoryProvenance,
    MemoryProvenanceSource,
    MemoryType,
    TaskEpisode,
)
from cowork_agent.features.ai_chat.controller import (
    ChatController,
    ChatSessionAccessDenied,
    ChatSessionRegistryPort,
)
from cowork_agent.features.ai_chat.memory_gateway import MemorySourceUnavailableError
from cowork_agent.features.ai_chat.ports import (
    ChatHistoryPort,
    ChatSessionBufferPort,
    DeclarativeMemoryPort,
    EpisodicMemoryPort,
)
from cowork_agent.features.ai_chat.profile_policy import (
    ProfileWriteRejected,
    authorize_profile_write,
)
from cowork_agent.identity import VerifiedPrincipal
from cowork_agent.persistence.repositories.projects import Project, ProjectDocument

PrincipalResolver = Callable[[Request], Awaitable[VerifiedPrincipal]]
ControllerFactory = Callable[[ChatMemoryScope], ChatController]


class _ChatMessagePayload(BaseModel):
    """Untrusted HTTP body kept separate from the pure chat contract."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    user_message: str
    idempotency_key: str
    document_ids: list[str] = []


class _CreateSessionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str | None = None


class CanonicalProjectRepository(Protocol):
    async def default_project(self, principal: VerifiedPrincipal) -> Project: ...

    async def require_project(
        self, principal: VerifiedPrincipal, project_id: str
    ) -> Project | None: ...

    async def require_document(
        self, principal: VerifiedPrincipal, project_id: str, document_id: str
    ) -> ProjectDocument | None: ...


class _ChatProfilePayload(BaseModel):
    """Explicit preference edit; bounds mirror profile_policy (FR-05)."""

    model_config = ConfigDict(extra="forbid")

    language: str | None = None
    timezone: str | None = None
    assistant_persona: str | None = None
    response_tone: str | None = None


def create_chat_router() -> APIRouter:
    """Create the transport-only router; runtime dependencies live on app.state."""

    router = APIRouter(prefix="/v1/cowork/chat", tags=["chat"])

    @router.post("/sessions", status_code=201)
    async def create_session(
        request: Request, payload: _CreateSessionPayload | None = None
    ) -> dict[str, str]:
        principal = await _verified_principal(request)
        requested_project_id = payload.project_id if payload else None
        project_repository = getattr(request.app.state, "project_repository", None)
        if project_repository is None and requested_project_id is None:
            project_id = "default-project"
        elif project_repository is None:
            raise HTTPException(status_code=404, detail="Project not found")
        elif requested_project_id is None:
            project = await cast(CanonicalProjectRepository, project_repository).default_project(
                principal
            )
            project_id = project.id
        else:
            owned_project = await cast(
                CanonicalProjectRepository, project_repository
            ).require_project(principal, requested_project_id)
            if owned_project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            project_id = owned_project.id
        sessions = _sessions(request)
        if project_id == "default-project":
            scope = await sessions.create(
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
            )
        else:
            scope = await sessions.create(
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                project_id=project_id,
            )
        controller = _controller_factory(request)(scope)
        _controllers(request)[scope.session_id] = controller
        response = {
            "session_id": scope.session_id,
            "feature": scope.feature,
        }
        if scope.project_id != "default-project":
            response["project_id"] = scope.project_id
        return response

    @router.post("/sessions/{session_id}/messages", response_class=StreamingResponse)
    @observe(name="api_chat_create_message")
    async def create_message(
        session_id: str,
        payload: _ChatMessagePayload,
        request: Request,
    ) -> Any:
        if payload.session_id != session_id:
            raise HTTPException(
                status_code=422,
                detail="Payload session_id must match the path session_id",
            )
        try:
            message = ChatMessageRequest.from_dict(payload.model_dump())
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Invalid chat message") from exc
        principal = await _verified_principal(request)
        try:
            scope = await _require_session(request, principal, session_id)
        except ChatSessionAccessDenied as exc:
            raise HTTPException(status_code=404, detail="Chat session not found") from exc
        if message.document_ids:
            settings = getattr(request.app.state, "user_documents_settings", None)
            if settings is None or not bool(getattr(settings, "enabled", False)):
                raise HTTPException(status_code=503, detail="User documents are disabled")
            if getattr(request.app.state, "project_document_vectors", None) is None:
                raise HTTPException(
                    status_code=503,
                    detail="Project document retrieval unavailable",
                )
            if getattr(request.app.state, "chat_routing_service", None) is None:
                raise HTTPException(
                    status_code=503,
                    detail="Project document routing unavailable",
                )
            repository = getattr(request.app.state, "project_repository", None)
            if repository is None:
                raise HTTPException(status_code=404, detail="Document not found")
            for document_id in message.document_ids:
                if (
                    document := await cast(
                        CanonicalProjectRepository, repository
                    ).require_document(principal, scope.project_id, document_id)
                ) is None:
                    raise HTTPException(status_code=404, detail="Document not found")
                if document.status != "ready":
                    raise HTTPException(status_code=409, detail="document_not_ready")
        controller = _controllers(request).get(session_id)
        if controller is None:
            controller = _controller_factory(request)(scope)
            _controllers(request)[session_id] = controller
        return StreamingResponse(
            _sse_events(controller, message, request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @router.get("/sessions")
    async def list_sessions(
        request: Request, project_id: str | None = Query(default=None)
    ) -> dict[str, object]:
        principal = await _verified_principal(request)
        if project_id is None:
            scopes = await _sessions(request).list_for(
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
            )
        else:
            scopes = await _sessions(request).list_for(
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                project_id=project_id,
            )
        history = _history_repository(request)
        titles = await history.titles_for(scopes) if history is not None else {}
        return {
            "sessions": [
                _session_response(scope, title=titles.get(scope.session_id)) for scope in scopes
            ]
        }

    @router.get("/sessions/{session_id}/messages")
    async def list_messages(session_id: str, request: Request) -> dict[str, object]:
        principal = await _verified_principal(request)
        try:
            scope = await _require_session(request, principal, session_id)
        except ChatSessionAccessDenied as exc:
            raise HTTPException(status_code=404, detail="Chat session not found") from exc
        namespace = MemoryNamespace(
            scope=scope,
            memory_type=MemoryType.SHORT_TERM,
            record_id=session_id,
            source_id=None,
        )
        history = _history_repository(request)
        turns = (
            await history.list_turns(scope)
            if history is not None
            else _buffer(request).read(namespace)
        )
        serialized: list[dict[str, object]] = []
        repository = getattr(request.app.state, "project_repository", None)
        for turn in turns:
            payload = turn.to_dict()
            coordinates = payload.get("citation_coordinates")
            if isinstance(coordinates, list) and repository is not None:
                for coordinate in coordinates:
                    if not isinstance(coordinate, dict):
                        continue
                    document_id = coordinate.get("document_id")
                    if not isinstance(document_id, str):
                        continue
                    document = await cast(
                        CanonicalProjectRepository, repository
                    ).require_document(principal, scope.project_id, document_id)
                    coordinate["unavailable"] = document is None or document.status != "ready"
            serialized.append(payload)
        return {"session_id": session_id, "turns": serialized}

    @router.delete("/sessions/{session_id}", status_code=204, response_model=None)
    async def delete_session(session_id: str, request: Request) -> None:
        principal = await _verified_principal(request)
        try:
            scope = await _require_session(request, principal, session_id)
        except ChatSessionAccessDenied as exc:
            raise HTTPException(status_code=404, detail="Chat session not found") from exc
        deleted = await _sessions(request).delete(
            session_id, tenant_id=principal.tenant_id, user_id=principal.user_id
        )
        if not deleted:
            raise HTTPException(status_code=404, detail="Chat session not found")
        _buffer(request).clear(
            MemoryNamespace(
                scope=scope,
                memory_type=MemoryType.SHORT_TERM,
                record_id=session_id,
                source_id=None,
            )
        )
        _controllers(request).pop(session_id, None)

    @router.get("/episodes")
    async def list_episodes(request: Request) -> dict[str, object]:
        principal = await _verified_principal(request)
        repository = _episodic_repository(request)
        try:
            episodes = await repository.list_episodes(
                _user_namespace(principal, MemoryType.EPISODIC)
            )
        except MemorySourceUnavailableError as exc:
            raise HTTPException(status_code=503, detail="Chat memory store unavailable") from exc
        return {"episodes": [episode.to_dict() for episode in episodes]}

    @router.get("/profile")
    async def get_profile(request: Request) -> dict[str, object]:
        principal = await _verified_principal(request)
        repository = _profile_repository(request)
        try:
            profile = await repository.read_profile(
                _user_namespace(principal, MemoryType.LONG_TERM)
            )
        except MemorySourceUnavailableError as exc:
            raise HTTPException(status_code=503, detail="Chat memory store unavailable") from exc
        if profile is None:
            raise HTTPException(status_code=404, detail="Chat profile not found")
        return profile.to_dict()

    @router.post("/profile", status_code=201)
    async def create_profile(payload: _ChatProfilePayload, request: Request) -> dict[str, object]:
        return await _write_profile(payload, request)

    @router.put("/profile")
    async def update_profile(payload: _ChatProfilePayload, request: Request) -> dict[str, object]:
        return await _write_profile(payload, request)

    @router.delete("/profile", status_code=204, response_model=None)
    async def delete_profile(request: Request) -> None:
        principal = await _verified_principal(request)
        repository = _profile_repository(request)
        try:
            await repository.delete_profile(_user_namespace(principal, MemoryType.LONG_TERM))
        except MemorySourceUnavailableError as exc:
            raise HTTPException(status_code=503, detail="Chat memory store unavailable") from exc

    @router.post("/sessions/{session_id}/task-episodes/{episode_id}/approve")
    async def approve_task_episode(
        session_id: str, episode_id: str, request: Request
    ) -> dict[str, object]:
        return await _task_episode_action(request, session_id, episode_id, "approve")

    @router.post("/sessions/{session_id}/task-episodes/{episode_id}/complete")
    async def complete_task_episode(
        session_id: str, episode_id: str, request: Request
    ) -> dict[str, object]:
        return await _task_episode_action(request, session_id, episode_id, "complete")

    @router.post("/sessions/{session_id}/task-episodes/{episode_id}/reject")
    async def reject_task_episode(
        session_id: str, episode_id: str, request: Request
    ) -> dict[str, object]:
        return await _task_episode_action(request, session_id, episode_id, "reject")

    @router.delete(
        "/sessions/{session_id}/task-episodes/{episode_id}",
        status_code=204,
        response_model=None,
    )
    async def delete_task_episode(session_id: str, episode_id: str, request: Request) -> None:
        controller = await _owned_controller(request, session_id)
        if not await controller.delete_task_episode(episode_id):
            raise HTTPException(status_code=404, detail="Chat task episode not found")

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


async def _owned_controller(request: Request, session_id: str) -> ChatController:
    principal = await _verified_principal(request)
    try:
        scope = await _require_session(request, principal, session_id)
    except ChatSessionAccessDenied as exc:
        raise HTTPException(status_code=404, detail="Chat session not found") from exc
    controller = _controllers(request).get(session_id)
    if controller is None:
        controller = _controller_factory(request)(scope)
        _controllers(request)[session_id] = controller
    return controller


async def _task_episode_action(
    request: Request, session_id: str, episode_id: str, action: str
) -> dict[str, object]:
    controller = await _owned_controller(request, session_id)
    operation = {
        "approve": controller.approve_task_episode,
        "complete": controller.complete_task_episode,
        "reject": controller.reject_task_episode,
    }[action]
    episode = await operation(episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="Chat task episode not found")
    return _task_episode_response(episode)


def _task_episode_response(episode: TaskEpisode) -> dict[str, object]:
    return {
        "episode_id": episode.episode_id,
        "validation_status": episode.validation_status.value,
        "retrieval_eligible": episode.retrieval_eligible,
    }


async def _write_profile(payload: _ChatProfilePayload, request: Request) -> dict[str, object]:
    principal = await _verified_principal(request)
    repository = _profile_repository(request)
    namespace = _user_namespace(principal, MemoryType.LONG_TERM)
    try:
        existing = await repository.read_profile(namespace)
    except MemorySourceUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Chat memory store unavailable") from exc
    now = datetime.now(UTC)
    profile = DeclarativeProfile(
        profile_id=existing.profile_id if existing else f"prof_{uuid.uuid4().hex}",
        user_id=principal.user_id,
        language=payload.language,
        timezone=payload.timezone,
        assistant_persona=payload.assistant_persona,
        response_tone=payload.response_tone,
        created_at=existing.created_at if existing else now,
        updated_at=now,
    )
    provenance = MemoryProvenance(
        source_type=MemoryProvenanceSource.EXPLICIT_USER_CONFIG,
        source_id="demo-profile-editor",
        chat_turn_id=None,
        pipeline_version=None,
        model_id=None,
        prompt_version=None,
    )
    try:
        authorize_profile_write(namespace, profile, provenance)
    except ProfileWriteRejected as exc:
        raise HTTPException(status_code=422, detail="Invalid profile preference") from exc
    try:
        stored = await repository.write_profile(namespace, profile)
    except MemorySourceUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Chat memory store unavailable") from exc
    return stored.to_dict()


def _user_namespace(principal: VerifiedPrincipal, memory_type: MemoryType) -> MemoryNamespace:
    # User-level administration namespace: storage keys are user/session/feature,
    # so the session component is a stable placeholder, not a live chat session.
    return MemoryNamespace(
        scope=ChatMemoryScope(
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            session_id="memory-admin",
        ),
        memory_type=memory_type,
        record_id=None,
        source_id=None,
    )


def _buffer(request: Request) -> ChatSessionBufferPort:
    return cast(ChatSessionBufferPort, request.app.state.chat_session_buffer)


def _profile_repository(request: Request) -> DeclarativeMemoryPort:
    repository = getattr(request.app.state, "chat_profile_repository", None)
    if repository is None:
        raise HTTPException(status_code=503, detail="Chat memory store unavailable")
    return cast(DeclarativeMemoryPort, repository)


def _episodic_repository(request: Request) -> EpisodicMemoryPort:
    repository = getattr(request.app.state, "chat_task_episode_repository", None)
    if repository is None:
        raise HTTPException(status_code=503, detail="Chat memory store unavailable")
    return cast(EpisodicMemoryPort, repository)


async def _require_session(
    request: Request, principal: VerifiedPrincipal, session_id: str
) -> ChatMemoryScope:
    return await _sessions(request).require(
        session_id, tenant_id=principal.tenant_id, user_id=principal.user_id
    )


def _controllers(request: Request) -> dict[str, ChatController]:
    controllers = getattr(request.app.state, "chat_controllers", None)
    if controllers is None:
        controllers = {}
        request.app.state.chat_controllers = controllers
    return cast(dict[str, ChatController], controllers)


def _sessions(request: Request) -> ChatSessionRegistryPort:
    return cast(ChatSessionRegistryPort, request.app.state.chat_sessions)


def _controller_factory(request: Request) -> ControllerFactory:
    return cast(ControllerFactory, request.app.state.chat_controller_factory)


def _session_response(scope: ChatMemoryScope, *, title: str | None = None) -> dict[str, str]:
    payload = {"session_id": scope.session_id, "feature": scope.feature}
    if scope.project_id != "default-project":
        payload["project_id"] = scope.project_id
    if title is not None:
        payload["title"] = title
    return payload


def _history_repository(request: Request) -> ChatHistoryPort | None:
    repository = getattr(request.app.state, "chat_history_repository", None)
    return cast(ChatHistoryPort | None, repository)


__all__ = ["create_chat_router"]
