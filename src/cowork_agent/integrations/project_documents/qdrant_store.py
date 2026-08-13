"""Incremental Qdrant index and ACL-first retrieval for Project documents."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    Range,
    VectorParams,
)

from cowork_agent.domain.project_documents import (
    ProjectDocumentChunk,
    ProjectDocumentEvidence,
    ProjectDocumentQuery,
    ProjectDocumentResponse,
)
from cowork_agent.features.user_documents.ports import (
    ProjectDocumentRepositoryPort,
    ProjectRepositoryPort,
)
from cowork_agent.integrations.rag.embeddings import EmbeddingPort

_POINT_NAMESPACE = uuid5(NAMESPACE_URL, "cowork-agent/project-documents")


class DocumentEmbeddingError(RuntimeError):
    pass


class DocumentVectorStoreError(RuntimeError):
    pass


class QdrantProjectDocumentStore:
    def __init__(
        self,
        *,
        client: AsyncQdrantClient,
        collection_name: str,
        embedder: EmbeddingPort,
        projects: ProjectRepositoryPort,
        documents: ProjectDocumentRepositoryPort,
    ) -> None:
        self._client = client
        self._collection = collection_name
        self._embedder = embedder
        self._projects = projects
        self._documents = documents

    async def upsert_chunks(
        self, chunks: Sequence[ProjectDocumentChunk], *, expires_at: datetime
    ) -> int:
        if not chunks:
            raise ValueError("cannot index an empty document")
        try:
            vectors = await self._embedder.embed(tuple(chunk.text for chunk in chunks))
        except Exception as exc:
            raise DocumentEmbeddingError("document embedding failed") from exc
        if len(vectors) != len(chunks) or not vectors or not vectors[0]:
            raise DocumentEmbeddingError("embedding response is incomplete")
        points = [
            PointStruct(
                id=str(uuid5(_POINT_NAMESPACE, chunk.chunk_id)),
                vector=list(vector),
                payload={
                    "tenant_id": chunk.tenant_id,
                    "user_id": chunk.user_id,
                    "project_id": chunk.project_id,
                    "document_id": chunk.document_id,
                    "chunk_id": chunk.chunk_id,
                    "status": "indexing",
                    "expires_at": expires_at.astimezone(UTC).isoformat(),
                    "expires_at_epoch": int(expires_at.timestamp()),
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "section": chunk.section,
                    "text": chunk.text,
                },
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        try:
            await self._ensure_collection(len(vectors[0]))
            for start in range(0, len(points), 128):
                await self._client.upsert(
                    collection_name=self._collection,
                    points=points[start : start + 128],
                    wait=True,
                )
        except Exception as exc:
            raise DocumentVectorStoreError("document vector upsert failed") from exc
        return len(points)

    async def activate(self, document_id: str) -> None:
        try:
            await self._client.set_payload(
                collection_name=self._collection,
                payload={"status": "ready"},
                points=Filter(
                    must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
                ),
                wait=True,
            )
        except Exception as exc:
            raise DocumentVectorStoreError("document vector activation failed") from exc

    async def delete_document(self, document_id: str) -> None:
        if not await self._client.collection_exists(self._collection):
            return
        await self._client.delete(
            collection_name=self._collection,
            points_selector=Filter(
                must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
            ),
            wait=True,
        )

    async def retrieve(self, request: ProjectDocumentQuery) -> ProjectDocumentResponse:
        # Ownership and requested-document subset are proven before embedding I/O.
        project = await self._projects.get_owned(
            request.tenant_id, request.user_id, request.project_id
        )
        if project is None:
            return ProjectDocumentResponse((), reason_code="authorization_denied")
        now = datetime.now(UTC)
        ready = await self._documents.list_ready(
            request.tenant_id, request.user_id, request.project_id, at=now
        )
        ready_ids = {document.document_id for document in ready}
        requested_ids = set(request.document_ids)
        if requested_ids and not requested_ids.issubset(ready_ids):
            return ProjectDocumentResponse((), reason_code="authorization_denied")
        allowed_ids = requested_ids or ready_ids
        if not allowed_ids:
            return ProjectDocumentResponse((), reason_code="no_ready_documents")

        must = [
            FieldCondition(key="tenant_id", match=MatchValue(value=request.tenant_id)),
            FieldCondition(key="user_id", match=MatchValue(value=request.user_id)),
            FieldCondition(key="project_id", match=MatchValue(value=request.project_id)),
            FieldCondition(key="status", match=MatchValue(value="ready")),
            FieldCondition(key="expires_at_epoch", range=Range(gt=int(now.timestamp()))),
            FieldCondition(key="document_id", match=MatchAny(any=sorted(allowed_ids))),
        ]
        query_filter = Filter(must=cast(Any, must))
        try:
            async with asyncio.timeout(request.timeout_ms / 1000):
                (vector,) = await self._embedder.embed((request.query,))
                result = await self._client.query_points(
                    collection_name=self._collection,
                    query=list(vector),
                    query_filter=query_filter,
                    limit=request.top_k,
                    score_threshold=request.min_score,
                    timeout=max(1, request.timeout_ms // 1000),
                    with_payload=True,
                )
        except (TimeoutError, ValueError, UnexpectedResponse, ResponseHandlingException):
            return ProjectDocumentResponse((), degraded=True, reason_code="retrieval_unavailable")

        titles = {document.document_id: document.title for document in ready}
        evidence: list[ProjectDocumentEvidence] = []
        for point in result.points:
            payload = point.payload or {}
            document_id = str(payload.get("document_id", ""))
            if document_id not in allowed_ids:
                continue
            chunk_id = str(payload.get("chunk_id", ""))
            evidence.append(
                ProjectDocumentEvidence(
                    citation_id=f"citation_{uuid5(_POINT_NAMESPACE, chunk_id).hex}",
                    chunk_id=chunk_id,
                    document_id=document_id,
                    project_id=request.project_id,
                    title=titles.get(document_id, "Document"),
                    text=str(payload.get("text", "")),
                    page_start=int(payload.get("page_start", 1)),
                    page_end=int(payload.get("page_end", 1)),
                    section=(str(payload["section"]) if payload.get("section") else None),
                    score=float(point.score),
                )
            )
        return ProjectDocumentResponse(tuple(evidence))

    async def _ensure_collection(self, vector_size: int) -> None:
        if await self._client.collection_exists(self._collection):
            return
        await self._client.create_collection(
            collection_name=self._collection,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )
        fields = {
            "tenant_id": PayloadSchemaType.KEYWORD,
            "user_id": PayloadSchemaType.KEYWORD,
            "project_id": PayloadSchemaType.KEYWORD,
            "document_id": PayloadSchemaType.KEYWORD,
            "status": PayloadSchemaType.KEYWORD,
            "expires_at_epoch": PayloadSchemaType.INTEGER,
        }
        for name, schema in fields.items():
            await self._client.create_payload_index(
                collection_name=self._collection,
                field_name=name,
                field_schema=schema,
            )
