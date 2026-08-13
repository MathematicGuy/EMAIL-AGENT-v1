from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
from cryptography.fernet import Fernet
from fastapi import FastAPI, Request

from cowork_agent.api.chat import create_chat_router
from cowork_agent.api.projects import create_project_router
from cowork_agent.domain.project_documents import (
    ProjectDocumentFailureReason,
    ProjectDocumentStatus,
)
from cowork_agent.features.ai_chat.controller import InMemoryChatSessionRegistry
from cowork_agent.identity import VerifiedPrincipal
from cowork_agent.integrations.project_documents.encrypted_store import EncryptedDocumentStore
from cowork_agent.persistence.repositories.project_documents import (
    InMemoryProjectDocumentRepository,
    InMemoryProjectRepository,
)
from cowork_agent.persistence.repositories.projects import (
    DocumentIngestionJob,
    Project,
    ProjectDocument,
)


class RecordingDispatcher:
    def __init__(self) -> None:
        self.document_ids: list[str] = []

    async def dispatch(self, document) -> None:
        self.document_ids.append(document.document_id)


def _app(tmp_path: Path) -> tuple[FastAPI, RecordingDispatcher]:
    app = FastAPI()
    app.include_router(create_chat_router())
    app.state.chat_project_repository = InMemoryProjectRepository()
    app.state.project_document_repository = InMemoryProjectDocumentRepository()
    app.state.chat_sessions = InMemoryChatSessionRegistry()
    app.state.chat_session_repository = None
    app.state.chat_controllers = {}
    app.state.chat_controller_factory = lambda scope: object()
    app.state.project_document_store = EncryptedDocumentStore(
        tmp_path, Fernet.generate_key().decode("ascii")
    )
    app.state.project_document_vectors = None
    app.state.user_documents_settings = SimpleNamespace(
        enabled=True,
        max_file_bytes=1024,
        max_documents_per_project=1,
        max_project_bytes=2048,
        retention_days=30,
    )
    dispatcher = RecordingDispatcher()
    app.state.document_ingestion_dispatcher = dispatcher

    async def principal(request: Request) -> VerifiedPrincipal:
        del request
        return VerifiedPrincipal("tenant_1", "user_1")

    app.state.chat_principal_resolver = principal
    return app, dispatcher


def test_project_session_and_upload_contracts_are_backend_scoped(tmp_path: Path) -> None:
    async def scenario() -> None:
        app, dispatcher = _app(tmp_path)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            listed = await client.get("/v1/cowork/chat/projects")
            assert listed.status_code == 200
            default_project = listed.json()["projects"][0]
            project_id = default_project["project_id"]

            session = await client.post("/v1/cowork/chat/sessions", json={"project_id": project_id})
            assert session.status_code == 201
            assert session.json()["project_id"] == project_id

            pdf = {"file": ("policy.docx", b"%PDF-1.7\nbody", "application/octet-stream")}
            first = await client.post(f"/v1/cowork/chat/projects/{project_id}/documents", files=pdf)
            assert first.status_code == 202
            assert first.json()["media_type"] == "application/pdf"
            assert dispatcher.document_ids == [first.json()["document_id"]]

            duplicate = await client.post(
                f"/v1/cowork/chat/projects/{project_id}/documents", files=pdf
            )
            assert duplicate.status_code == 200
            assert duplicate.json()["document_id"] == first.json()["document_id"]
            assert len(dispatcher.document_ids) == 1

            document_repository = app.state.project_document_repository
            await document_repository.transition(
                first.json()["document_id"],
                from_statuses=(ProjectDocumentStatus.RECEIVED,),
                to_status=ProjectDocumentStatus.FAILED,
                at=datetime.now(UTC),
                reason_code=ProjectDocumentFailureReason.INVALID_PDF,
            )
            retried = await client.post(
                f"/v1/cowork/chat/projects/{project_id}/documents", files=pdf
            )
            assert retried.status_code == 202
            assert retried.json()["document_id"] != first.json()["document_id"]
            assert dispatcher.document_ids == [
                first.json()["document_id"],
                retried.json()["document_id"],
            ]

            over_quota = await client.post(
                f"/v1/cowork/chat/projects/{project_id}/documents",
                files={"file": ("other.pdf", b"%PDF-1.7\nother", "application/pdf")},
            )
            assert over_quota.status_code == 422

            other = await client.post("/v1/cowork/chat/projects", json={"name": "Other Project"})
            cross_project = await client.post(
                f"/v1/cowork/chat/projects/{other.json()['project_id']}/documents",
                files=pdf,
            )
            assert cross_project.status_code == 202
            assert cross_project.json()["document_id"] != first.json()["document_id"]

            document_path = (
                f"/v1/cowork/chat/projects/{other.json()['project_id']}/documents/"
                f"{cross_project.json()['document_id']}"
            )
            assert (await client.delete(document_path)).status_code == 204
            assert (await client.delete(document_path)).status_code == 204
            project_path = f"/v1/cowork/chat/projects/{other.json()['project_id']}"
            assert (await client.delete(project_path)).status_code == 204
            assert (await client.delete(project_path)).status_code == 204

    asyncio.run(scenario())


def test_upload_limits_and_content_sniffing_fail_closed(tmp_path: Path) -> None:
    async def scenario() -> None:
        app, _dispatcher = _app(tmp_path)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            project_id = (await client.get("/v1/cowork/chat/projects")).json()["projects"][0][
                "project_id"
            ]
            invalid = await client.post(
                f"/v1/cowork/chat/projects/{project_id}/documents",
                files={"file": ("fake.pdf", b"not a document", "application/pdf")},
            )
            assert invalid.status_code == 415
            too_large = await client.post(
                f"/v1/cowork/chat/projects/{project_id}/documents",
                files={"file": ("large.pdf", b"%PDF-" + b"x" * 1024, "application/pdf")},
            )
            assert too_large.status_code == 413

    asyncio.run(scenario())


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
