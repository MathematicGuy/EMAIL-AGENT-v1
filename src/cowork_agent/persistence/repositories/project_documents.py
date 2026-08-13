"""In-memory and PostgreSQL repositories for Project-scoped documents."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from psycopg_pool import AsyncConnectionPool

from cowork_agent.domain.chat_contracts import ChatMemoryScope
from cowork_agent.domain.project_documents import (
    ChatProject,
    ProjectDocument,
    ProjectDocumentFailureReason,
    ProjectDocumentMediaType,
    ProjectDocumentStatus,
    can_transition_document,
    deterministic_default_project_id,
)

_DOC_COLUMNS = (
    "document_id, project_id, tenant_id, user_id, title, media_type, size_bytes, sha256,"
    " status, reason_code, page_count, chunk_count, ocr_page_count, created_at, updated_at,"
    " expires_at"
)


class InMemoryProjectRepository:
    def __init__(self) -> None:
        self._items: dict[str, ChatProject] = {}
        self._deleted_items: dict[str, ChatProject] = {}
        self._lock = asyncio.Lock()

    async def resolve_default(self, tenant_id: str, user_id: str) -> ChatProject:
        from datetime import UTC

        async with self._lock:
            for project in self._items.values():
                if (
                    project.tenant_id == tenant_id
                    and project.user_id == user_id
                    and project.is_default
                ):
                    return project
            now = datetime.now(UTC)
            project = ChatProject(
                deterministic_default_project_id(tenant_id, user_id),
                tenant_id,
                user_id,
                "Default Project",
                True,
                now,
                now,
            )
            self._items[project.project_id] = project
            self._deleted_items.pop(project.project_id, None)
            return project

    async def create(self, tenant_id: str, user_id: str, name: str) -> ChatProject:
        from datetime import UTC
        from uuid import uuid4

        now = datetime.now(UTC)
        project = ChatProject(
            f"project_{uuid4().hex}", tenant_id, user_id, name.strip(), False, now, now
        )
        async with self._lock:
            self._items[project.project_id] = project
        return project

    async def get_owned(
        self,
        tenant_id: str,
        user_id: str,
        project_id: str,
        *,
        include_deleted: bool = False,
    ) -> ChatProject | None:
        item = self._items.get(project_id)
        if item is None and include_deleted:
            item = self._deleted_items.get(project_id)
        return item if item and item.tenant_id == tenant_id and item.user_id == user_id else None

    async def list_owned(self, tenant_id: str, user_id: str) -> tuple[ChatProject, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._items.values()
                    if item.tenant_id == tenant_id and item.user_id == user_id
                ),
                key=lambda item: (not item.is_default, item.created_at, item.project_id),
            )
        )

    async def delete_owned(
        self, tenant_id: str, user_id: str, project_id: str, *, at: datetime
    ) -> tuple[bool, ChatProject | None]:
        async with self._lock:
            item = self._items.get(project_id)
            if item is None or item.tenant_id != tenant_id or item.user_id != user_id:
                deleted = self._deleted_items.get(project_id)
                if deleted and deleted.tenant_id == tenant_id and deleted.user_id == user_id:
                    return True, None
                return False, None
            del self._items[project_id]
            self._deleted_items[project_id] = item
        replacement = await self.resolve_default(tenant_id, user_id) if item.is_default else None
        return True, replacement


class InMemoryProjectDocumentRepository:
    def __init__(self) -> None:
        self._items: dict[str, ProjectDocument] = {}
        self._leases: dict[str, tuple[str, datetime]] = {}
        self._cleanup_pending: set[str] = set()
        self._lock = asyncio.Lock()

    async def create_or_get(self, **values: object) -> tuple[ProjectDocument, bool]:
        async with self._lock:
            for item in self._items.values():
                if (
                    item.tenant_id == values["tenant_id"]
                    and item.user_id == values["user_id"]
                    and item.project_id == values["project_id"]
                    and item.sha256 == values["sha256"]
                    and item.status is not ProjectDocumentStatus.DELETED
                ):
                    return item, False
            item = ProjectDocument(
                document_id=cast(str, values["document_id"]),
                project_id=cast(str, values["project_id"]),
                tenant_id=cast(str, values["tenant_id"]),
                user_id=cast(str, values["user_id"]),
                title=cast(str, values["title"]),
                media_type=cast(ProjectDocumentMediaType, values["media_type"]),
                size_bytes=cast(int, values["size_bytes"]),
                sha256=cast(str, values["sha256"]),
                status=ProjectDocumentStatus.RECEIVED,
                reason_code=None,
                page_count=0,
                chunk_count=0,
                ocr_page_count=0,
                created_at=cast(datetime, values["created_at"]),
                updated_at=cast(datetime, values["created_at"]),
                expires_at=cast(datetime, values["expires_at"]),
            )
            self._items[item.document_id] = item
            return item, True

    async def get_job(self, document_id: str) -> ProjectDocument | None:
        item = self._items.get(document_id)
        return item if item and item.status is not ProjectDocumentStatus.DELETED else None

    async def get_owned(
        self, tenant_id: str, user_id: str, project_id: str, document_id: str
    ) -> ProjectDocument | None:
        item = self._items.get(document_id)
        return (
            item
            if item
            and item.tenant_id == tenant_id
            and item.user_id == user_id
            and item.project_id == project_id
            and item.status is not ProjectDocumentStatus.DELETED
            else None
        )

    async def list_owned(
        self, tenant_id: str, user_id: str, project_id: str, *, include_deleted: bool = False
    ) -> tuple[ProjectDocument, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._items.values()
                    if item.tenant_id == tenant_id
                    and item.user_id == user_id
                    and item.project_id == project_id
                    and (include_deleted or item.status is not ProjectDocumentStatus.DELETED)
                ),
                key=lambda item: (item.created_at, item.document_id),
                reverse=True,
            )
        )

    async def list_ready(
        self, tenant_id: str, user_id: str, project_id: str, *, at: datetime
    ) -> tuple[ProjectDocument, ...]:
        return tuple(
            item
            for item in await self.list_owned(tenant_id, user_id, project_id)
            if item.status is ProjectDocumentStatus.READY and item.expires_at > at
        )

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
    ) -> ProjectDocument | None:
        async with self._lock:
            item = self._items.get(document_id)
            if (
                item is None
                or item.status not in from_statuses
                or not can_transition_document(item.status, to_status)
            ):
                return None
            updated = replace(
                item,
                status=to_status,
                reason_code=reason_code,
                page_count=item.page_count if page_count is None else page_count,
                chunk_count=item.chunk_count if chunk_count is None else chunk_count,
                ocr_page_count=item.ocr_page_count if ocr_page_count is None else ocr_page_count,
                updated_at=at,
            )
            self._items[document_id] = updated
            if to_status in {
                ProjectDocumentStatus.READY,
                ProjectDocumentStatus.FAILED,
                ProjectDocumentStatus.DELETED,
            }:
                self._leases.pop(document_id, None)
            if to_status is ProjectDocumentStatus.DELETED:
                self._cleanup_pending.add(document_id)
            return updated

    async def claim(
        self, document_id: str, *, worker_id: str, now: datetime, lease_until: datetime
    ) -> bool:
        async with self._lock:
            item = self._items.get(document_id)
            lease = self._leases.get(document_id)
            if item is None or item.status in {
                ProjectDocumentStatus.READY,
                ProjectDocumentStatus.FAILED,
                ProjectDocumentStatus.DELETED,
            }:
                return False
            if lease is not None and lease[1] > now:
                return False
            self._leases[document_id] = (worker_id, lease_until)
            return True

    async def reclaimable(self, *, now: datetime, limit: int = 100) -> tuple[str, ...]:
        return tuple(
            item.document_id
            for item in self._items.values()
            if item.status
            in {
                ProjectDocumentStatus.RECEIVED,
                ProjectDocumentStatus.EXTRACTING,
                ProjectDocumentStatus.INDEXING,
            }
            and (item.document_id not in self._leases or self._leases[item.document_id][1] <= now)
        )[:limit]

    async def mark_project_deleted(
        self, tenant_id: str, user_id: str, project_id: str, *, at: datetime
    ) -> tuple[str, ...]:
        ids: list[str] = []
        for item in await self.list_owned(tenant_id, user_id, project_id):
            if await self.transition(
                item.document_id,
                from_statuses=(item.status,),
                to_status=ProjectDocumentStatus.DELETED,
                at=at,
            ):
                ids.append(item.document_id)
        return tuple(ids)

    async def mark_expired_deleted(self, *, at: datetime, limit: int = 100) -> tuple[str, ...]:
        ids: list[str] = []
        for item in tuple(self._items.values()):
            if len(ids) >= limit:
                break
            if item.status is ProjectDocumentStatus.DELETED or item.expires_at > at:
                continue
            if await self.transition(
                item.document_id,
                from_statuses=(item.status,),
                to_status=ProjectDocumentStatus.DELETED,
                at=at,
            ):
                ids.append(item.document_id)
        return tuple(ids)

    async def cleanup_candidates(self, *, limit: int = 100) -> tuple[str, ...]:
        return tuple(sorted(self._cleanup_pending))[:limit]

    async def confirm_cleanup(self, document_id: str, *, at: datetime) -> None:
        del at
        self._cleanup_pending.discard(document_id)


class PostgresProjectRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def resolve_default(self, tenant_id: str, user_id: str) -> ChatProject:
        project_id = deterministic_default_project_id(tenant_id, user_id)
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                INSERT INTO user_chat_projects
                    (project_id, tenant_id, user_id, name, is_default, created_at, updated_at)
                VALUES (%s, %s, %s, 'Default Project', true, now(), now())
                ON CONFLICT (project_id) DO UPDATE SET
                    name = 'Default Project', is_default = true,
                    deleted_at = NULL, updated_at = EXCLUDED.updated_at
                RETURNING project_id, tenant_id, user_id, name, is_default, created_at, updated_at
                """,
                (project_id, tenant_id, user_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise RuntimeError("default project upsert returned no row")
            return _project(row)

    async def create(self, tenant_id: str, user_id: str, name: str) -> ChatProject:
        from uuid import uuid4

        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """INSERT INTO user_chat_projects
                (project_id, tenant_id, user_id, name, is_default, created_at, updated_at)
                VALUES (%s, %s, %s, %s, false, now(), now())
                RETURNING project_id, tenant_id, user_id, name, is_default,
                    created_at, updated_at""",
                (f"project_{uuid4().hex}", tenant_id, user_id, name.strip()),
            )
            row = await cursor.fetchone()
            if row is None:
                raise RuntimeError("project insert returned no row")
            return _project(row)

    async def get_owned(
        self,
        tenant_id: str,
        user_id: str,
        project_id: str,
        *,
        include_deleted: bool = False,
    ) -> ChatProject | None:
        active_clause = "" if include_deleted else "AND deleted_at IS NULL"
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                f"""SELECT project_id, tenant_id, user_id, name, is_default, created_at, updated_at
                FROM user_chat_projects WHERE tenant_id=%s AND user_id=%s AND project_id=%s
                {active_clause}""",
                (tenant_id, user_id, project_id),
            )
            row = await cursor.fetchone()
        return None if row is None else _project(row)

    async def list_owned(self, tenant_id: str, user_id: str) -> tuple[ChatProject, ...]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT project_id, tenant_id, user_id, name, is_default, created_at, updated_at
                FROM user_chat_projects WHERE tenant_id=%s AND user_id=%s AND deleted_at IS NULL
                ORDER BY is_default DESC, created_at, project_id""",
                (tenant_id, user_id),
            )
            return tuple(_project(row) for row in await cursor.fetchall())

    async def delete_owned(
        self, tenant_id: str, user_id: str, project_id: str, *, at: datetime
    ) -> tuple[bool, ChatProject | None]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """UPDATE user_chat_projects SET deleted_at=%s, is_default=false, updated_at=%s
                WHERE tenant_id=%s AND user_id=%s AND project_id=%s AND deleted_at IS NULL
                RETURNING is_default""",
                (at, at, tenant_id, user_id, project_id),
            )
            row = await cursor.fetchone()
        if row is None:
            return False, None
        # RETURNING sees the new value, so use deterministic identity to decide replacement.
        replacement = (
            await self.resolve_default(tenant_id, user_id)
            if project_id == deterministic_default_project_id(tenant_id, user_id)
            else None
        )
        return True, replacement


class PostgresChatSessionRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def create(
        self,
        *,
        tenant_id: str,
        user_id: str,
        project_id: str = "default-project",
    ) -> ChatMemoryScope:
        resolved_project_id = (
            deterministic_default_project_id(tenant_id, user_id)
            if project_id == "default-project"
            else project_id
        )
        scope = ChatMemoryScope(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=str(uuid4()),
            project_id=resolved_project_id,
        )
        await self.save(scope, created_at=datetime.now(UTC))
        return scope

    async def save(self, scope: ChatMemoryScope, *, created_at: datetime) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                """INSERT INTO user_chat_sessions
                (session_id, tenant_id, user_id, project_id, created_at, deleted_at)
                VALUES (%s,%s,%s,%s,%s,NULL)
                ON CONFLICT (session_id) DO NOTHING""",
                (
                    scope.session_id,
                    scope.tenant_id,
                    scope.user_id,
                    scope.project_id,
                    created_at,
                ),
            )

    async def get_owned(
        self, tenant_id: str, user_id: str, session_id: str
    ) -> ChatMemoryScope | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT tenant_id, user_id, session_id, project_id FROM user_chat_sessions
                WHERE tenant_id=%s AND user_id=%s AND session_id=%s AND deleted_at IS NULL""",
                (tenant_id, user_id, session_id),
            )
            row = await cursor.fetchone()
        return (
            None
            if row is None
            else ChatMemoryScope(
                tenant_id=str(row[0]),
                user_id=str(row[1]),
                session_id=str(row[2]),
                project_id=str(row[3]),
            )
        )

    async def require(
        self, session_id: str, *, tenant_id: str, user_id: str
    ) -> ChatMemoryScope:
        from cowork_agent.features.ai_chat.controller import ChatSessionAccessDenied

        scope = await self.get_owned(tenant_id, user_id, session_id)
        if scope is None:
            raise ChatSessionAccessDenied(session_id)
        return scope

    async def list_owned(
        self, tenant_id: str, user_id: str, project_id: str | None = None
    ) -> tuple[ChatMemoryScope, ...]:
        project_clause = "" if project_id is None else "AND project_id=%s"
        params: tuple[object, ...] = (
            (tenant_id, user_id) if project_id is None else (tenant_id, user_id, project_id)
        )
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                f"""SELECT tenant_id, user_id, session_id, project_id FROM user_chat_sessions
                WHERE tenant_id=%s AND user_id=%s AND deleted_at IS NULL {project_clause}
                ORDER BY created_at, session_id""",
                params,
            )
            return tuple(
                ChatMemoryScope(
                    tenant_id=str(row[0]),
                    user_id=str(row[1]),
                    session_id=str(row[2]),
                    project_id=str(row[3]),
                )
                for row in await cursor.fetchall()
            )

    async def list_for(
        self, *, tenant_id: str, user_id: str, project_id: str | None = None
    ) -> tuple[ChatMemoryScope, ...]:
        return await self.list_owned(tenant_id, user_id, project_id)

    async def delete_project(
        self,
        *,
        tenant_id: str,
        user_id: str,
        project_id: str,
        at: datetime | None = None,
    ) -> tuple[str, ...]:
        deleted_at = at or datetime.now(UTC)
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """UPDATE user_chat_sessions SET deleted_at=%s
                WHERE tenant_id=%s AND user_id=%s AND project_id=%s AND deleted_at IS NULL
                RETURNING session_id""",
                (deleted_at, tenant_id, user_id, project_id),
            )
            return tuple(str(row[0]) for row in await cursor.fetchall())


class PostgresProjectDocumentRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def create_or_get(self, **values: object) -> tuple[ProjectDocument, bool]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                f"""INSERT INTO user_project_documents
                (document_id, project_id, tenant_id, user_id, title, media_type, size_bytes,
                 sha256, status, reason_code, created_at, updated_at, expires_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'received',NULL,%s,%s,%s)
                ON CONFLICT (tenant_id, user_id, project_id, sha256) WHERE status <> 'deleted'
                DO UPDATE SET updated_at = user_project_documents.updated_at
                RETURNING {_DOC_COLUMNS}, (xmax = 0) AS inserted""",
                (
                    values["document_id"],
                    values["project_id"],
                    values["tenant_id"],
                    values["user_id"],
                    values["title"],
                    cast(ProjectDocumentMediaType, values["media_type"]).value,
                    values["size_bytes"],
                    values["sha256"],
                    values["created_at"],
                    values["created_at"],
                    values["expires_at"],
                ),
            )
            row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("document upsert returned no row")
        return _document(row[:16]), bool(row[16])

    async def get_job(self, document_id: str) -> ProjectDocument | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                f"""SELECT {_DOC_COLUMNS} FROM user_project_documents
                WHERE document_id=%s AND status <> 'deleted'""",
                (document_id,),
            )
            row = await cursor.fetchone()
        return None if row is None else _document(row)

    async def get_owned(
        self, tenant_id: str, user_id: str, project_id: str, document_id: str
    ) -> ProjectDocument | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                f"""SELECT {_DOC_COLUMNS} FROM user_project_documents
                WHERE tenant_id=%s AND user_id=%s AND project_id=%s
                    AND document_id=%s AND status <> 'deleted'""",
                (tenant_id, user_id, project_id, document_id),
            )
            row = await cursor.fetchone()
        return None if row is None else _document(row)

    async def list_owned(
        self, tenant_id: str, user_id: str, project_id: str, *, include_deleted: bool = False
    ) -> tuple[ProjectDocument, ...]:
        suffix = "" if include_deleted else "AND status <> 'deleted'"
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                f"""SELECT {_DOC_COLUMNS} FROM user_project_documents
                WHERE tenant_id=%s AND user_id=%s AND project_id=%s {suffix}
                ORDER BY created_at DESC, document_id DESC""",
                (tenant_id, user_id, project_id),
            )
            return tuple(_document(row) for row in await cursor.fetchall())

    async def list_ready(
        self, tenant_id: str, user_id: str, project_id: str, *, at: datetime
    ) -> tuple[ProjectDocument, ...]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                f"""SELECT {_DOC_COLUMNS} FROM user_project_documents
                WHERE tenant_id=%s AND user_id=%s AND project_id=%s
                    AND status='ready' AND expires_at > %s
                ORDER BY created_at DESC""",
                (tenant_id, user_id, project_id, at),
            )
            return tuple(_document(row) for row in await cursor.fetchall())

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
    ) -> ProjectDocument | None:
        if not all(can_transition_document(status, to_status) for status in from_statuses):
            raise ValueError("invalid document transition")
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                f"""UPDATE user_project_documents SET status=%s, reason_code=%s, updated_at=%s,
                page_count=COALESCE(%s,page_count), chunk_count=COALESCE(%s,chunk_count),
                ocr_page_count=COALESCE(%s,ocr_page_count),
                lease_owner=CASE WHEN %s IN ('ready','failed','deleted')
                    THEN NULL ELSE lease_owner END,
                lease_expires_at=CASE WHEN %s IN ('ready','failed','deleted')
                    THEN NULL ELSE lease_expires_at END,
                deleted_at=CASE WHEN %s='deleted' THEN %s ELSE deleted_at END,
                cleanup_completed_at=CASE WHEN %s='deleted' THEN NULL ELSE cleanup_completed_at END
                WHERE document_id=%s AND status=ANY(%s) RETURNING {_DOC_COLUMNS}""",
                (
                    to_status.value,
                    reason_code.value if reason_code else None,
                    at,
                    page_count,
                    chunk_count,
                    ocr_page_count,
                    to_status.value,
                    to_status.value,
                    to_status.value,
                    at,
                    to_status.value,
                    document_id,
                    [item.value for item in from_statuses],
                ),
            )
            row = await cursor.fetchone()
        return None if row is None else _document(row)

    async def claim(
        self, document_id: str, *, worker_id: str, now: datetime, lease_until: datetime
    ) -> bool:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """UPDATE user_project_documents SET lease_owner=%s, lease_expires_at=%s,
                attempt_count=attempt_count+1 WHERE document_id=%s
                AND status IN ('received','extracting','indexing')
                AND (lease_expires_at IS NULL OR lease_expires_at <= %s) RETURNING document_id""",
                (worker_id, lease_until, document_id, now),
            )
            return await cursor.fetchone() is not None

    async def reclaimable(self, *, now: datetime, limit: int = 100) -> tuple[str, ...]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT document_id FROM user_project_documents
                WHERE status IN ('received','extracting','indexing')
                AND (lease_expires_at IS NULL OR lease_expires_at <= %s)
                ORDER BY updated_at LIMIT %s""",
                (now, limit),
            )
            return tuple(str(row[0]) for row in await cursor.fetchall())

    async def mark_project_deleted(
        self, tenant_id: str, user_id: str, project_id: str, *, at: datetime
    ) -> tuple[str, ...]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """UPDATE user_project_documents SET status='deleted', reason_code=NULL,
                deleted_at=%s, updated_at=%s, lease_owner=NULL, lease_expires_at=NULL,
                cleanup_completed_at=NULL
                WHERE tenant_id=%s AND user_id=%s AND project_id=%s AND status <> 'deleted'
                RETURNING document_id""",
                (at, at, tenant_id, user_id, project_id),
            )
            return tuple(str(row[0]) for row in await cursor.fetchall())

    async def mark_expired_deleted(self, *, at: datetime, limit: int = 100) -> tuple[str, ...]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """WITH expired AS (
                    SELECT document_id FROM user_project_documents
                    WHERE status <> 'deleted' AND expires_at <= %s
                    ORDER BY expires_at FOR UPDATE SKIP LOCKED LIMIT %s
                )
                UPDATE user_project_documents AS document SET status='deleted', reason_code=NULL,
                    deleted_at=%s, updated_at=%s, lease_owner=NULL, lease_expires_at=NULL,
                    cleanup_completed_at=NULL
                FROM expired WHERE document.document_id=expired.document_id
                RETURNING document.document_id""",
                (at, limit, at, at),
            )
            return tuple(str(row[0]) for row in await cursor.fetchall())

    async def cleanup_candidates(self, *, limit: int = 100) -> tuple[str, ...]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT document_id FROM user_project_documents
                WHERE status='deleted' AND cleanup_completed_at IS NULL
                ORDER BY deleted_at NULLS FIRST, document_id LIMIT %s""",
                (limit,),
            )
            return tuple(str(row[0]) for row in await cursor.fetchall())

    async def confirm_cleanup(self, document_id: str, *, at: datetime) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                """UPDATE user_project_documents SET cleanup_completed_at=%s
                WHERE document_id=%s AND status='deleted'""",
                (at, document_id),
            )


def _project(row: Sequence[object]) -> ChatProject:
    return ChatProject(
        str(row[0]),
        str(row[1]),
        str(row[2]),
        str(row[3]),
        bool(row[4]),
        cast(datetime, row[5]),
        cast(datetime, row[6]),
    )


def _document(row: Sequence[object]) -> ProjectDocument:
    return ProjectDocument(
        document_id=str(row[0]),
        project_id=str(row[1]),
        tenant_id=str(row[2]),
        user_id=str(row[3]),
        title=str(row[4]),
        media_type=ProjectDocumentMediaType(str(row[5])),
        size_bytes=int(cast(int, row[6])),
        sha256=str(row[7]),
        status=ProjectDocumentStatus(str(row[8])),
        reason_code=None if row[9] is None else ProjectDocumentFailureReason(str(row[9])),
        page_count=int(cast(int, row[10])),
        chunk_count=int(cast(int, row[11])),
        ocr_page_count=int(cast(int, row[12])),
        created_at=cast(datetime, row[13]),
        updated_at=cast(datetime, row[14]),
        expires_at=cast(datetime, row[15]),
    )
