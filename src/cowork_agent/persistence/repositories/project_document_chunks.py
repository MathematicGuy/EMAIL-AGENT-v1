"""Durable project chunk text, the tenant ACL, and the lexical retrieval leg.

ADR-008 moves all three into one Postgres query. The allowlist this module
returns is what makes the Turbovec dense leg exact: a per-project ``.tvim``
only enforces *project*, so the remaining five ADR-007 conditions live here in
the ``WHERE`` clause and reach the vector kernel as a ``uint64`` allowlist.

The lexical ranking is deliberately returned as an ordered list of vector IDs
with no scores attached. RRF consumes ranks only, so ``ts_rank_cd`` not being
true BM25 never propagates -- and a caller cannot accidentally fuse its raw
values.

``'simple'`` is the text search configuration on both sides of the match:
the corpus is Vietnamese, so an English dictionary would stem nothing and
would drop query terms that happen to be English stopwords. Migration 014
defines the generated ``fts`` column the same way -- the two must never
diverge, or the tsquery matches nothing at all.
"""

from dataclasses import dataclass
from datetime import datetime

from psycopg_pool import AsyncConnectionPool


@dataclass(frozen=True, slots=True)
class ChunkInput:
    """One extracted chunk awaiting persistence, before it has a vector ID."""

    chunk_id: str
    chunk_index: int
    text: str
    page_start: int
    page_end: int
    section: str | None = None


@dataclass(frozen=True, slots=True)
class StoredChunk:
    """A persisted chunk with the citation coordinates retrieval must return."""

    vector_id: int
    chunk_id: str
    document_id: str
    filename: str
    text: str
    page_start: int
    page_end: int
    section: str | None


@dataclass(frozen=True, slots=True)
class EligibleChunks:
    """Retrieval-eligible vector IDs plus their lexical ordering."""

    allowlist: tuple[int, ...]
    lexical: tuple[int, ...]


class PostgresProjectDocumentChunkRepository:
    """Chunk text, ACL evaluation, and full-text ranking for project documents."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def replace_document_chunks(
        self,
        *,
        document_id: str,
        project_id: str,
        chunks: tuple[ChunkInput, ...],
    ) -> tuple[tuple[str, int], ...]:
        """Persist a document's chunks; returns ``(chunk_id, vector_id)`` pairs.

        ``chunk_id`` is a deterministic uuid5 of the chunk's coordinates and
        text, so a retried ingestion re-uses the existing row and its
        ``vector_id`` stays stable -- which is what lets ``IdMapIndex.remove``
        find the same entry later.
        """
        if not chunks:
            raise ValueError("a project document must persist at least one chunk")
        assigned: list[tuple[str, int]] = []
        async with self._pool.connection() as connection, connection.transaction():
            await connection.execute(
                """
                DELETE FROM project_document_chunks
                WHERE document_id = %s AND NOT (chunk_id = ANY(%s::uuid[]))
                """,
                (document_id, [chunk.chunk_id for chunk in chunks]),
            )
            for chunk in chunks:
                cursor = await connection.execute(
                    """
                    INSERT INTO project_document_chunks (
                        chunk_id, document_id, project_id, chunk_index,
                        text, page_start, page_end, section
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (document_id, chunk_id) DO UPDATE
                        SET chunk_index = EXCLUDED.chunk_index,
                            text = EXCLUDED.text,
                            page_start = EXCLUDED.page_start,
                            page_end = EXCLUDED.page_end,
                            section = EXCLUDED.section
                    RETURNING vector_id
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
                row = await cursor.fetchone()
                assert row is not None
                assigned.append((chunk.chunk_id, int(row[0])))
        return tuple(assigned)

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
        """Evaluate all six ADR-007 conditions and rank the survivors lexically.

        Deliberately callable before the query is embedded: no vector I/O may
        precede the authorization decision.
        """
        if not document_ids or lexical_limit < 1:
            return EligibleChunks((), ())
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT chunks.vector_id,
                    ts_rank_cd(chunks.fts, websearch_to_tsquery('simple', %s)) AS rank
                FROM project_document_chunks AS chunks
                JOIN project_documents AS documents ON documents.id = chunks.document_id
                WHERE documents.workspace_id = %s
                  AND documents.user_id = %s
                  AND documents.project_id = %s
                  AND documents.id = ANY(%s::uuid[])
                  AND documents.status = 'ready'
                  AND documents.deleted_at IS NULL
                  AND documents.expires_at > %s
                ORDER BY rank DESC, chunks.vector_id
                """,
                (
                    query,
                    workspace_id,
                    user_id,
                    project_id,
                    list(document_ids),
                    now,
                ),
            )
            rows = await cursor.fetchall()
        allowlist = tuple(int(row[0]) for row in rows)
        lexical = tuple(int(row[0]) for row in rows if float(row[1]) > 0.0)
        return EligibleChunks(allowlist, lexical[:lexical_limit])

    async def list_section_siblings(
        self, *, vector_ids: tuple[int, ...], allowlist: tuple[int, ...]
    ) -> tuple[tuple[int, int], ...]:
        """Pair each seed with every chunk of its section, in reading order.

        A long article is cut into several chunks, so a question about the
        article as a whole ("Điều 4 gồm những gì") is answered by a ranking
        that returns one of them and hides the rest. This is the query that
        puts the article back together.

        Authorization is not re-derived: ``allowlist`` is the set
        ``list_eligible`` already cleared under the six ADR-007 conditions, and
        a sibling outside it is not returned. Chunks with no section are never
        grouped -- a NULL section is the absence of a heading, not a heading
        shared by every unstructured chunk in the document.
        """
        if not vector_ids or not allowlist:
            return ()
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT seeds.vector_id, siblings.vector_id
                FROM project_document_chunks AS seeds
                JOIN project_document_chunks AS siblings
                    ON siblings.document_id = seeds.document_id
                   AND siblings.section = seeds.section
                WHERE seeds.vector_id = ANY(%s::bigint[])
                  AND seeds.section IS NOT NULL
                  AND siblings.vector_id = ANY(%s::bigint[])
                ORDER BY seeds.vector_id, siblings.chunk_index
                """,
                (list(vector_ids), list(allowlist)),
            )
            rows = await cursor.fetchall()
        return tuple((int(row[0]), int(row[1])) for row in rows)

    async def hydrate(self, vector_ids: tuple[int, ...]) -> tuple[StoredChunk, ...]:
        """Fetch text and citation coordinates for already-authorized chunks."""
        if not vector_ids:
            return ()
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT chunks.vector_id, chunks.chunk_id, chunks.document_id,
                    documents.filename, chunks.text, chunks.page_start,
                    chunks.page_end, chunks.section
                FROM project_document_chunks AS chunks
                JOIN project_documents AS documents ON documents.id = chunks.document_id
                WHERE chunks.vector_id = ANY(%s::bigint[])
                """,
                (list(vector_ids),),
            )
            rows = await cursor.fetchall()
        return tuple(
            StoredChunk(
                vector_id=int(row[0]),
                chunk_id=str(row[1]),
                document_id=str(row[2]),
                filename=str(row[3]),
                text=str(row[4]),
                page_start=int(row[5]),
                page_end=int(row[6]),
                section=None if row[7] is None else str(row[7]),
            )
            for row in rows
        )

    async def list_document_vector_ids(self, document_id: str) -> tuple[int, ...]:
        """Vector IDs a caller must remove from the project index."""
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT vector_id FROM project_document_chunks WHERE document_id = %s",
                (document_id,),
            )
            rows = await cursor.fetchall()
        return tuple(int(row[0]) for row in rows)

    async def delete_document_chunks(self, document_id: str) -> tuple[int, ...]:
        """Hard-delete a document's chunks; returns the freed vector IDs.

        ADR-008 decision 9: rows are removed, never soft-flagged, so a deleted
        document leaves no text behind in Postgres or its backups.
        """
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                "DELETE FROM project_document_chunks WHERE document_id = %s RETURNING vector_id",
                (document_id,),
            )
            rows = await cursor.fetchall()
        return tuple(int(row[0]) for row in rows)
