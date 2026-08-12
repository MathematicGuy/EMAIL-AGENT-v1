"""FastAPI project-document transport; storage credentials stay server-side."""

from collections.abc import Awaitable, Callable
from typing import Protocol, cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from cowork_agent.identity import VerifiedPrincipal
from cowork_agent.integrations.storage.supabase import StorageUnavailable
from cowork_agent.persistence.repositories.projects import Project, ProjectDocument

PrincipalResolver = Callable[[Request], Awaitable[VerifiedPrincipal]]


class ProjectRepository(Protocol):
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


class PrivateStorage(Protocol):
    async def create_signed_upload_url(self, object_key: str) -> str: ...

    async def create_signed_download_url(self, object_key: str, expires_in: int) -> str: ...


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
    async def create_project(payload: _ProjectPayload, request: Request) -> dict[str, str]:
        project = await _projects(request).create(await _principal(request), payload.name)
        return {"project_id": project.id, "name": project.name}

    @router.get("/projects")
    async def list_projects(request: Request) -> dict[str, object]:
        projects = await _projects(request).list_for(await _principal(request))
        return {"projects": [_project_response(project) for project in projects]}

    @router.post("/projects/{project_id}/documents", status_code=202)
    async def initiate_upload(
        project_id: str, payload: _DocumentUploadPayload, request: Request
    ) -> dict[str, object]:
        principal = await _principal(request)
        if await _projects(request).require_project(principal, project_id) is None:
            raise HTTPException(status_code=404, detail="Project not found")
        try:
            document, _created = await _projects(request).create_or_get_document(
                principal=principal,
                project_id=project_id,
                filename=payload.filename,
                media_type=payload.media_type,
                byte_size=payload.byte_size,
                content_sha256=payload.content_sha256,
                expires_in_seconds=2_592_000,
            )
            upload_url = await _storage(request).create_signed_upload_url(document.storage_key)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid project document") from exc
        except StorageUnavailable as exc:
            raise HTTPException(status_code=503, detail="Private storage unavailable") from exc
        return {
            "document_id": document.id,
            "status": document.status,
            "upload_url": upload_url,
        }

    @router.get("/projects/{project_id}/documents")
    async def list_documents(project_id: str, request: Request) -> dict[str, object]:
        principal = await _principal(request)
        if await _projects(request).require_project(principal, project_id) is None:
            raise HTTPException(status_code=404, detail="Project not found")
        documents = await _projects(request).list_documents(principal, project_id)
        return {"documents": [_document_response(document) for document in documents]}

    @router.get("/projects/{project_id}/documents/{document_id}/download")
    async def download_document(
        project_id: str, document_id: str, request: Request
    ) -> dict[str, object]:
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
    return {"project_id": project.id, "name": project.name, "is_default": project.is_default}


def _document_response(document: ProjectDocument) -> dict[str, object]:
    return {
        "document_id": document.id,
        "filename": document.filename,
        "media_type": document.media_type,
        "byte_size": document.byte_size,
        "status": document.status,
        "expires_at": document.expires_at.isoformat(),
    }
