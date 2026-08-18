"""SQLite implementation of the chunk half of project-document retrieval."""

from __future__ import annotations

import asyncio
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from .project_document_chunks import ChunkInput, EligibleChunks, StoredChunk


class SQLiteProjectDocumentChunkRepository:
    """Chunk text and lexical ranking for the local SQLite document plane."""

    def __init__(self, path: Path, project_metadata_path: Path) -> None:
        self._path = path
        self._project_metadata_path = project_metadata_path

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    async def replace_document_chunks(
        self,
        *,
        document_id: str,
        project_id: str,
        chunks: tuple[ChunkInput, ...],
    ) -> tuple[tuple[str, int], ...]:
        return await asyncio.to_thread(
            self._replace_document_chunks, document_id, project_id, chunks
        )

    async def list_eligible(
        self,
        *,
        workspace_id: str,
        user_id: str,
        project_id: str,
        document_ids: tuple[str, ...],
        now: datetime,
        query: str,
        lexical_limit: int,
    ) -> EligibleChunks:
        return await asyncio.to_thread(
            self._list_eligible,
            workspace_id,
            user_id,
            project_id,
            document_ids,
            now,
            query,
            lexical_limit,
        )

    async def list_section_siblings(
        self, *, vector_ids: tuple[int, ...], allowlist: tuple[int, ...]
    ) -> tuple[tuple[int, int], ...]:
        return await asyncio.to_thread(self._list_section_siblings, vector_ids, allowlist)

    async def hydrate(self, vector_ids: tuple[int, ...]) -> tuple[StoredChunk, ...]:
        return await asyncio.to_thread(self._hydrate, vector_ids)

    async def list_document_vector_ids(self, document_id: str) -> tuple[int, ...]:
        return await asyncio.to_thread(self._list_document_vector_ids, document_id)

    async def delete_document_chunks(self, document_id: str) -> tuple[int, ...]:
        return await asyncio.to_thread(self._delete_document_chunks, document_id)

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS local_project_document_chunks (
                    vector_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chunk_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    page_start INTEGER NOT NULL,
                    page_end INTEGER NOT NULL,
                    section TEXT,
                    UNIQUE(document_id, chunk_id)
                )
                """
            )

    def _replace_document_chunks(
        self,
        document_id: str,
        project_id: str,
        chunks: tuple[ChunkInput, ...],
    ) -> tuple[tuple[str, int], ...]:
        if not chunks:
            raise ValueError("a project document must persist at least one chunk")
        chunk_ids = tuple(chunk.chunk_id for chunk in chunks)
        placeholders = ", ".join("?" for _ in chunk_ids)
        assigned: list[tuple[str, int]] = []
        with self._connect() as db:
            db.execute(
                "DELETE FROM local_project_document_chunks "
                f"WHERE document_id=? AND chunk_id NOT IN ({placeholders})",
                (document_id, *chunk_ids),
            )
            for chunk in chunks:
                db.execute(
                    """
                    INSERT INTO local_project_document_chunks (
                        chunk_id, document_id, project_id, chunk_index,
                        text, page_start, page_end, section
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(document_id, chunk_id) DO UPDATE SET
                        project_id=excluded.project_id,
                        chunk_index=excluded.chunk_index,
                        text=excluded.text,
                        page_start=excluded.page_start,
                        page_end=excluded.page_end,
                        section=excluded.section
                    """,
                    (
                        chunk.chunk_id,
                        document_id,
                        project_id,
                        chunk.chunk_index,
                        chunk.text,
                        chunk.page_start,
                        chunk.page_end,
                        chunk.section,
                    ),
                )
                row = db.execute(
                    """
                    SELECT vector_id FROM local_project_document_chunks
                    WHERE document_id=? AND chunk_id=?
                    """,
                    (document_id, chunk.chunk_id),
                ).fetchone()
                assert row is not None
                assigned.append((chunk.chunk_id, int(row["vector_id"])))
        return tuple(assigned)

    def _list_eligible(
        self,
        workspace_id: str,
        user_id: str,
        project_id: str,
        document_ids: tuple[str, ...],
        now: datetime,
        query: str,
        lexical_limit: int,
    ) -> EligibleChunks:
        if not document_ids or lexical_limit < 1:
            return EligibleChunks((), ())
        placeholders = ", ".join("?" for _ in document_ids)
        sql = (
            "SELECT chunks.vector_id, chunks.text FROM local_project_document_chunks AS chunks "
            "JOIN metadata.local_documents AS documents ON documents.id=chunks.document_id "
            "WHERE documents.workspace_id=? AND documents.user_id=? "
            "AND documents.project_id=? AND documents.id IN "
            f"({placeholders}) AND documents.status='ready' "
            "AND documents.expires_at>?"
        )
        with self._connect() as db:
            rows = db.execute(
                sql,
                (workspace_id, user_id, project_id, *document_ids, now.isoformat()),
            ).fetchall()
        allowlist = tuple(int(row["vector_id"]) for row in rows)
        terms = tuple(re.findall(r"[^\W_]+", query.casefold()))
        ranked = sorted(
            (
                (
                    sum(term in str(row["text"]).casefold() for term in terms),
                    int(row["vector_id"]),
                )
                for row in rows
            ),
            key=lambda item: (-item[0], item[1]),
        )
        lexical = tuple(vector_id for score, vector_id in ranked if score > 0)
        return EligibleChunks(allowlist, lexical[:lexical_limit])

    def _list_section_siblings(
        self, vector_ids: tuple[int, ...], allowlist: tuple[int, ...]
    ) -> tuple[tuple[int, int], ...]:
        if not vector_ids or not allowlist:
            return ()
        seed_marks = ", ".join("?" for _ in vector_ids)
        allowed_marks = ", ".join("?" for _ in allowlist)
        sql = (
            "SELECT seeds.vector_id AS seed, siblings.vector_id AS sibling "
            "FROM local_project_document_chunks AS seeds "
            "JOIN local_project_document_chunks AS siblings "
            "ON siblings.document_id=seeds.document_id AND siblings.section=seeds.section "
            f"WHERE seeds.vector_id IN ({seed_marks}) AND seeds.section IS NOT NULL "
            f"AND siblings.vector_id IN ({allowed_marks}) "
            "ORDER BY seeds.vector_id, siblings.chunk_index"
        )
        with self._connect() as db:
            rows = db.execute(sql, (*vector_ids, *allowlist)).fetchall()
        return tuple((int(row["seed"]), int(row["sibling"])) for row in rows)

    def _hydrate(self, vector_ids: tuple[int, ...]) -> tuple[StoredChunk, ...]:
        if not vector_ids:
            return ()
        marks = ", ".join("?" for _ in vector_ids)
        sql = (
            "SELECT chunks.vector_id, chunks.chunk_id, chunks.document_id, "
            "documents.filename, chunks.text, chunks.page_start, chunks.page_end, chunks.section "
            "FROM local_project_document_chunks AS chunks "
            "JOIN metadata.local_documents AS documents ON documents.id=chunks.document_id "
            f"WHERE chunks.vector_id IN ({marks})"
        )
        with self._connect() as db:
            rows = db.execute(sql, vector_ids).fetchall()
        return tuple(
            StoredChunk(
                vector_id=int(row["vector_id"]),
                chunk_id=str(row["chunk_id"]),
                document_id=str(row["document_id"]),
                filename=str(row["filename"]),
                text=str(row["text"]),
                page_start=int(row["page_start"]),
                page_end=int(row["page_end"]),
                section=str(row["section"]) if row["section"] is not None else None,
            )
            for row in rows
        )

    def _list_document_vector_ids(self, document_id: str) -> tuple[int, ...]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT vector_id FROM local_project_document_chunks WHERE document_id=?",
                (document_id,),
            ).fetchall()
        return tuple(int(row["vector_id"]) for row in rows)

    def _delete_document_chunks(self, document_id: str) -> tuple[int, ...]:
        vector_ids = self._list_document_vector_ids(document_id)
        with self._connect() as db:
            db.execute(
                "DELETE FROM local_project_document_chunks WHERE document_id=?", (document_id,)
            )
        return vector_ids

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("ATTACH DATABASE ? AS metadata", (str(self._project_metadata_path),))
        return db
