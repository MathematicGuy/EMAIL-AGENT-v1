"""Project/document metadata repositories; bytes never pass through this layer."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import NAMESPACE_URL, uuid4, uuid5

from psycopg_pool import AsyncConnectionPool

from cowork_agent.identity import VerifiedPrincipal

_DOCUMENT_NAMESPACE = uuid5(NAMESPACE_URL, "cowork-agent/project-document")


@dataclass(frozen=True, slots=True)
class Project:
    id: str
    workspace_id: str
    owner_user_id: str
    name: str
    is_default: bool


@dataclass(frozen=True, slots=True)
class ProjectDocument:
    id: str
    project_id: str
    workspace_id: str
    user_id: str
    filename: str
    media_type: str
    byte_size: int
    content_sha256: str
    storage_key: str
    status: str
    expires_at: datetime


class PostgresProjectRepository:
    """Authoritative per-user project and document metadata repository."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def default_project(self, principal: VerifiedPrincipal) -> Project:
        existing = await self._one_project(principal, is_default=True)
        if existing is not None:
            return existing
        project_id = str(uuid4())
        async with self._pool.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO projects (id, workspace_id, owner_user_id, name, is_default)
                    SELECT %s, workspace_id, user_id, 'Default project', true
                    FROM workspace_members
                    WHERE workspace_id = %s AND user_id = %s
                    ON CONFLICT (workspace_id, owner_user_id) WHERE is_default DO NOTHING
                    """,
                    (project_id, principal.workspace_id, principal.user_id),
                )
        project = await self._one_project(principal, is_default=True)
        if project is None:
            raise LookupError("Principal is not an active workspace member")
        return project

    async def list_for(self, principal: VerifiedPrincipal) -> tuple[Project, ...]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT id, workspace_id, owner_user_id, name, is_default
                FROM projects
                WHERE workspace_id = %s AND owner_user_id = %s
                ORDER BY is_default DESC, created_at, id
                """,
                (principal.workspace_id, principal.user_id),
            )
            rows = await cursor.fetchall()
        return tuple(_project(row) for row in rows)

    async def create(self, principal: VerifiedPrincipal, name: str) -> Project:
        normalized_name = name.strip()
        if not normalized_name or len(normalized_name) > 200:
            raise ValueError("project name must be between 1 and 200 characters")
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                INSERT INTO projects (id, workspace_id, owner_user_id, name)
                SELECT %s, workspace_id, user_id, %s
                FROM workspace_members WHERE workspace_id = %s AND user_id = %s
                RETURNING id, workspace_id, owner_user_id, name, is_default
                """,
                (str(uuid4()), normalized_name, principal.workspace_id, principal.user_id),
            )
            row = await cursor.fetchone()
        if row is None:
            raise LookupError("Principal is not an active workspace member")
        return _project(row)

    async def require_project(
        self, principal: VerifiedPrincipal, project_id: str
    ) -> Project | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT id, workspace_id, owner_user_id, name, is_default
                FROM projects
                WHERE id = %s AND workspace_id = %s AND owner_user_id = %s
                """,
                (project_id, principal.workspace_id, principal.user_id),
            )
            row = await cursor.fetchone()
        return None if row is None else _project(row)

    async def create_or_get_document(
        self,
        *,
        principal: VerifiedPrincipal,
        project_id: str,
        filename: str,
        media_type: str,
        byte_size: int,
        content_sha256: str,
        expires_in_seconds: int,
    ) -> tuple[ProjectDocument, bool]:
        project = await self.require_project(principal, project_id)
        if project is None:
            raise LookupError("Project not found")
        if media_type not in {
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }:
            raise ValueError("unsupported_media_type")
        if byte_size <= 0:
            raise ValueError("byte_size must be positive")
        if len(content_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in content_sha256
        ):
            raise ValueError("content_sha256 must be a lowercase SHA-256 digest")
        document_identity = (
            f"{project.workspace_id}/{principal.user_id}/{project_id}/{content_sha256}"
        )
        document_id = str(uuid5(_DOCUMENT_NAMESPACE, document_identity))
        storage_key = (
            f"workspace/{project.workspace_id}/user/{principal.user_id}/project/{project_id}"
            f"/document/{document_id}/source"
        )
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                INSERT INTO project_documents (
                    id, workspace_id, user_id, project_id, filename, media_type, byte_size,
                    content_sha256, storage_key, status, expires_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'received', %s)
                ON CONFLICT (project_id, content_sha256) DO NOTHING
                RETURNING id, project_id, workspace_id, user_id, filename, media_type, byte_size,
                    content_sha256, storage_key, status, expires_at
                """,
                (
                    document_id,
                    project.workspace_id,
                    principal.user_id,
                    project_id,
                    filename.strip(),
                    media_type,
                    byte_size,
                    content_sha256,
                    storage_key,
                    datetime.now(UTC) + timedelta(seconds=expires_in_seconds),
                ),
            )
            row = await cursor.fetchone()
            if row is not None:
                return _document(row), True
            cursor = await connection.execute(
                """
                SELECT id, project_id, workspace_id, user_id, filename, media_type, byte_size,
                    content_sha256, storage_key, status, expires_at
                FROM project_documents
                WHERE project_id = %s AND content_sha256 = %s
                """,
                (project_id, content_sha256),
            )
            existing = await cursor.fetchone()
        assert existing is not None
        return _document(existing), False

    async def require_document(
        self, principal: VerifiedPrincipal, project_id: str, document_id: str
    ) -> ProjectDocument | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT id, project_id, workspace_id, user_id, filename, media_type, byte_size,
                    content_sha256, storage_key, status, expires_at
                FROM project_documents
                WHERE id = %s AND project_id = %s AND workspace_id = %s AND user_id = %s
                """,
                (document_id, project_id, principal.workspace_id, principal.user_id),
            )
            row = await cursor.fetchone()
        return None if row is None else _document(row)

    async def list_documents(
        self, principal: VerifiedPrincipal, project_id: str
    ) -> tuple[ProjectDocument, ...]:
        if await self.require_project(principal, project_id) is None:
            return ()
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT id, project_id, workspace_id, user_id, filename, media_type, byte_size,
                    content_sha256, storage_key, status, expires_at
                FROM project_documents
                WHERE project_id = %s AND workspace_id = %s AND user_id = %s
                ORDER BY created_at DESC, id
                """,
                (project_id, principal.workspace_id, principal.user_id),
            )
            rows = await cursor.fetchall()
        return tuple(_document(row) for row in rows)

    async def _one_project(
        self, principal: VerifiedPrincipal, *, is_default: bool
    ) -> Project | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT id, workspace_id, owner_user_id, name, is_default
                FROM projects
                WHERE workspace_id = %s AND owner_user_id = %s AND is_default = %s
                """,
                (principal.workspace_id, principal.user_id, is_default),
            )
            row = await cursor.fetchone()
        return None if row is None else _project(row)


def _project(row: tuple[object, ...]) -> Project:
    return Project(str(row[0]), str(row[1]), str(row[2]), str(row[3]), bool(row[4]))


def _document(row: tuple[object, ...]) -> ProjectDocument:
    return ProjectDocument(
        id=str(row[0]),
        project_id=str(row[1]),
        workspace_id=str(row[2]),
        user_id=str(row[3]),
        filename=str(row[4]),
        media_type=str(row[5]),
        byte_size=int(cast(int, row[6])),
        content_sha256=str(row[7]),
        storage_key=str(row[8]),
        status=str(row[9]),
        expires_at=(
            row[10]
            if isinstance(row[10], datetime)
            else datetime.fromisoformat(str(row[10]))
        ),
    )
