"""FastAPI adapter for V2-M4A chat sessions and typed SSE events."""

from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, cast

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
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
from cowork_agent.domain.project_documents import ProjectDocumentStatus
from cowork_agent.features.ai_chat.controller import (
    ChatController,
    ChatSessionAccessDenied,
    ChatSessionRegistryPort,
)
from cowork_agent.features.ai_chat.memory_gateway import MemorySourceUnavailableError
from cowork_agent.features.ai_chat.ports import (
    ChatSessionBufferPort,
    DeclarativeMemoryPort,
    EpisodicMemoryPort,
)
from cowork_agent.features.ai_chat.profile_policy import (
    ProfileWriteRejected,
    authorize_profile_write,
)
from cowork_agent.features.user_documents.ports import (
    ProjectDocumentRepositoryPort,
    ProjectRepositoryPort,
)
from cowork_agent.identity import VerifiedPrincipal
from cowork_agent.integrations.project_documents.encrypted_store import EncryptedDocumentStore
from cowork_agent.integrations.project_documents.sniffing import sniff_media_type

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


class _CreateProjectPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str


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

    @router.post("/projects", status_code=201)
    async def create_project(payload: _CreateProjectPayload, request: Request) -> dict[str, object]:
        principal = await _verified_principal(request)
        name = payload.name.strip()
        if not 1 <= len(name) <= 200:
            raise HTTPException(
                status_code=422, detail="Project name must contain 1-200 characters"
            )
        project = await _project_repository(request).create(
            principal.tenant_id, principal.user_id, name
        )
        return project.to_dict()

    @router.get("/projects")
    async def list_projects(request: Request) -> dict[str, object]:
        principal = await _verified_principal(request)
        repository = _project_repository(request)
        await repository.resolve_default(principal.tenant_id, principal.user_id)
        projects = await repository.list_owned(principal.tenant_id, principal.user_id)
        return {"projects": [project.to_dict() for project in projects]}

    @router.delete("/projects/{project_id}", status_code=204, response_model=None)
    async def delete_project(project_id: str, request: Request) -> None:
        principal = await _verified_principal(request)
        projects = _project_repository(request)
        project = await projects.get_owned(
            principal.tenant_id,
            principal.user_id,
            project_id,
            include_deleted=True,
        )
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        if await projects.get_owned(principal.tenant_id, principal.user_id, project_id) is None:
            return
        at = datetime.now(UTC)
        document_ids = await _document_repository(request).mark_project_deleted(
            principal.tenant_id, principal.user_id, project_id, at=at
        )
        await _purge_document_objects(request, document_ids)
        removed_sessions = await _sessions(request).delete_project(
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            project_id=project_id,
        )
        for session_id in removed_sessions:
            _controllers(request).pop(session_id, None)
        deleted, _replacement = await projects.delete_owned(
            principal.tenant_id, principal.user_id, project_id, at=at
        )
        if not deleted:
            raise HTTPException(status_code=404, detail="Project not found")

    @router.post("/sessions", status_code=201)
    async def create_session(
        request: Request, payload: _CreateSessionPayload | None = None
    ) -> dict[str, str]:
        principal = await _verified_principal(request)
        requested_project_id = payload.project_id if payload else None
        project_repository = getattr(request.app.state, "chat_project_repository", None)
        if project_repository is None and requested_project_id is None:
            project_id = "default-project"
        elif project_repository is None:
            raise HTTPException(status_code=404, detail="Project not found")
        elif requested_project_id is None:
            project = await cast(ProjectRepositoryPort, project_repository).resolve_default(
                principal.tenant_id, principal.user_id
            )
            project_id = project.project_id
        else:
            owned_project = await cast(ProjectRepositoryPort, project_repository).get_owned(
                principal.tenant_id, principal.user_id, requested_project_id
            )
            if owned_project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            project_id = owned_project.project_id
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

    @router.post("/sessions/{session_id}/messages")
    @observe(name="api_chat_create_message")
    async def create_message(
        session_id: str,
        payload: _ChatMessagePayload,
        request: Request,
    ) -> StreamingResponse:
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
            repository = getattr(request.app.state, "project_document_repository", None)
            if repository is None:
                raise HTTPException(status_code=404, detail="Document not found")
            for document_id in message.document_ids:
                if (
                    await cast(ProjectDocumentRepositoryPort, repository).get_owned(
                        principal.tenant_id,
                        principal.user_id,
                        scope.project_id,
                        document_id,
                    )
                    is None
                ):
                    raise HTTPException(status_code=404, detail="Document not found")
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
        return {"sessions": [_session_response(scope) for scope in scopes]}

    @router.post("/projects/{project_id}/documents")
    async def upload_project_document(
        project_id: str,
        request: Request,
        file: Annotated[UploadFile, File()],
    ) -> JSONResponse:
        principal = await _verified_principal(request)
        _require_documents_enabled(request)
        if (
            await _project_repository(request).get_owned(
                principal.tenant_id, principal.user_id, project_id
            )
            is None
        ):
            raise HTTPException(status_code=404, detail="Project not found")
        settings = request.app.state.user_documents_settings
        data, size_bytes, digest = await _read_bounded_upload(file, settings.max_file_bytes)
        try:
            media_type = sniff_media_type(data)
        except ValueError as exc:
            raise HTTPException(
                status_code=415, detail="Only valid PDF and DOCX files are supported"
            ) from exc
        existing = await _document_repository(request).list_owned(
            principal.tenant_id, principal.user_id, project_id
        )
        duplicate = next((item for item in existing if item.sha256 == digest), None)
        if duplicate is not None:
            return JSONResponse(duplicate.to_dict(), status_code=200)
        if len(existing) >= settings.max_documents_per_project:
            raise HTTPException(status_code=422, detail="Project document quota exceeded")
        if sum(item.size_bytes for item in existing) + size_bytes > settings.max_project_bytes:
            raise HTTPException(status_code=422, detail="Project storage quota exceeded")
        now = datetime.now(UTC)
        title = (file.filename or "Document")[:300].strip() or "Document"
        document, created = await _document_repository(request).create_or_get(
            document_id=f"document_{uuid.uuid4().hex}",
            project_id=project_id,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            title=title,
            media_type=media_type,
            size_bytes=size_bytes,
            sha256=digest,
            created_at=now,
            expires_at=now + timedelta(days=settings.retention_days),
        )
        if created:
            _document_store(request).put_source(document.document_id, data)
            try:
                await request.app.state.document_ingestion_dispatcher.dispatch(document)
            except Exception as exc:
                raise HTTPException(
                    status_code=503, detail="Document ingestion queue unavailable"
                ) from exc
        return JSONResponse(document.to_dict(), status_code=202 if created else 200)

    @router.get("/projects/{project_id}/documents")
    async def list_project_documents(project_id: str, request: Request) -> dict[str, object]:
        principal = await _verified_principal(request)
        await _require_owned_project(request, principal, project_id)
        documents = await _document_repository(request).list_owned(
            principal.tenant_id, principal.user_id, project_id
        )
        return {"documents": [document.to_dict() for document in documents]}

    @router.get("/projects/{project_id}/documents/{document_id}")
    async def get_project_document(
        project_id: str, document_id: str, request: Request
    ) -> dict[str, object]:
        principal = await _verified_principal(request)
        document = await _document_repository(request).get_owned(
            principal.tenant_id, principal.user_id, project_id, document_id
        )
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found")
        return document.to_dict()

    @router.delete(
        "/projects/{project_id}/documents/{document_id}", status_code=204, response_model=None
    )
    async def delete_project_document(project_id: str, document_id: str, request: Request) -> None:
        principal = await _verified_principal(request)
        repository = _document_repository(request)
        document = await repository.get_owned(
            principal.tenant_id, principal.user_id, project_id, document_id
        )
        if document is None:
            document = next(
                (
                    item
                    for item in await repository.list_owned(
                        principal.tenant_id,
                        principal.user_id,
                        project_id,
                        include_deleted=True,
                    )
                    if item.document_id == document_id
                ),
                None,
            )
            if document is None:
                raise HTTPException(status_code=404, detail="Document not found")
            await _purge_document_objects(request, (document_id,))
            return
        deleted = await repository.transition(
            document_id,
            from_statuses=(document.status,),
            to_status=ProjectDocumentStatus.DELETED,
            at=datetime.now(UTC),
        )
        if deleted is None:
            raise HTTPException(status_code=404, detail="Document not found")
        await _purge_document_objects(request, (document_id,))

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
        turns = _buffer(request).read(namespace)
        return {"session_id": session_id, "turns": [turn.to_dict() for turn in turns]}

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
        tenant_id=principal.tenant_id,
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
    # User-level administration namespace: storage keys are tenant/user/feature,
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


def _session_response(scope: ChatMemoryScope) -> dict[str, str]:
    payload = {"session_id": scope.session_id, "feature": scope.feature}
    if scope.project_id != "default-project":
        payload["project_id"] = scope.project_id
    return payload


def _project_repository(request: Request) -> ProjectRepositoryPort:
    repository = getattr(request.app.state, "chat_project_repository", None)
    if repository is None:
        raise HTTPException(status_code=503, detail="Project store unavailable")
    return cast(ProjectRepositoryPort, repository)


def _document_repository(request: Request) -> ProjectDocumentRepositoryPort:
    repository = getattr(request.app.state, "project_document_repository", None)
    if repository is None:
        raise HTTPException(status_code=503, detail="Document store unavailable")
    return cast(ProjectDocumentRepositoryPort, repository)


def _document_store(request: Request) -> EncryptedDocumentStore:
    store = getattr(request.app.state, "project_document_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Encrypted document store unavailable")
    return cast(EncryptedDocumentStore, store)


def _require_documents_enabled(request: Request) -> None:
    settings = getattr(request.app.state, "user_documents_settings", None)
    if settings is None or not settings.enabled:
        raise HTTPException(status_code=503, detail="Project documents are disabled")


async def _require_owned_project(
    request: Request, principal: VerifiedPrincipal, project_id: str
) -> None:
    if (
        await _project_repository(request).get_owned(
            principal.tenant_id, principal.user_id, project_id
        )
        is None
    ):
        raise HTTPException(status_code=404, detail="Project not found")


async def _read_bounded_upload(upload: UploadFile, maximum_bytes: int) -> tuple[bytes, int, str]:
    digest = hashlib.sha256()
    size = 0
    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            path = Path(handle.name)
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > maximum_bytes:
                    raise HTTPException(status_code=413, detail="Document exceeds size limit")
                digest.update(chunk)
                handle.write(chunk)
        if size == 0:
            raise HTTPException(status_code=422, detail="Document is empty")
        return path.read_bytes(), size, digest.hexdigest()
    finally:
        await upload.close()
        if path is not None:
            path.unlink(missing_ok=True)


async def _purge_document_objects(request: Request, document_ids: tuple[str, ...]) -> None:
    vectors = getattr(request.app.state, "project_document_vectors", None)
    store = getattr(request.app.state, "project_document_store", None)
    try:
        for document_id in document_ids:
            if vectors is not None:
                await vectors.delete_document(document_id)
            if store is not None:
                store.delete(document_id)
            await _document_repository(request).confirm_cleanup(document_id, at=datetime.now(UTC))
    except Exception as exc:
        # Metadata is already retrieval-ineligible. A later retention/recovery
        # pass repeats physical cleanup rather than making deletion visible again.
        raise HTTPException(status_code=503, detail="Document cleanup is pending") from exc


__all__ = ["create_chat_router"]
