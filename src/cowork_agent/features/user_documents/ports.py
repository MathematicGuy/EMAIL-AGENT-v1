"""Ports for the durable Project and user-document control plane."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from cowork_agent.domain.chat_contracts import ChatMemoryScope
from cowork_agent.domain.project_documents import (
    ChatProject,
    ProjectDocument,
    ProjectDocumentFailureReason,
    ProjectDocumentMediaType,
    ProjectDocumentQuery,
    ProjectDocumentResponse,
    ProjectDocumentStatus,
)


class ChatSessionRepositoryPort(Protocol):
    async def save(self, scope: ChatMemoryScope, *, created_at: datetime) -> None: ...
    async def get_owned(
        self, tenant_id: str, user_id: str, session_id: str
    ) -> ChatMemoryScope | None: ...
    async def list_owned(
        self, tenant_id: str, user_id: str, project_id: str | None = None
    ) -> tuple[ChatMemoryScope, ...]: ...
    async def delete_project(
        self, tenant_id: str, user_id: str, project_id: str, *, at: datetime
    ) -> tuple[str, ...]: ...


class ProjectRepositoryPort(Protocol):
    async def resolve_default(self, tenant_id: str, user_id: str) -> ChatProject: ...
    async def create(self, tenant_id: str, user_id: str, name: str) -> ChatProject: ...
    async def get_owned(
        self,
        tenant_id: str,
        user_id: str,
        project_id: str,
        *,
        include_deleted: bool = False,
    ) -> ChatProject | None: ...
    async def list_owned(self, tenant_id: str, user_id: str) -> tuple[ChatProject, ...]: ...
    async def delete_owned(
        self, tenant_id: str, user_id: str, project_id: str, *, at: datetime
    ) -> tuple[bool, ChatProject | None]: ...


class ProjectDocumentRepositoryPort(Protocol):
    async def get_job(self, document_id: str) -> ProjectDocument | None: ...

    async def create_or_get(
        self,
        *,
        document_id: str,
        project_id: str,
        tenant_id: str,
        user_id: str,
        title: str,
        media_type: ProjectDocumentMediaType,
        size_bytes: int,
        sha256: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> tuple[ProjectDocument, bool]: ...

    async def get_owned(
        self, tenant_id: str, user_id: str, project_id: str, document_id: str
    ) -> ProjectDocument | None: ...

    async def list_owned(
        self, tenant_id: str, user_id: str, project_id: str, *, include_deleted: bool = False
    ) -> tuple[ProjectDocument, ...]: ...

    async def list_ready(
        self, tenant_id: str, user_id: str, project_id: str, *, at: datetime
    ) -> tuple[ProjectDocument, ...]: ...

    async def transition(
        self,
        document_id: str,
        *,
        from_statuses: Sequence[ProjectDocumentStatus],
        to_status: ProjectDocumentStatus,
        at: datetime,
        reason_code: ProjectDocumentFailureReason | None = None,
        page_count: int | None = None,
        chunk_count: int | None = None,
        ocr_page_count: int | None = None,
    ) -> ProjectDocument | None: ...

    async def claim(
        self, document_id: str, *, worker_id: str, now: datetime, lease_until: datetime
    ) -> bool: ...

    async def reclaimable(self, *, now: datetime, limit: int = 100) -> tuple[str, ...]: ...

    async def mark_project_deleted(
        self, tenant_id: str, user_id: str, project_id: str, *, at: datetime
    ) -> tuple[str, ...]: ...

    async def mark_expired_deleted(self, *, at: datetime, limit: int = 100) -> tuple[str, ...]: ...

    async def cleanup_candidates(self, *, limit: int = 100) -> tuple[str, ...]: ...

    async def confirm_cleanup(self, document_id: str, *, at: datetime) -> None: ...


class ProjectDocumentRetrievalPort(Protocol):
    async def retrieve(self, request: ProjectDocumentQuery) -> ProjectDocumentResponse: ...
