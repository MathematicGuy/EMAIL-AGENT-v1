from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
from fastapi import FastAPI, Request

from cowork_agent.api.projects import create_project_router
from cowork_agent.composition import ChatRuntime, CoworkRuntime, EmailRagRuntime, MailboxRuntime
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
        assert principal.user_id == "user-1"
        return Project("project-2", "workspace-1", "user-1", name, False)

    async def default_project(self, principal: VerifiedPrincipal) -> Project:
        return self.project

    async def list_for(self, principal: VerifiedPrincipal) -> tuple[Project, ...]:
        return (self.project,)

    async def require_project(
        self, principal: VerifiedPrincipal, project_id: str
    ) -> Project | None:
        return self.project if project_id == "project-1" else None

    async def create_or_get_document(self, **kwargs: object) -> tuple[ProjectDocument, bool]:
        del kwargs
        self.document = ProjectDocument(
            "doc-1",
            "project-1",
            "workspace-1",
            "user-1",
            "plan.pdf",
            "application/pdf",
            20,
            "a" * 64,
            "workspace/workspace-1/user/user-1/project/project-1/document/doc-1/source",
            "received",
            datetime.now(UTC) + timedelta(days=30),
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

    async def begin_deletion(
        self, principal: VerifiedPrincipal, project_id: str, document_id: str
    ) -> ProjectDocument | None:
        document = await self.require_document(principal, project_id, document_id)
        if document is None:
            return None
        self.document = replace(document, status="deleting")
        return self.document

    async def begin_project_deletion(
        self, principal: VerifiedPrincipal, project_id: str
    ) -> tuple[Project, Project | None, tuple[str, ...]] | None:
        project = await self.require_project(principal, project_id)
        return None if project is None else (project, None, ())

    async def worker_heartbeat_is_fresh(self, *, max_age_seconds: int) -> bool:
        assert max_age_seconds == 120
        return True


class Storage:
    async def create_signed_upload_url(self, object_key: str) -> str:
        assert object_key.endswith("/source")
        return "https://storage.example/upload-token"

    async def create_signed_download_url(self, object_key: str, expires_in: int) -> str:
        assert object_key.endswith("/source") and expires_in == 60
        return "https://storage.example/download-token"

    async def object_exists(self, object_key: str) -> bool:
        return object_key.endswith("/source")


def _runtime_with(
    *,
    project_repository: object,
    private_storage: object,
    user_documents_settings: object,
    chat_principal_resolver: object,
    project_document_vectors: object = None,
    chat_routing_service: object = None,
) -> CoworkRuntime:
    """Inject only the fields the project routes read through the runtime seam.

    Unlisted group fields stay None: these tests exercise the routes' reads,
    not group composition, and the routes treat every absent field as the
    same 503 the old missing ``app.state`` keys produced.
    """

    return CoworkRuntime(
        reports=None,
        control_plane=SimpleNamespace(project_repository=project_repository),
        mailbox=MailboxRuntime(
            gmail_settings=None,
            gmail_connections=None,
            gmail_mailbox=None,
            outlook_connections=None,
            outlook_mailbox=None,
            outlook_settings=None,
            outlook_configuration_error=None,
            provider_availability={},
            mailbox=None,
            user_documents_settings=None,
            private_storage_client=None,
            private_storage=private_storage,
        ),
        chat=ChatRuntime(
            chat_memory_settings=None,
            chat_sessions=None,
            chat_session_buffer=None,
            memory_metrics=None,
            memory_operation_sink=None,
            user_documents_settings=user_documents_settings,
            ready_document_catalog=None,
            chat_principal_resolver=chat_principal_resolver,
            chat_guest_session_issuer=None,
            chat_reply=None,
            chat_intent_settings=None,
            chat_routing_service=chat_routing_service,
        ),
        email_rag=EmailRagRuntime(
            semantic_memory=None,
            knowledge_documents=(),
            digest_worker=None,
            llm_configuration_error=None,
            llm_provider_label="none",
            document_embeddings_configured=False,
            project_document_vectors=project_document_vectors,
            project_document_index=None,
        ),
    )


def test_canonical_signed_upload_status_download_and_authorization() -> None:
    async def scenario() -> None:
        async def principal(request: Request) -> VerifiedPrincipal:
            del request
            return VerifiedPrincipal(user_id="user-1")

        app = FastAPI()
        app.include_router(create_project_router())
        app.state.runtime = _runtime_with(
            project_repository=Projects(),
            private_storage=Storage(),
            user_documents_settings=SimpleNamespace(
                enabled=True,
                retention_days=30,
                max_file_bytes=25 * 1024 * 1024,
                max_documents_per_project=50,
                max_project_bytes=500 * 1024 * 1024,
            ),
            chat_principal_resolver=principal,
            project_document_vectors=object(),
            chat_routing_service=object(),
        )
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
            status = await client.get(
                "/v1/cowork/chat/projects/project-1/documents/doc-1"
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
                    "filename": "plan.pdf",
                    "media_type": "application/pdf",
                    "byte_size": 20,
                    "content_sha256": "a" * 64,
                },
            )

        assert created.status_code == 201
        assert upload.status_code == 202
        assert upload.json()["upload_url"] == "https://storage.example/upload-token"
        assert status.status_code == 200 and status.json()["filename"] == "plan.pdf"
        assert download.json()["download_url"] == "https://storage.example/download-token"
        assert completed.json() == {"document_id": "doc-1", "status": "received"}
        assert foreign.status_code == 404
        assert "secret" not in upload.text

    asyncio.run(scenario())


def test_document_routes_are_unavailable_when_feature_is_disabled() -> None:
    async def scenario() -> None:
        async def principal(request: Request) -> VerifiedPrincipal:
            del request
            return VerifiedPrincipal(user_id="user-1")

        app = FastAPI()
        app.include_router(create_project_router())
        projects = Projects()
        app.state.runtime = _runtime_with(
            project_repository=projects,
            private_storage=Storage(),
            user_documents_settings=SimpleNamespace(enabled=False),
            chat_principal_resolver=principal,
        )
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
            documents = await client.get(
                "/v1/cowork/chat/projects/project-1/documents"
            )
            document_path = "/v1/cowork/chat/projects/project-1/documents/doc-1"
            document_responses = [
                await client.get(document_path),
                await client.post(f"{document_path}/complete"),
                await client.delete(document_path),
                await client.get(f"{document_path}/download"),
            ]

        assert created.status_code == 201
        assert upload.status_code == 503
        assert upload.json() == {"detail": "User documents are disabled"}
        assert documents.status_code == 503
        assert all(response.status_code == 503 for response in document_responses)
        assert projects.document is None

    asyncio.run(scenario())
