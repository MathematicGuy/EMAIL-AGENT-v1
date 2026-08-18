"""SQLite project-document metadata and queue for local runtime."""

# ruff: noqa: E501

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from cowork_agent.identity import VerifiedPrincipal
from cowork_agent.persistence.repositories.projects import (
    DocumentIngestionJob,
    Project,
    ProjectDocument,
)


class SQLiteProjectRepository:
    def __init__(self, path: Path) -> None:
        self._path = path

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    async def default_project(self, principal: VerifiedPrincipal) -> Project:
        return await asyncio.to_thread(self._default_project, principal)

    async def list_for(self, principal: VerifiedPrincipal) -> tuple[Project, ...]:
        return await asyncio.to_thread(self._list_for, principal)

    async def create(self, principal: VerifiedPrincipal, name: str) -> Project:
        return await asyncio.to_thread(self._create, principal, name)

    async def require_project(
        self, principal: VerifiedPrincipal, project_id: str
    ) -> Project | None:
        return await asyncio.to_thread(self._require_project, principal, project_id)

    async def create_or_get_document(self, **kwargs: object) -> tuple[ProjectDocument, bool]:
        return await asyncio.to_thread(self._create_or_get_document, kwargs)

    async def require_document(
        self, principal: VerifiedPrincipal, project_id: str, document_id: str
    ) -> ProjectDocument | None:
        return await asyncio.to_thread(self._require_document, principal, project_id, document_id)

    async def list_documents(
        self, principal: VerifiedPrincipal, project_id: str
    ) -> tuple[ProjectDocument, ...]:
        return await asyncio.to_thread(self._list_documents, principal, project_id)

    async def mark_upload_completed(
        self, principal: VerifiedPrincipal, project_id: str, document_id: str
    ) -> DocumentIngestionJob | None:
        return await asyncio.to_thread(
            self._mark_upload_completed, principal, project_id, document_id
        )

    async def claim_job(self, document_id: str) -> ProjectDocument | None:
        return await asyncio.to_thread(self._claim_job, document_id)

    async def next_claimable_job(self) -> str | None:
        return await asyncio.to_thread(self._next_claimable_job)

    async def transition_document(self, document_id: str, **kwargs: object) -> bool:
        return await asyncio.to_thread(self._transition_document, document_id, kwargs)

    async def finish_job(
        self, document_id: str, *, status: str, error_code: str | None = None
    ) -> bool:
        return await asyncio.to_thread(self._finish_job, document_id, status, error_code)

    async def retry_job(
        self,
        document_id: str,
        *,
        from_status: str,
        error_code: str,
        max_attempts: int,
        delay_seconds: int,
    ) -> bool:
        return await asyncio.to_thread(
            self._retry_job, document_id, from_status, error_code, max_attempts, delay_seconds
        )

    async def record_document_worker_heartbeat(self) -> None:
        await asyncio.to_thread(self._heartbeat)

    async def worker_heartbeat_is_fresh(self, *, max_age_seconds: int) -> bool:
        return await asyncio.to_thread(self._heartbeat_is_fresh, max_age_seconds)

    async def list_ready_for_scope(
        self, workspace_id: str, user_id: str, project_id: str, *, at: datetime
    ) -> tuple[ProjectDocument, ...]:
        return await asyncio.to_thread(self._list_ready, workspace_id, user_id, project_id, at)

    async def begin_deletion(
        self, principal: VerifiedPrincipal, project_id: str, document_id: str
    ) -> ProjectDocument | None:
        document = await self.require_document(principal, project_id, document_id)
        if document is None:
            return None
        await asyncio.to_thread(self._set_document_status, document_id, "deleted")
        return await self.require_document(principal, project_id, document_id)

    async def begin_project_deletion(
        self, principal: VerifiedPrincipal, project_id: str
    ) -> tuple[Project, Project | None, tuple[str, ...]] | None:
        project = await self.require_project(principal, project_id)
        if project is None:
            return None
        documents = await self.list_documents(principal, project_id)
        for document in documents:
            await asyncio.to_thread(self._set_document_status, document.id, "deleted")
        return project, None, tuple(document.id for document in documents)

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS local_projects (
                    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, owner_user_id TEXT NOT NULL,
                    name TEXT NOT NULL, is_default INTEGER NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(workspace_id, owner_user_id, name)
                );
                CREATE TABLE IF NOT EXISTS local_documents (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
                    user_id TEXT NOT NULL, filename TEXT NOT NULL, media_type TEXT NOT NULL,
                    byte_size INTEGER NOT NULL, content_sha256 TEXT NOT NULL, storage_key TEXT NOT NULL,
                    status TEXT NOT NULL, expires_at TEXT NOT NULL, page_count INTEGER, ocr_page_count INTEGER,
                    chunk_count INTEGER, error_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(project_id, content_sha256)
                );
                CREATE TABLE IF NOT EXISTS local_document_jobs (
                    document_id TEXT PRIMARY KEY, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    available_at TEXT NOT NULL, claimed_at TEXT, error_code TEXT
                );
                CREATE TABLE IF NOT EXISTS local_document_worker (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1), heartbeat_at TEXT NOT NULL
                );
                """
            )

    def _default_project(self, principal: VerifiedPrincipal) -> Project:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM local_projects WHERE workspace_id=? AND owner_user_id=? AND is_default=1",
                (principal.workspace_id, principal.user_id),
            ).fetchone()
            if row is None:
                now = _now()
                project_id = str(uuid4())
                db.execute(
                    "INSERT INTO local_projects VALUES (?, ?, ?, 'Default Project', 1, ?)",
                    (project_id, principal.workspace_id, principal.user_id, now),
                )
                row = db.execute(
                    "SELECT * FROM local_projects WHERE id=?", (project_id,)
                ).fetchone()
        assert row is not None
        return _project(row)

    def _list_for(self, principal: VerifiedPrincipal) -> tuple[Project, ...]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM local_projects WHERE workspace_id=? AND owner_user_id=? ORDER BY is_default DESC, created_at, id",
                (principal.workspace_id, principal.user_id),
            ).fetchall()
        return tuple(_project(row) for row in rows)

    def _create(self, principal: VerifiedPrincipal, name: str) -> Project:
        clean = name.strip()
        if not clean or len(clean) > 200:
            raise ValueError("project name must be between 1 and 200 characters")
        project_id = str(uuid4())
        with self._connect() as db:
            db.execute(
                "INSERT INTO local_projects VALUES (?, ?, ?, ?, 0, ?)",
                (project_id, principal.workspace_id, principal.user_id, clean, _now()),
            )
            row = db.execute("SELECT * FROM local_projects WHERE id=?", (project_id,)).fetchone()
        assert row is not None
        return _project(row)

    def _require_project(self, principal: VerifiedPrincipal, project_id: str) -> Project | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM local_projects WHERE id=? AND workspace_id=? AND owner_user_id=?",
                (project_id, principal.workspace_id, principal.user_id),
            ).fetchone()
        return None if row is None else _project(row)

    def _create_or_get_document(self, values: dict[str, object]) -> tuple[ProjectDocument, bool]:
        principal = values["principal"]
        if not isinstance(principal, VerifiedPrincipal):
            raise TypeError("principal is required")
        project_id = str(values["project_id"])
        if self._require_project(principal, project_id) is None:
            raise LookupError("Project not found")
        media_type = str(values["media_type"])
        if media_type not in {
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }:
            raise ValueError("unsupported_media_type")
        byte_size = _required_int(values, "byte_size")
        digest = str(values["content_sha256"])
        if byte_size < 1 or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid_document_metadata")
        with self._connect() as db:
            existing = db.execute(
                "SELECT * FROM local_documents WHERE project_id=? AND content_sha256=? AND status <> 'deleted'",
                (project_id, digest),
            ).fetchone()
            if existing is not None:
                return _document(existing), False
            count, total = db.execute(
                "SELECT count(*), coalesce(sum(byte_size), 0) FROM local_documents WHERE project_id=? AND status <> 'deleted'",
                (project_id,),
            ).fetchone()
            if int(count) >= _optional_int(values, "max_documents_per_project", 50):
                raise ValueError("document_quota_exceeded")
            if int(total) + byte_size > _optional_int(
                values, "max_project_bytes", 500 * 1024 * 1024
            ):
                raise ValueError("project_storage_quota_exceeded")
            document_id, now = str(uuid4()), _now()
            key = f"projects/{principal.workspace_id}/{principal.user_id}/{project_id}/{document_id}/source"
            expires = (
                datetime.now(UTC) + timedelta(seconds=_required_int(values, "expires_in_seconds"))
            ).isoformat()
            db.execute(
                "INSERT INTO local_documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'received', ?, NULL, NULL, NULL, NULL, ?, ?)",
                (
                    document_id,
                    project_id,
                    principal.workspace_id,
                    principal.user_id,
                    str(values["filename"]).strip(),
                    media_type,
                    byte_size,
                    digest,
                    key,
                    expires,
                    now,
                    now,
                ),
            )
            row = db.execute("SELECT * FROM local_documents WHERE id=?", (document_id,)).fetchone()
        assert row is not None
        return _document(row), True

    def _require_document(
        self, principal: VerifiedPrincipal, project_id: str, document_id: str
    ) -> ProjectDocument | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM local_documents WHERE id=? AND project_id=? AND workspace_id=? AND user_id=?",
                (document_id, project_id, principal.workspace_id, principal.user_id),
            ).fetchone()
        return None if row is None else _document(row)

    def _list_documents(
        self, principal: VerifiedPrincipal, project_id: str
    ) -> tuple[ProjectDocument, ...]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM local_documents WHERE project_id=? AND workspace_id=? AND user_id=? AND status <> 'deleted' ORDER BY created_at DESC, id",
                (project_id, principal.workspace_id, principal.user_id),
            ).fetchall()
        return tuple(_document(row) for row in rows)

    def _mark_upload_completed(
        self, principal: VerifiedPrincipal, project_id: str, document_id: str
    ) -> DocumentIngestionJob | None:
        document = self._require_document(principal, project_id, document_id)
        if document is None or document.status != "received":
            return None
        with self._connect() as db:
            db.execute(
                "INSERT INTO local_document_jobs VALUES (?, 'queued', 0, ?, NULL, NULL) ON CONFLICT(document_id) DO UPDATE SET status='queued', error_code=NULL, available_at=excluded.available_at",
                (document_id, _now()),
            )
            row = db.execute(
                "SELECT * FROM local_document_jobs WHERE document_id=?", (document_id,)
            ).fetchone()
        assert row is not None
        return DocumentIngestionJob(
            document_id, document_id, str(row["status"]), int(row["attempts"])
        )

    def _claim_job(self, document_id: str) -> ProjectDocument | None:
        with self._connect() as db:
            now = _now()
            row = db.execute(
                "SELECT * FROM local_document_jobs WHERE document_id=? AND status='queued' AND available_at <= ?",
                (document_id, now),
            ).fetchone()
            if row is None:
                return None
            changed = db.execute(
                "UPDATE local_documents SET status='extracting', updated_at=? WHERE id=? AND status='received'",
                (now, document_id),
            ).rowcount
            if changed != 1:
                return None
            db.execute(
                "UPDATE local_document_jobs SET status='extracting', attempts=attempts+1, claimed_at=? WHERE document_id=?",
                (now, document_id),
            )
            doc = db.execute("SELECT * FROM local_documents WHERE id=?", (document_id,)).fetchone()
        return None if doc is None else _document(doc)

    def _next_claimable_job(self) -> str | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT document_id FROM local_document_jobs WHERE status='queued' AND available_at <= ? ORDER BY available_at LIMIT 1",
                (_now(),),
            ).fetchone()
        return None if row is None else str(row[0])

    def _transition_document(self, document_id: str, values: dict[str, object]) -> bool:
        fields: dict[str, object] = {"status": values["to_status"], "updated_at": _now()}
        for key in ("page_count", "ocr_page_count", "chunk_count", "error_code"):
            if key in values:
                fields[key] = values[key]
        assignments = ", ".join(f"{key}=?" for key in fields)
        with self._connect() as db:
            changed = db.execute(
                f"UPDATE local_documents SET {assignments} WHERE id=? AND status=?",
                (*fields.values(), document_id, values["from_status"]),
            ).rowcount
        return changed == 1

    def _finish_job(self, document_id: str, status: str, error_code: str | None) -> bool:
        with self._connect() as db:
            changed = db.execute(
                "UPDATE local_document_jobs SET status=?, error_code=? WHERE document_id=?",
                (status, error_code, document_id),
            ).rowcount
        return changed == 1

    def _retry_job(
        self, document_id: str, from_status: str, error_code: str, max_attempts: int, delay: int
    ) -> bool:
        with self._connect() as db:
            row = db.execute(
                "SELECT attempts FROM local_document_jobs WHERE document_id=? "
                "AND status='extracting'",
                (document_id,),
            ).fetchone()
            if row is None or int(row[0]) >= max_attempts:
                return False
            changed = db.execute(
                "UPDATE local_documents SET status='received', error_code=?, updated_at=? "
                "WHERE id=? AND status=?",
                (error_code, _now(), document_id, from_status),
            ).rowcount
            if changed != 1:
                return False
            db.execute(
                "UPDATE local_document_jobs SET status='queued', error_code=?, available_at=? WHERE document_id=?",
                (
                    error_code,
                    (datetime.now(UTC) + timedelta(seconds=delay)).isoformat(),
                    document_id,
                ),
            )
        return True

    def _heartbeat(self) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO local_document_worker VALUES (1, ?) ON CONFLICT(singleton) DO UPDATE SET heartbeat_at=excluded.heartbeat_at",
                (_now(),),
            )

    def _heartbeat_is_fresh(self, seconds: int) -> bool:
        with self._connect() as db:
            row = db.execute(
                "SELECT heartbeat_at FROM local_document_worker WHERE singleton=1"
            ).fetchone()
        return row is not None and datetime.fromisoformat(str(row[0])) >= datetime.now(
            UTC
        ) - timedelta(seconds=seconds)

    def _list_ready(
        self, workspace: str, user: str, project: str, at: datetime
    ) -> tuple[ProjectDocument, ...]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM local_documents WHERE workspace_id=? AND user_id=? AND project_id=? AND status='ready' AND expires_at > ?",
                (workspace, user, project, at.isoformat()),
            ).fetchall()
        return tuple(_document(row) for row in rows)

    def _set_document_status(self, document_id: str, status: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE local_documents SET status=?, updated_at=? WHERE id=?",
                (status, _now(), document_id),
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=5000")
        return db


def _project(row: sqlite3.Row) -> Project:
    return Project(
        str(row["id"]),
        str(row["workspace_id"]),
        str(row["owner_user_id"]),
        str(row["name"]),
        bool(row["is_default"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
    )


def _document(row: sqlite3.Row) -> ProjectDocument:
    return ProjectDocument(
        str(row["id"]),
        str(row["project_id"]),
        str(row["workspace_id"]),
        str(row["user_id"]),
        str(row["filename"]),
        str(row["media_type"]),
        int(row["byte_size"]),
        str(row["content_sha256"]),
        str(row["storage_key"]),
        str(row["status"]),
        datetime.fromisoformat(str(row["expires_at"])),
        row["page_count"],
        row["ocr_page_count"],
        row["chunk_count"],
        row["error_code"],
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _required_int(values: dict[str, object], key: str) -> int:
    value = values[key]
    if not isinstance(value, int):
        raise TypeError(f"{key} must be an integer")
    return value


def _optional_int(values: dict[str, object], key: str, default: int) -> int:
    value = values.get(key, default)
    if not isinstance(value, int):
        raise TypeError(f"{key} must be an integer")
    return value
