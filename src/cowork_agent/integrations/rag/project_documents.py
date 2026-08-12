"""Private, project-scoped Qdrant collection for user uploaded documents.

This module intentionally has no company-RAG fallback.  A retrieval filter is
constructed from the verified workspace/user/project coordinates *before* the
query is embedded, so a vector search cannot consider another project's text.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    Range,
    VectorParams,
)

from .embeddings import EmbeddingPort

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

    def __post_init__(self) -> None:
        if not self.chunk_id.strip() or not self.text.strip():
            raise ValueError("project chunks require a non-empty identifier and text")
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
    score: float


class ProjectDocumentVectorStore:
    """Qdrant adapter for the project-documents semantic plane only."""

    def __init__(
        self, client: AsyncQdrantClient, collection_name: str, embedder: EmbeddingPort
    ) -> None:
        if not collection_name.strip():
            raise ValueError("collection_name must not be empty")
        self._client = client
        self._collection_name = collection_name
        self._embedder = embedder

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
        await self._ensure_collection(len(vectors[0]))
        points = [
            PointStruct(
                id=str(uuid5(_POINT_NAMESPACE, f"{document_id}/{chunk.chunk_id}")),
                vector=list(vector),
                payload={
                    "workspace_id": workspace_id,
                    "user_id": user_id,
                    "project_id": project_id,
                    "document_id": document_id,
                    "document_status": "ready",
                    "expires_at_epoch": expiry,
                    "chunk_id": chunk.chunk_id,
                    "filename": filename,
                    "text": chunk.text,
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                },
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        for start in range(0, len(points), _UPSERT_BATCH):
            await self._client.upsert(
                collection_name=self._collection_name, points=points[start : start + _UPSERT_BATCH]
            )
        return len(points)

    async def retrieve(
        self,
        *,
        query: str,
        workspace_id: str,
        user_id: str,
        project_id: str,
        now: datetime,
        limit: int = 5,
        min_score: float = 0.2,
    ) -> tuple[ProjectDocumentEvidence, ...]:
        if not query.strip() or limit < 1 or not 0 <= min_score <= 1:
            return ()
        _require_scope(workspace_id, user_id, project_id)
        # Deliberately precedes embed(): never score another project's vector.
        query_filter = _retrieval_filter(workspace_id, user_id, project_id, now)
        (vector,) = await self._embedder.embed((query,), task="retrieval.query")
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

    async def _ensure_collection(self, vector_size: int) -> None:
        if not await self._client.collection_exists(self._collection_name):
            await self._client.create_collection(
                collection_name=self._collection_name,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
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


def _require_scope(workspace_id: str, user_id: str, project_id: str) -> None:
    if not all(value.strip() for value in (workspace_id, user_id, project_id)):
        raise ValueError("project retrieval requires workspace, user, and project scope")


def _epoch(value: datetime) -> float:
    return value.astimezone(UTC).timestamp()


def _retrieval_filter(
    workspace_id: str, user_id: str, project_id: str, now: datetime
) -> Filter:
    return Filter(
        must=[
            FieldCondition(key="workspace_id", match=MatchValue(value=workspace_id)),
            FieldCondition(key="user_id", match=MatchValue(value=user_id)),
            FieldCondition(key="project_id", match=MatchValue(value=project_id)),
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
        score=float(score),
    )
