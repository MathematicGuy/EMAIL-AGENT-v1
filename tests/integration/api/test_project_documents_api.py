import asyncio
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import FastAPI, Request

from cowork_agent.api.projects import create_project_router
from cowork_agent.identity import VerifiedPrincipal
from cowork_agent.persistence.repositories.projects import (
    DocumentIngestionJob,
    Project,
    ProjectDocument,
)


class Projects:
    def __init__(self) -> None:
        self.project = Project("project-1", "workspace-1", "user-1", "Default project", True)
        self.document: ProjectDocument | None = None

    async def create(self, principal: VerifiedPrincipal, name: str) -> Project:
        assert principal.workspace_id == "workspace-1"
        return Project("project-2", "workspace-1", "user-1", name, False)

    async def list_for(self, principal: VerifiedPrincipal) -> tuple[Project, ...]:
        return (self.project,)

    async def require_project(
        self, principal: VerifiedPrincipal, project_id: str
    ) -> Project | None:
        return self.project if project_id == "project-1" else None

    async def create_or_get_document(self, **kwargs: object) -> tuple[ProjectDocument, bool]:
        self.document = ProjectDocument(
            "doc-1", "project-1", "workspace-1", "user-1", "plan.pdf", "application/pdf", 20,
            "a" * 64, "workspace/workspace-1/user/user-1/project/project-1/document/doc-1/source",
            "received", datetime.now(UTC) + timedelta(days=30),
        )
        return self.document, True

    async def require_document(
        self, principal: VerifiedPrincipal, project_id: str, document_id: str
    ) -> ProjectDocument | None:
        return self.document if project_id == "project-1" and document_id == "doc-1" else None

    async def list_documents(
        self, principal: VerifiedPrincipal, project_id: str
    ) -> tuple[ProjectDocument, ...]:
        return () if self.document is None else (self.document,)

    async def mark_upload_completed(
        self, principal: VerifiedPrincipal, project_id: str, document_id: str
    ) -> DocumentIngestionJob | None:
        if await self.require_document(principal, project_id, document_id) is None:
            return None
        return DocumentIngestionJob("job-1", document_id, "queued", 0)


class Storage:
    async def create_signed_upload_url(self, object_key: str) -> str:
        assert object_key.endswith("/source")
        return "https://storage.example/upload-token"

    async def create_signed_download_url(self, object_key: str, expires_in: int) -> str:
        assert object_key.endswith("/source") and expires_in == 60
        return "https://storage.example/download-token"


def test_project_document_api_authorizes_then_returns_only_signed_urls() -> None:
    async def scenario() -> None:
        app = FastAPI()
        app.include_router(create_project_router())
        app.state.project_repository = Projects()
        app.state.private_storage = Storage()

        async def principal(request: Request) -> VerifiedPrincipal:
            del request
            return VerifiedPrincipal("workspace-1", "user-1")

        app.state.chat_principal_resolver = principal
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            created = await client.post("/v1/cowork/chat/projects", json={"name": "Finance"})
            upload = await client.post(
                "/v1/cowork/chat/projects/project-1/documents",
                json={
                    "filename": "plan.pdf",
                    "media_type": "application/pdf",
                    "byte_size": 20,
                    "content_sha256": "a" * 64,
                },
            )
            download = await client.get(
                "/v1/cowork/chat/projects/project-1/documents/doc-1/download"
            )
            completed = await client.post(
                "/v1/cowork/chat/projects/project-1/documents/doc-1/complete"
            )
            foreign = await client.post(
                "/v1/cowork/chat/projects/not-owned/documents",
                json={
                    "filename": "plan.pdf", "media_type": "application/pdf", "byte_size": 20,
                    "content_sha256": "a" * 64,
                },
            )

        assert created.status_code == 201
        assert created.json() == {"project_id": "project-2", "name": "Finance"}
        assert upload.status_code == 202
        assert upload.json()["upload_url"] == "https://storage.example/upload-token"
        assert "secret" not in upload.text
        assert download.status_code == 200
        assert download.json()["download_url"] == "https://storage.example/download-token"
        assert completed.status_code == 202
        assert completed.json() == {"document_id": "doc-1", "status": "queued"}
        assert foreign.status_code == 404

    asyncio.run(scenario())
