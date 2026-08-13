"""Private, project-scoped Qdrant collection for user uploaded documents.

This module intentionally has no company-RAG fallback.  A retrieval filter is
constructed from the verified workspace/user/project coordinates *before* the
query is embedded, so a vector search cannot consider another project's text.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchAny,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    Range,
    VectorParams,
)

from cowork_agent.domain.project_documents import (
    ProjectDocumentEvidence as DomainProjectDocumentEvidence,
)
from cowork_agent.domain.project_documents import (
    ProjectDocumentQuery,
    ProjectDocumentResponse,
)
from cowork_agent.persistence.repositories.projects import ProjectDocument

from .embeddings import EmbeddingPort
from .markdown_chunking import DEFAULT_MAX_CHARS

_POINT_NAMESPACE = uuid5(NAMESPACE_URL, "cowork-agent/project-document-chunk")
_UPSERT_BATCH = 128
_REQUIRED_KEYWORD_INDEXES = (
    "workspace_id",
    "user_id",
    "project_id",
    "document_id",
    "document_status",
)


@dataclass(frozen=True, slots=True)
class ProjectDocumentChunk:
    """Extracted text retained only in the vector store, with page coordinates."""

    chunk_id: str
    text: str
    page_start: int
    page_end: int
    section: str | None = None

    def __post_init__(self) -> None:
        if not self.chunk_id.strip() or not self.text.strip():
            raise ValueError("project chunks require a non-empty identifier and text")
        if len(self.text) > DEFAULT_MAX_CHARS:
            raise ValueError("project chunks must satisfy the embedding input safety limit")
        if self.page_start < 1 or self.page_end < self.page_start:
            raise ValueError("project chunk page range is invalid")


@dataclass(frozen=True, slots=True)
class ProjectDocumentEvidence:
    """Retrieved private evidence; callers must not persist its text in Postgres."""

    chunk_id: str
    document_id: str
    filename: str
    text: str
    page_start: int
    page_end: int
    section: str | None
    score: float


class ProjectDocumentVectorStore:
    """Qdrant adapter for the project-documents semantic plane only."""

    def __init__(
        self,
        client: AsyncQdrantClient,
        collection_name: str,
        embedder: EmbeddingPort,
        *,
        vector_size: int = 3072,
    ) -> None:
        if not collection_name.strip():
            raise ValueError("collection_name must not be empty")
        self._client = client
        self._collection_name = collection_name
        self._embedder = embedder
        if vector_size < 1:
            raise ValueError("vector_size must be positive")
        self._vector_size = vector_size

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
        if not chunks:
            raise ValueError("empty project extraction must not be indexed")
        _require_scope(workspace_id, user_id, project_id)
        if not document_id.strip() or not filename.strip():
            raise ValueError("document coordinates must not be empty")
        expiry = _epoch(expires_at)
        if expiry <= datetime.now(UTC).timestamp():
            raise ValueError("expired documents must not be indexed")
        vectors = await self._embedder.embed(
            tuple(chunk.text for chunk in chunks), task="retrieval.passage"
        )
        if len(vectors) != len(chunks) or not vectors or not vectors[0]:
            raise ValueError("embedding response does not match project chunks")
        if any(len(vector) != self._vector_size for vector in vectors):
            raise ValueError("project embedding dimension does not match Qdrant configuration")
        await self.ensure_collection()
        points = [
            PointStruct(
                id=str(uuid5(_POINT_NAMESPACE, f"{document_id}/{chunk.chunk_id}")),
                vector=list(vector),
                payload={
                    "workspace_id": workspace_id,
                    "user_id": user_id,
                    "project_id": project_id,
                    "document_id": document_id,
                    "document_status": "indexing",
                    "expires_at_epoch": expiry,
                    "chunk_id": chunk.chunk_id,
                    "filename": filename,
                    "text": chunk.text,
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "section": chunk.section,
                },
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        for start in range(0, len(points), _UPSERT_BATCH):
            await self._client.upsert(
                collection_name=self._collection_name, points=points[start : start + _UPSERT_BATCH]
            )
        return len(points)

    async def mark_document_ready(
        self,
        *,
        workspace_id: str,
        user_id: str,
        project_id: str,
        document_id: str,
    ) -> None:
        """Publish an indexed document to retrieval after all chunks are durable."""
        _require_scope(workspace_id, user_id, project_id)
        if not document_id.strip():
            raise ValueError("document_id must not be empty")
        await self._client.set_payload(
            collection_name=self._collection_name,
            payload={"document_status": "ready"},
            points=_document_filter(workspace_id, user_id, project_id, document_id),
        )

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
        _require_scope(workspace_id, user_id, project_id)
        # Deliberately precedes embed(): never score another project's vector.
        query_filter = _retrieval_filter(
            workspace_id, user_id, project_id, now, document_ids
        )
        vector = await self.embed_query(query)
        return await self._retrieve_vector(
            vector=vector,
            query_filter=query_filter,
            limit=limit,
            min_score=min_score,
        )

    async def embed_query(self, query: str) -> tuple[float, ...]:
        if not query.strip():
            raise ValueError("project document query must not be empty")
        (vector,) = await self._embedder.embed((query,), task="retrieval.query")
        if len(vector) != self._vector_size:
            raise ValueError("project query embedding has an invalid dimension")
        return vector

    async def retrieve_vector(
        self,
        *,
        vector: tuple[float, ...],
        workspace_id: str,
        user_id: str,
        project_id: str,
        now: datetime,
        document_ids: tuple[str, ...],
        limit: int = 5,
        min_score: float = 0.2,
    ) -> tuple[ProjectDocumentEvidence, ...]:
        _require_scope(workspace_id, user_id, project_id)
        if len(vector) != self._vector_size or not document_ids:
            raise ValueError("project query vector or document selection is invalid")
        return await self._retrieve_vector(
            vector=vector,
            query_filter=_retrieval_filter(
                workspace_id, user_id, project_id, now, document_ids
            ),
            limit=limit,
            min_score=min_score,
        )

    async def _retrieve_vector(
        self,
        *,
        vector: tuple[float, ...],
        query_filter: Filter,
        limit: int,
        min_score: float,
    ) -> tuple[ProjectDocumentEvidence, ...]:
        result = await self._client.query_points(
            collection_name=self._collection_name,
            query=list(vector),
            query_filter=query_filter,
            limit=limit,
            score_threshold=min_score,
            with_payload=True,
        )
        return tuple(
            _evidence(point.payload, point.score)
            for point in result.points
            if point.payload is not None
        )

    async def delete_document(
        self,
        *,
        workspace_id: str,
        user_id: str,
        project_id: str,
        document_id: str,
    ) -> bool:
        _require_scope(workspace_id, user_id, project_id)
        if not document_id.strip():
            raise ValueError("document_id must not be empty")
        if not await self._client.collection_exists(self._collection_name):
            return True
        await self._client.delete(
            collection_name=self._collection_name,
            points_selector=FilterSelector(filter=_document_filter(
                workspace_id, user_id, project_id, document_id
            )),
        )
        return True

    async def ensure_collection(self) -> None:
        """Create the canonical collection or reject an incompatible one."""
        if not await self._client.collection_exists(self._collection_name):
            await self._client.create_collection(
                collection_name=self._collection_name,
                vectors_config=VectorParams(size=self._vector_size, distance=Distance.COSINE),
            )
        else:
            collection = await self._client.get_collection(self._collection_name)
            vectors = collection.config.params.vectors
            size = getattr(vectors, "size", None)
            distance = getattr(vectors, "distance", None)
            if size != self._vector_size or distance != Distance.COSINE:
                raise ValueError(
                    f"project document collection configuration size={size}, distance={distance} "
                    f"does not match size={self._vector_size}, distance={Distance.COSINE}"
                )
        for key in _REQUIRED_KEYWORD_INDEXES:
            await self._client.create_payload_index(
                collection_name=self._collection_name,
                field_name=key,
                field_schema=PayloadSchemaType.KEYWORD,
            )
        await self._client.create_payload_index(
            collection_name=self._collection_name,
            field_name="expires_at_epoch",
            field_schema=PayloadSchemaType.FLOAT,
        )


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
        vectors: ProjectDocumentVectorStore,
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
            return ProjectDocumentResponse(
                (), degraded=True, reason_code="retrieval_timeout"
            )
        except Exception:
            return ProjectDocumentResponse(
                (), degraded=True, reason_code="retrieval_unavailable"
            )

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
            except Exception as exc:
                last_error = exc
        assert last_error is not None
        raise last_error


def _require_scope(workspace_id: str, user_id: str, project_id: str) -> None:
    if not all(value.strip() for value in (workspace_id, user_id, project_id)):
        raise ValueError("project retrieval requires workspace, user, and project scope")


def _epoch(value: datetime) -> float:
    return value.astimezone(UTC).timestamp()


def _retrieval_filter(
    workspace_id: str,
    user_id: str,
    project_id: str,
    now: datetime,
    document_ids: tuple[str, ...],
) -> Filter:
    return Filter(
        must=[
            FieldCondition(key="workspace_id", match=MatchValue(value=workspace_id)),
            FieldCondition(key="user_id", match=MatchValue(value=user_id)),
            FieldCondition(key="project_id", match=MatchValue(value=project_id)),
            FieldCondition(key="document_id", match=MatchAny(any=list(document_ids))),
            FieldCondition(key="document_status", match=MatchValue(value="ready")),
            FieldCondition(key="expires_at_epoch", range=Range(gt=_epoch(now))),
        ]
    )


def _document_filter(
    workspace_id: str, user_id: str, project_id: str, document_id: str
) -> Filter:
    return Filter(
        must=[
            FieldCondition(key="workspace_id", match=MatchValue(value=workspace_id)),
            FieldCondition(key="user_id", match=MatchValue(value=user_id)),
            FieldCondition(key="project_id", match=MatchValue(value=project_id)),
            FieldCondition(key="document_id", match=MatchValue(value=document_id)),
        ]
    )


def _evidence(payload: dict[str, object], score: float) -> ProjectDocumentEvidence:
    return ProjectDocumentEvidence(
        chunk_id=str(payload["chunk_id"]),
        document_id=str(payload["document_id"]),
        filename=str(payload["filename"]),
        text=str(payload["text"]),
        page_start=int(cast(int | str, payload["page_start"])),
        page_end=int(cast(int | str, payload["page_end"])),
        section=str(payload["section"]) if payload.get("section") is not None else None,
        score=float(score),
    )
