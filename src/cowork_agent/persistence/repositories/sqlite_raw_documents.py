"""SQLite repository for raw document metadata: version and save history."""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class RawDocumentMetadata:
    filename: str
    doc_key: str
    version: int
    last_saved_at: str
    last_status: int | None = None


class SQLiteRawDocumentRepository:
    def __init__(self, path: Path) -> None:
        self._path = path

    def _connect(self) -> sqlite3.Connection:
        """Open a connection. Callers must wrap it in ``closing``.

        ``with connection`` only commits or rolls back the transaction -- it leaves
        the handle open -- so on its own it leaks a file descriptor per call.
        """
        conn = sqlite3.connect(self._path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS raw_document_metadata (
                    filename TEXT PRIMARY KEY,
                    doc_key TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    last_saved_at TEXT NOT NULL,
                    last_status INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_raw_document_doc_key
                    ON raw_document_metadata (doc_key);
                """
            )

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    def _get(self, filename: str) -> RawDocumentMetadata | None:
        with closing(self._connect()) as conn, conn as db:
            row = db.execute(
                """
                SELECT filename, doc_key, version, last_saved_at, last_status
                FROM raw_document_metadata
                WHERE filename = ?
                """,
                (filename,),
            ).fetchone()
            if row is None:
                return None
            return RawDocumentMetadata(
                filename=row["filename"],
                doc_key=row["doc_key"],
                version=row["version"],
                last_saved_at=row["last_saved_at"],
                last_status=row["last_status"],
            )

    async def get(self, filename: str) -> RawDocumentMetadata | None:
        return await asyncio.to_thread(self._get, filename)

    def _record_save(self, filename: str, status: int) -> RawDocumentMetadata:
        with closing(self._connect()) as conn, conn as db:
            now_iso = datetime.now(UTC).isoformat()
            raw_data = f"{filename}:{now_iso}:{uuid4().hex}".encode()
            new_key = hashlib.sha256(raw_data).hexdigest()[:20]

            row = db.execute(
                """
                SELECT version FROM raw_document_metadata WHERE filename = ?
                """,
                (filename,),
            ).fetchone()

            if row is None:
                new_version = 1
                db.execute(
                    """
                    INSERT INTO raw_document_metadata (
                        filename, doc_key, version, last_saved_at, last_status
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (filename, new_key, new_version, now_iso, status),
                )
            else:
                new_version = row["version"] + 1
                db.execute(
                    """
                    UPDATE raw_document_metadata
                    SET doc_key = ?, version = ?, last_saved_at = ?, last_status = ?
                    WHERE filename = ?
                    """,
                    (new_key, new_version, now_iso, status, filename),
                )

            return RawDocumentMetadata(
                filename=filename,
                doc_key=new_key,
                version=new_version,
                last_saved_at=now_iso,
                last_status=status,
            )

    async def record_save(self, filename: str, status: int) -> RawDocumentMetadata:
        return await asyncio.to_thread(self._record_save, filename, status)

    def _delete(self, filename: str) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                """
                DELETE FROM raw_document_metadata WHERE filename = ?
                """,
                (filename,),
            )
            return cursor.rowcount > 0

    async def delete(self, filename: str) -> bool:
        return await asyncio.to_thread(self._delete, filename)
