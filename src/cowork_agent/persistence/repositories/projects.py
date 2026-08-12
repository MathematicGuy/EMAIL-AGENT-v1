"""Project/document metadata repositories; bytes never pass through this layer."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import NAMESPACE_URL, uuid4, uuid5

from psycopg_pool import AsyncConnectionPool

from cowork_agent.identity import VerifiedPrincipal

_DOCUMENT_NAMESPACE = uuid5(NAMESPACE_URL, "cowork-agent/project-document")
_JOB_NAMESPACE = uuid5(NAMESPACE_URL, "cowork-agent/project-document-job")


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


@dataclass(frozen=True, slots=True)
class DocumentIngestionJob:
    id: str
    document_id: str
    status: str
    attempts: int


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

    async def mark_upload_completed(
        self, principal: VerifiedPrincipal, project_id: str, document_id: str
    ) -> DocumentIngestionJob | None:
        """Create the metadata-only job after a client finishes its signed upload."""
        document = await self.require_document(principal, project_id, document_id)
        if document is None or document.status != "received":
            return None
        job_id = str(uuid5(_JOB_NAMESPACE, document.id))
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                INSERT INTO document_ingestion_jobs (id, document_id, status)
                VALUES (%s, %s, 'queued')
                ON CONFLICT (document_id) DO UPDATE
                    SET status = CASE
                        WHEN document_ingestion_jobs.status = 'failed' THEN 'queued'
                        ELSE document_ingestion_jobs.status
                    END,
                    error_code = NULL,
                    updated_at = now()
                RETURNING id, document_id, status, attempts
                """,
                (job_id, document.id),
            )
            row = await cursor.fetchone()
        return None if row is None else _job(row)

    async def claim_job(self, document_id: str) -> ProjectDocument | None:
        """Atomically move a queued document to extracting for one worker."""
        async with self._pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    UPDATE document_ingestion_jobs AS jobs
                    SET status = 'extracting', attempts = attempts + 1,
                        claimed_at = now(), updated_at = now(), error_code = NULL
                    FROM project_documents AS documents
                    WHERE jobs.document_id = documents.id
                      AND jobs.document_id = %s
                      AND jobs.status IN ('queued', 'failed')
                      AND documents.status = 'received'
                    RETURNING documents.id, documents.project_id, documents.workspace_id,
                        documents.user_id, documents.filename, documents.media_type,
                        documents.byte_size, documents.content_sha256, documents.storage_key,
                        documents.status, documents.expires_at
                    """,
                    (document_id,),
                )
                row = await cursor.fetchone()
                if row is None:
                    return None
                cursor = await connection.execute(
                    """
                    UPDATE project_documents
                    SET status = 'extracting', error_code = NULL, updated_at = now()
                    WHERE id = %s AND status = 'received'
                    RETURNING id
                    """,
                    (document_id,),
                )
                if await cursor.fetchone() is None:
                    raise RuntimeError("document state changed while claiming ingestion job")
        return _document(row[:9] + ("extracting", row[10]))

    async def next_claimable_job(self) -> str | None:
        """Return one opaque queued job ID; `claim_job` remains the CAS authority."""
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT jobs.document_id
                FROM document_ingestion_jobs AS jobs
                JOIN project_documents AS documents ON documents.id = jobs.document_id
                WHERE jobs.status IN ('queued', 'failed')
                  AND documents.status = 'received'
                  AND documents.expires_at > now()
                ORDER BY jobs.available_at, jobs.created_at, jobs.id
                LIMIT 1
                """
            )
            row = await cursor.fetchone()
        return None if row is None else str(row[0])

    async def reset_stale_jobs(self, *, claimed_before: datetime) -> int:
        """Release expired worker leases; vector upserts are deterministic on retry."""
        async with self._pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    UPDATE document_ingestion_jobs AS jobs
                    SET status = 'queued', claimed_at = NULL, updated_at = now()
                    FROM project_documents AS documents
                    WHERE jobs.document_id = documents.id
                      AND jobs.status IN ('extracting', 'indexing')
                      AND jobs.claimed_at < %s
                      AND documents.status IN ('extracting', 'indexing')
                    RETURNING jobs.document_id
                    """,
                    (claimed_before,),
                )
                rows = await cursor.fetchall()
                for row in rows:
                    await connection.execute(
                        """
                        UPDATE project_documents
                        SET status = 'received', error_code = NULL, updated_at = now()
                        WHERE id = %s AND status IN ('extracting', 'indexing')
                        """,
                        (row[0],),
                    )
        return len(rows)

    async def transition_document(
        self,
        document_id: str,
        *,
        from_status: str,
        to_status: str,
        page_count: int | None = None,
        ocr_page_count: int | None = None,
        chunk_count: int | None = None,
        error_code: str | None = None,
    ) -> bool:
        """Guard the documented state machine; no content crosses this boundary."""
        allowed = {
            ("extracting", "indexing"),
            ("indexing", "ready"),
            ("extracting", "failed"),
            ("indexing", "failed"),
            ("deleting", "deleted"),
        }
        if (from_status, to_status) not in allowed:
            raise ValueError("invalid project document state transition")
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE project_documents
                SET status = %s, page_count = COALESCE(%s, page_count),
                    ocr_page_count = COALESCE(%s, ocr_page_count),
                    chunk_count = COALESCE(%s, chunk_count), error_code = %s,
                    deleted_at = CASE WHEN %s = 'deleted' THEN now() ELSE deleted_at END,
                    updated_at = now()
                WHERE id = %s AND status = %s
                RETURNING id
                """,
                (
                    to_status,
                    page_count,
                    ocr_page_count,
                    chunk_count,
                    error_code,
                    to_status,
                    document_id,
                    from_status,
                ),
            )
            return await cursor.fetchone() is not None

    async def finish_job(
        self, document_id: str, *, status: str, error_code: str | None = None
    ) -> bool:
        if status not in {"completed", "failed"}:
            raise ValueError("job status must be completed or failed")
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE document_ingestion_jobs
                SET status = %s, error_code = %s, completed_at = now(), updated_at = now()
                WHERE document_id = %s AND status IN ('extracting', 'indexing')
                RETURNING id
                """,
                (status, error_code, document_id),
            )
            return await cursor.fetchone() is not None

    async def begin_deletion(
        self, principal: VerifiedPrincipal, project_id: str, document_id: str
    ) -> ProjectDocument | None:
        document = await self.require_document(principal, project_id, document_id)
        if document is None or document.status in {"deleting", "deleted"}:
            return None
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE project_documents
                SET status = 'deleting', deleted_at = now(), updated_at = now()
                WHERE id = %s AND project_id = %s AND workspace_id = %s AND user_id = %s
                  AND status <> 'deleted'
                RETURNING id, project_id, workspace_id, user_id, filename, media_type, byte_size,
                    content_sha256, storage_key, status, expires_at
                """,
                (document_id, project_id, principal.workspace_id, principal.user_id),
            )
            row = await cursor.fetchone()
        return None if row is None else _document(row)

    async def record_deletion_audit(
        self,
        document_id: str,
        *,
        postgres_outcome: str,
        qdrant_outcome: str,
        storage_outcome: str,
    ) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                """
                INSERT INTO document_deletion_audits (
                    id, document_id, postgres_outcome, qdrant_outcome, storage_outcome
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (str(uuid4()), document_id, postgres_outcome, qdrant_outcome, storage_outcome),
            )

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


def _job(row: tuple[object, ...]) -> DocumentIngestionJob:
    return DocumentIngestionJob(
        id=str(row[0]), document_id=str(row[1]), status=str(row[2]), attempts=int(cast(int, row[3]))
    )
