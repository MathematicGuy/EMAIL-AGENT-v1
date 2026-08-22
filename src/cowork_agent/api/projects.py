"""FastAPI project-document transport; storage credentials stay server-side."""

from collections.abc import Awaitable, Callable
from typing import Protocol, cast

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from cowork_agent.identity import VerifiedPrincipal
from cowork_agent.integrations.storage.supabase import StorageUnavailable
from cowork_agent.persistence.repositories.projects import (
    DocumentIngestionJob,
    Project,
    ProjectDocument,
)

PrincipalResolver = Callable[[Request], Awaitable[VerifiedPrincipal]]


class ProjectRepository(Protocol):
    async def default_project(self, principal: VerifiedPrincipal) -> Project: ...

    async def create(self, principal: VerifiedPrincipal, name: str) -> Project: ...

    async def list_for(self, principal: VerifiedPrincipal) -> tuple[Project, ...]: ...

    async def require_project(
        self, principal: VerifiedPrincipal, project_id: str
    ) -> Project | None: ...

    async def create_or_get_document(
        self, **kwargs: object
    ) -> tuple[ProjectDocument, bool]: ...

    async def require_document(
        self, principal: VerifiedPrincipal, project_id: str, document_id: str
    ) -> ProjectDocument | None: ...

    async def list_documents(
        self, principal: VerifiedPrincipal, project_id: str
    ) -> tuple[ProjectDocument, ...]: ...

    async def mark_upload_completed(
        self, principal: VerifiedPrincipal, project_id: str, document_id: str
    ) -> DocumentIngestionJob | None: ...

    async def begin_deletion(
        self, principal: VerifiedPrincipal, project_id: str, document_id: str
    ) -> ProjectDocument | None: ...

    async def begin_project_deletion(
        self, principal: VerifiedPrincipal, project_id: str
    ) -> tuple[Project, Project | None, tuple[str, ...]] | None: ...


class PrivateStorage(Protocol):
    async def create_signed_upload_url(self, object_key: str) -> str: ...

    async def create_signed_download_url(self, object_key: str, expires_in: int) -> str: ...

    async def object_exists(self, object_key: str) -> bool: ...


class _ProjectPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)


class _DocumentUploadPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=255)
    media_type: str
    byte_size: int = Field(gt=0)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def create_project_router() -> APIRouter:
    router = APIRouter(prefix="/v1/cowork/chat", tags=["projects"])

    @router.post("/projects", status_code=201)
    async def create_project(payload: _ProjectPayload, request: Request) -> dict[str, object]:
        project = await _projects(request).create(await _principal(request), payload.name)
        return _project_response(project)

    @router.get("/projects")
    async def list_projects(request: Request) -> dict[str, object]:
        principal = await _principal(request)
        await _projects(request).default_project(principal)
        projects = await _projects(request).list_for(principal)
        return {"projects": [_project_response(project) for project in projects]}

    @router.post("/projects/{project_id}/documents", status_code=202)
    async def initiate_upload(
        project_id: str, payload: _DocumentUploadPayload, request: Request
    ) -> dict[str, object]:
        _require_user_documents_enabled(request)
        await _require_user_documents_ready(request)
        principal = await _principal(request)
        if await _projects(request).require_project(principal, project_id) is None:
            raise HTTPException(status_code=404, detail="Project not found")
        try:
            settings = request.app.state.user_documents_settings
            if payload.byte_size > settings.max_file_bytes:
                raise HTTPException(status_code=413, detail="document_too_large")
            document, created = await _projects(request).create_or_get_document(
                principal=principal,
                project_id=project_id,
                filename=payload.filename,
                media_type=payload.media_type,
                byte_size=payload.byte_size,
                content_sha256=payload.content_sha256,
                expires_in_seconds=settings.retention_days * 86_400,
                max_documents_per_project=settings.max_documents_per_project,
                max_project_bytes=settings.max_project_bytes,
            )
            storage = _storage(request)
            upload_url = None
            if created or document.status == "received":
                if hasattr(storage, "upload_bytes"):
                    upload_url = (
                        f"/v1/cowork/chat/projects/{project_id}/documents/{document.id}/source"
                    )
                else:
                    upload_url = await storage.create_signed_upload_url(document.storage_key)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid project document") from exc
        except StorageUnavailable as exc:
            raise HTTPException(status_code=503, detail="Private storage unavailable") from exc
        response: dict[str, object] = {
            "document_id": document.id,
            "status": document.status,
        }
        if upload_url is not None:
            response["upload_url"] = upload_url
        return response

    @router.get("/projects/{project_id}/documents")
    async def list_documents(project_id: str, request: Request) -> dict[str, object]:
        _require_user_documents_enabled(request)
        principal = await _principal(request)
        if await _projects(request).require_project(principal, project_id) is None:
            raise HTTPException(status_code=404, detail="Project not found")
        documents = await _projects(request).list_documents(principal, project_id)
        return {"documents": [_document_response(document) for document in documents]}

    @router.get("/projects/{project_id}/documents/{document_id}")
    async def get_document(
        project_id: str, document_id: str, request: Request
    ) -> dict[str, object]:
        _require_user_documents_enabled(request)
        return _document_response(await _owned_document(request, project_id, document_id))

    @router.post("/projects/{project_id}/documents/{document_id}/complete", status_code=202)
    async def complete_upload(
        project_id: str, document_id: str, request: Request
    ) -> dict[str, str]:
        _require_user_documents_enabled(request)
        await _require_user_documents_ready(request)
        principal = await _principal(request)
        document = await _projects(request).require_document(
            principal, project_id, document_id
        )
        if document is None:
            raise HTTPException(status_code=404, detail="Project document not found")
        if document.status != "received":
            return {"document_id": document_id, "status": document.status}
        try:
            if not await _storage(request).object_exists(document.storage_key):
                raise HTTPException(status_code=409, detail="upload_not_found")
        except StorageUnavailable as exc:
            raise HTTPException(status_code=503, detail="Private storage unavailable") from exc
        job = await _projects(request).mark_upload_completed(principal, project_id, document_id)
        if job is None:
            raise HTTPException(
                status_code=404, detail="Project document not available for ingestion"
            )
        return {"document_id": document_id, "status": document.status}

    @router.put(
        "/projects/{project_id}/documents/{document_id}/source",
        status_code=204,
        response_model=None,
    )
    async def upload_local_source(project_id: str, document_id: str, request: Request) -> Response:
        _require_user_documents_enabled(request)
        storage = _storage(request)
        upload_bytes = getattr(storage, "upload_bytes", None)
        if upload_bytes is None:
            raise HTTPException(status_code=405, detail="Direct upload is unavailable")
        document = await _projects(request).require_document(
            await _principal(request), project_id, document_id
        )
        if document is None:
            raise HTTPException(status_code=404, detail="Project document not found")
        content = await request.body()
        if len(content) != document.byte_size:
            raise HTTPException(status_code=422, detail="source_size_mismatch")
        await upload_bytes(document.storage_key, content)
        return Response(status_code=204)

    @router.delete(
        "/projects/{project_id}/documents/{document_id}",
        status_code=202,
        response_model=None,
    )
    async def delete_document(
        project_id: str, document_id: str, request: Request
    ) -> Response | dict[str, str]:
        _require_user_documents_enabled(request)
        principal = await _principal(request)
        current = await _projects(request).require_document(principal, project_id, document_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Project document not found")
        if current.status == "deleted":
            return Response(status_code=204)
        document = await _projects(request).begin_deletion(principal, project_id, document_id)
        if document is None:
            return {"document_id": document_id, "status": current.status}
        return {"document_id": document.id, "status": "deleting"}

    @router.delete("/projects/{project_id}", status_code=202, response_model=None)
    async def delete_project(
        project_id: str, request: Request
    ) -> Response | dict[str, object]:
        principal = await _principal(request)
        result = await _projects(request).begin_project_deletion(
            principal, project_id
        )
        if result is None:
            raise HTTPException(status_code=404, detail="Project not found")
        sessions = getattr(request.app.state, "chat_sessions", None)
        if sessions is not None and hasattr(sessions, "delete_project"):
            await sessions.delete_project(
                user_id=principal.user_id,
                project_id=project_id,
                tenant_id=principal.workspace_id,
            )
        project, replacement, document_ids = result
        if project.deleted_at is not None:
            return Response(status_code=204)
        return {
            "project_id": project.id,
            "status": "deleting",
            "replacement_project_id": replacement.id if replacement is not None else None,
            "document_ids": list(document_ids),
        }

    @router.get("/projects/{project_id}/documents/{document_id}/download")
    async def download_document(
        project_id: str, document_id: str, request: Request
    ) -> dict[str, object]:
        _require_user_documents_enabled(request)
        document = await _owned_document(request, project_id, document_id)
        if document.status in {"deleted", "deleting"}:
            raise HTTPException(status_code=404, detail="Project document not found")
        try:
            download_url = await _storage(request).create_signed_download_url(
                document.storage_key, 60
            )
        except StorageUnavailable as exc:
            raise HTTPException(status_code=503, detail="Private storage unavailable") from exc
        return {"document_id": document.id, "download_url": download_url}

    return router


def _require_user_documents_enabled(request: Request) -> None:
    settings = getattr(request.app.state, "user_documents_settings", None)
    if settings is None or not bool(getattr(settings, "enabled", False)):
        raise HTTPException(status_code=503, detail="User documents are disabled")


async def _require_user_documents_ready(request: Request) -> None:
    if (
        getattr(request.app.state, "private_storage", None) is None
        or getattr(request.app.state, "project_document_vectors", None) is None
        or getattr(request.app.state, "chat_routing_service", None) is None
    ):
        raise HTTPException(status_code=503, detail="Project document plane unavailable")
    repository = _projects(request)
    heartbeat = getattr(repository, "worker_heartbeat_is_fresh", None)
    if heartbeat is None or not await heartbeat(max_age_seconds=120):
        raise HTTPException(status_code=503, detail="Project document worker unavailable")


async def _owned_document(request: Request, project_id: str, document_id: str) -> ProjectDocument:
    document = await _projects(request).require_document(
        await _principal(request), project_id, document_id
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Project document not found")
    return document


async def _principal(request: Request) -> VerifiedPrincipal:
    resolver = cast(
        PrincipalResolver | None,
        getattr(request.app.state, "chat_principal_resolver", None),
    )
    if resolver is None:
        raise HTTPException(status_code=503, detail="Chat identity is unavailable")
    return await resolver(request)


def _projects(request: Request) -> ProjectRepository:
    repository = getattr(request.app.state, "project_repository", None)
    if repository is None:
        raise HTTPException(status_code=503, detail="Project storage unavailable")
    return cast(ProjectRepository, repository)


def _storage(request: Request) -> PrivateStorage:
    storage = getattr(request.app.state, "private_storage", None)
    if storage is None:
        raise HTTPException(status_code=503, detail="Private storage unavailable")
    return cast(PrivateStorage, storage)


def _project_response(project: Project) -> dict[str, object]:
    return {
        "project_id": project.id,
        "name": project.name,
        "is_default": project.is_default,
        "created_at": project.created_at.isoformat() if project.created_at else None,
    }


def _document_response(document: ProjectDocument) -> dict[str, object]:
    return {
        "document_id": document.id,
        "filename": document.filename,
        "media_type": document.media_type,
        "byte_size": document.byte_size,
        "status": document.status,
        "error_code": document.error_code,
        "page_count": document.page_count or 0,
        "chunk_count": document.chunk_count or 0,
        "ocr_page_count": document.ocr_page_count or 0,
        "expires_at": document.expires_at.isoformat(),
        "created_at": document.created_at.isoformat() if document.created_at else None,
        "updated_at": document.updated_at.isoformat() if document.updated_at else None,
    }
