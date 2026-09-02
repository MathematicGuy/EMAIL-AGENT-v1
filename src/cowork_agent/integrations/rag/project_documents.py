"""Private, project-scoped hybrid retrieval for user uploaded documents.

This module intentionally has no company-RAG fallback. The tenant ACL is
evaluated in SQL *before* the query reaches the vector index, so a search
cannot consider another project's text (ADR-007 §4, restated by ADR-008 §3).

Three stores cooperate, and each owns exactly one thing:

- Postgres owns chunk text, the six-condition ACL, and the lexical ranking.
- A per-project Turbovec ``.tvim`` owns the quantized vectors.
- ``ReciprocalRankFusion`` merges the two rankings -- ranks only, never scores.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Protocol

from cowork_agent.domain.project_documents import (
    ProjectDocumentEvidence as DomainProjectDocumentEvidence,
)
from cowork_agent.domain.project_documents import (
    ProjectDocumentQuery,
    ProjectDocumentResponse,
)
from cowork_agent.observability import (
    ProjectDocumentTimingOutcome,
    log_project_document_timing,
    safe_provider_label,
)
from cowork_agent.persistence.repositories.project_document_chunks import (
    ChunkInput,
    EligibleChunks,
    StoredChunk,
)
from cowork_agent.persistence.repositories.projects import ProjectDocument

from .embeddings import EmbeddingPort
from .project_index import ProjectIndexUnavailable, TurbovecProjectIndexStore
from .rrf import ReciprocalRankFusion

#: How many candidates each leg contributes per requested result. Fusion needs
#: more input than output or the two rankings barely overlap.
_CANDIDATE_MULTIPLIER = 4

#: Headroom over ``limit`` for chunks pulled in by section, on top of the ones
#: that earned their place by rank. An article cut into several chunks is worth
#: reassembling; an entire chapter arriving because one chunk of it ranked is
#: not. A section that will not fit inside the headroom is left unexpanded and
#: its ranked chunk stands alone, so expansion can never evict a result that
#: retrieval actually chose.
_SECTION_HEADROOM = 2

logger = logging.getLogger(__name__)

#: Longest chunk text the embedding request may carry. Stated here rather than
#: borrowed from the chunker's default, so retuning chunk size cannot silently
#: move a persistence-facing safety limit.
MAX_EMBEDDABLE_CHUNK_CHARS = 2_000


@dataclass(frozen=True, slots=True)
class ProjectDocumentChunk:
    """An extracted chunk with the page coordinates a citation must carry."""

    chunk_id: str
    text: str
    page_start: int
    page_end: int
    section: str | None = None

    def __post_init__(self) -> None:
        if not self.chunk_id.strip() or not self.text.strip():
            raise ValueError("project chunks require a non-empty identifier and text")
        if len(self.text) > MAX_EMBEDDABLE_CHUNK_CHARS:
            raise ValueError("project chunks must satisfy the embedding input safety limit")
        if self.page_start < 1 or self.page_end < self.page_start:
            raise ValueError("project chunk page range is invalid")


@dataclass(frozen=True, slots=True)
class ProjectDocumentEvidence:
    """Retrieved private evidence.

    ``score`` is the fused RRF score, not a cosine similarity: the dense and
    lexical legs are combined by rank, so no single-leg score survives fusion.
    Treat it as an ordering, not a calibrated confidence.
    """

    chunk_id: str
    document_id: str
    filename: str
    text: str
    page_start: int
    page_end: int
    section: str | None
    score: float


class ProjectDocumentChunkRepository(Protocol):
    """The Postgres half of retrieval: ACL, lexical ranking, and text."""

    async def replace_document_chunks(
        self, *, document_id: str, project_id: str, chunks: tuple[ChunkInput, ...]
    ) -> tuple[tuple[str, int], ...]: ...

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
    ) -> EligibleChunks: ...

    async def list_section_siblings(
        self, *, vector_ids: tuple[int, ...], allowlist: tuple[int, ...]
    ) -> tuple[tuple[int, int], ...]: ...

    async def hydrate(self, vector_ids: tuple[int, ...]) -> tuple[StoredChunk, ...]: ...

    async def list_document_vector_ids(self, document_id: str) -> tuple[int, ...]: ...

    async def delete_document_chunks(self, document_id: str) -> tuple[int, ...]: ...


class HybridProjectDocumentStore:
    """Turbovec dense leg, Postgres lexical leg, RRF fusion, one ACL."""

    def __init__(
        self,
        chunks: ProjectDocumentChunkRepository,
        indexes: TurbovecProjectIndexStore,
        embedder: EmbeddingPort,
        *,
        vector_size: int = 3072,
    ) -> None:
        if vector_size < 1:
            raise ValueError("vector_size must be positive")
        self._chunks = chunks
        self._indexes = indexes
        self._embedder = embedder
        self._vector_size = vector_size
        self._fusion = ReciprocalRankFusion()

    async def index(
        self,
        *,
        workspace_id: str,
        user_id: str,
        project_id: str,
        document_id: str,
        filename: str,
        expires_at: datetime,
        chunks: tuple[ProjectDocumentChunk, ...],
    ) -> int:
        """Persist chunk text, then add its vectors to the project index.

        Text lands first because ``vector_id`` is assigned by Postgres and is
        the external ID Turbovec needs. If embedding then fails, the rows are
        harmless: the document is not yet ``ready``, and the allowlist is
        intersected with the index before any search.
        """
        if not chunks:
            raise ValueError("empty project extraction must not be indexed")
        require_scope(workspace_id, user_id, project_id)
        if not document_id.strip() or not filename.strip():
            raise ValueError("document coordinates must not be empty")
        if expires_at.astimezone(UTC) <= datetime.now(UTC):
            raise ValueError("expired documents must not be indexed")

        stage_started = perf_counter()
        stage_outcome: ProjectDocumentTimingOutcome = "error"
        try:
            assigned = await self._chunks.replace_document_chunks(
                document_id=document_id,
                project_id=project_id,
                chunks=tuple(
                    ChunkInput(
                        chunk_id=chunk.chunk_id,
                        chunk_index=index,
                        text=chunk.text,
                        page_start=chunk.page_start,
                        page_end=chunk.page_end,
                        section=chunk.section,
                    )
                    for index, chunk in enumerate(chunks)
                ),
            )
            stage_outcome = "success"
        finally:
            log_project_document_timing(
                logger,
                stage="chunk_persistence",
                started=stage_started,
                outcome=stage_outcome,
                document_id=document_id,
                provider=safe_provider_label(self._chunks),
            )
        stage_started = perf_counter()
        stage_outcome = "error"
        try:
            vectors = await self._embedder.embed(
                tuple(chunk.text for chunk in chunks), task="retrieval.passage"
            )
            if len(vectors) != len(chunks) or not vectors or not vectors[0]:
                raise ValueError("embedding response does not match project chunks")
            if any(len(vector) != self._vector_size for vector in vectors):
                raise ValueError("project embedding dimension does not match index configuration")
            stage_outcome = "success"
        finally:
            log_project_document_timing(
                logger,
                stage="embedding",
                started=stage_started,
                outcome=stage_outcome,
                document_id=document_id,
                provider=safe_provider_label(self._embedder),
            )
        by_chunk_id = dict(assigned)
        await self._indexes.add(
            project_id=project_id,
            document_id=document_id,
            vector_ids=[by_chunk_id[chunk.chunk_id] for chunk in chunks],
            vectors=vectors,
        )
        return len(chunks)

    async def embed_query(self, query: str) -> tuple[float, ...]:
        if not query.strip():
            raise ValueError("project document query must not be empty")
        (vector,) = await self._embedder.embed((query,), task="retrieval.query")
        if len(vector) != self._vector_size:
            raise ValueError("project query embedding has an invalid dimension")
        return vector

    async def retrieve(
        self,
        *,
        query: str,
        workspace_id: str,
        user_id: str,
        project_id: str,
        now: datetime,
        document_ids: tuple[str, ...],
        limit: int = 5,
        min_score: float = 0.2,
    ) -> tuple[ProjectDocumentEvidence, ...]:
        if not query.strip() or not document_ids or limit < 1 or not 0 <= min_score <= 1:
            return ()
        require_scope(workspace_id, user_id, project_id)
        # Deliberately precedes embed(): never score another project's vector.
        eligible = await self._eligible(
            workspace_id=workspace_id,
            user_id=user_id,
            project_id=project_id,
            now=now,
            document_ids=document_ids,
            query=query,
            limit=limit,
        )
        if not eligible.allowlist:
            return ()
        vector = await self.embed_query(query)
        return await self._fuse(
            project_id=project_id,
            vector=vector,
            eligible=eligible,
            limit=limit,
            min_score=min_score,
        )

    async def retrieve_vector(
        self,
        *,
        vector: tuple[float, ...],
        query: str,
        workspace_id: str,
        user_id: str,
        project_id: str,
        now: datetime,
        document_ids: tuple[str, ...],
        limit: int = 5,
        min_score: float = 0.2,
    ) -> tuple[ProjectDocumentEvidence, ...]:
        """Search with a pre-computed query vector.

        ``query`` is still required: the lexical leg needs the original text,
        and an embedding cannot be inverted back into one.
        """
        require_scope(workspace_id, user_id, project_id)
        if len(vector) != self._vector_size or not document_ids:
            raise ValueError("project query vector or document selection is invalid")
        eligible = await self._eligible(
            workspace_id=workspace_id,
            user_id=user_id,
            project_id=project_id,
            now=now,
            document_ids=document_ids,
            query=query,
            limit=limit,
        )
        if not eligible.allowlist:
            return ()
        return await self._fuse(
            project_id=project_id,
            vector=vector,
            eligible=eligible,
            limit=limit,
            min_score=min_score,
        )

    async def delete_document(
        self,
        *,
        workspace_id: str,
        user_id: str,
        project_id: str,
        document_id: str,
    ) -> bool:
        """Purge a document from both private stores.

        ``IdMapIndex.remove`` is O(1) by external ID, so the project's index is
        edited in place rather than rebuilt.
        """
        require_scope(workspace_id, user_id, project_id)
        if not document_id.strip():
            raise ValueError("document_id must not be empty")
        vector_ids = await self._chunks.list_document_vector_ids(document_id)
        await self._indexes.remove(project_id=project_id, vector_ids=vector_ids)
        await self._chunks.delete_document_chunks(document_id)
        return True

    async def _eligible(
        self,
        *,
        workspace_id: str,
        user_id: str,
        project_id: str,
        now: datetime,
        document_ids: tuple[str, ...],
        query: str,
        limit: int,
    ) -> EligibleChunks:
        return await self._chunks.list_eligible(
            workspace_id=workspace_id,
            user_id=user_id,
            project_id=project_id,
            document_ids=document_ids,
            now=now,
            query=query,
            lexical_limit=limit * _CANDIDATE_MULTIPLIER,
        )

    async def _fuse(
        self,
        *,
        project_id: str,
        vector: tuple[float, ...],
        eligible: EligibleChunks,
        limit: int,
        min_score: float,
    ) -> tuple[ProjectDocumentEvidence, ...]:
        dense = await self._indexes.search(
            project_id=project_id,
            vector=vector,
            allowlist=eligible.allowlist,
            limit=limit * _CANDIDATE_MULTIPLIER,
        )
        fused = self._fusion.fuse(
            dense_results=[
                (str(vector_id), score) for vector_id, score in dense if score >= min_score
            ],
            bm25_results=[(str(vector_id), 0.0) for vector_id in eligible.lexical],
        )
        ranked = fused[:limit]
        if not ranked:
            return ()
        seeds = {int(candidate.chunk_id): candidate.score for candidate in ranked}
        scores = _with_section_siblings(
            seeds,
            await self._chunks.list_section_siblings(
                vector_ids=tuple(seeds), allowlist=eligible.allowlist
            ),
            limit * _SECTION_HEADROOM,
        )
        stored = await self._chunks.hydrate(tuple(scores))
        by_vector_id = {chunk.vector_id: chunk for chunk in stored}
        return tuple(
            ProjectDocumentEvidence(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                filename=chunk.filename,
                text=chunk.text,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                section=chunk.section,
                score=scores[chunk.vector_id],
            )
            for chunk in (
                by_vector_id[vector_id] for vector_id in scores if vector_id in by_vector_id
            )
        )


def _with_section_siblings(
    seeds: dict[int, float],
    siblings: Sequence[tuple[int, int]],
    ceiling: int,
) -> dict[int, float]:
    """Widen a fused ranking to whole sections, best-ranked section first.

    Returned in reading order within each section, so an article arrives at the
    model as an article rather than as two disconnected fragments. A sibling
    inherits the fused score of the chunk that pulled it in: it was not ranked
    on its own merit, and ``score`` is already documented as an ordering rather
    than a confidence.
    """
    by_seed: dict[int, list[int]] = {}
    for seed, sibling in siblings:
        by_seed.setdefault(seed, []).append(sibling)

    widened: dict[int, float] = {}
    for seed, score in seeds.items():
        section = by_seed.get(seed) or [seed]
        if len(widened) + sum(1 for item in section if item not in widened) > ceiling:
            widened.setdefault(seed, score)
            continue
        for vector_id in section:
            widened.setdefault(vector_id, score)
    return widened


class ReadyProjectDocumentRepository(Protocol):
    async def list_ready_for_scope(
        self,
        workspace_id: str,
        user_id: str,
        project_id: str,
        *,
        at: datetime,
    ) -> tuple[ProjectDocument, ...]: ...


class CanonicalProjectDocumentRetriever:
    """Postgres-authorized retrieval facade used by the chat memory gateway."""

    def __init__(
        self,
        repository: ReadyProjectDocumentRepository,
        vectors: HybridProjectDocumentStore,
        *,
        top_k: int,
        min_score: float,
        timeout_ms: int,
    ) -> None:
        self._repository = repository
        self._vectors = vectors
        self._top_k = top_k
        self._min_score = min_score
        self._timeout_seconds = timeout_ms / 1000

    async def retrieve(self, request: ProjectDocumentQuery) -> ProjectDocumentResponse:
        try:
            return await asyncio.wait_for(
                self._retrieve_with_retries(request),
                timeout=self._timeout_seconds,
            )
        except TimeoutError:
            return ProjectDocumentResponse((), degraded=True, reason_code="retrieval_timeout")
        except ProjectIndexUnavailable:
            # The project's .tvim is missing or unreadable. Fail closed rather
            # than rebuild: reconstruction means re-embedding every chunk, which
            # does not belong behind a user's query.
            return ProjectDocumentResponse((), degraded=True, reason_code="index_unavailable")
        except Exception:
            return ProjectDocumentResponse((), degraded=True, reason_code="retrieval_unavailable")

    async def _retrieve_with_retries(
        self, request: ProjectDocumentQuery
    ) -> ProjectDocumentResponse:
        ready = await self._repository.list_ready_for_scope(
            request.tenant_id,
            request.user_id,
            request.project_id,
            at=datetime.now(UTC),
        )
        by_id = {document.id: document for document in ready}
        if request.document_ids and not set(request.document_ids).issubset(by_id):
            return ProjectDocumentResponse((), degraded=True, reason_code="document_not_ready")
        document_ids = request.document_ids or tuple(by_id)
        if not document_ids:
            return ProjectDocumentResponse(())
        last_error: Exception | None = None
        query_vector = await self._vectors.embed_query(request.query)
        for _attempt in range(2):
            try:
                evidence = await self._vectors.retrieve_vector(
                    vector=query_vector,
                    query=request.query,
                    workspace_id=request.tenant_id,
                    user_id=request.user_id,
                    project_id=request.project_id,
                    now=datetime.now(UTC),
                    document_ids=document_ids,
                    limit=self._top_k,
                    min_score=self._min_score,
                )
                latest_ready = await self._repository.list_ready_for_scope(
                    request.tenant_id,
                    request.user_id,
                    request.project_id,
                    at=datetime.now(UTC),
                )
                latest_by_id = {document.id: document for document in latest_ready}
                if request.document_ids and not set(request.document_ids).issubset(latest_by_id):
                    return ProjectDocumentResponse(
                        (), degraded=True, reason_code="document_not_ready"
                    )
                return ProjectDocumentResponse(
                    tuple(
                        DomainProjectDocumentEvidence(
                            citation_id=f"project:{item.document_id}:{item.chunk_id}",
                            chunk_id=item.chunk_id,
                            document_id=item.document_id,
                            project_id=request.project_id,
                            title=item.filename,
                            text=item.text,
                            page_start=item.page_start,
                            page_end=item.page_end,
                            section=item.section,
                            score=item.score,
                        )
                        for item in evidence
                        if item.document_id in latest_by_id
                    )
                )
            except ProjectIndexUnavailable:
                raise
            except Exception as exc:
                last_error = exc
        assert last_error is not None
        raise last_error


def require_scope(workspace_id: str, user_id: str, project_id: str) -> None:
    if not all(value.strip() for value in (workspace_id, user_id, project_id)):
        raise ValueError("project retrieval requires workspace, user, and project scope")
