"""Qdrant-backed Semantic Memory adapter (retrieval-only, PRD-v1 FR-08).

Replaces the in-process vector index with a Qdrant collection while keeping
the same ``SemanticMemoryPort`` seam: chunks, citation metadata, and scores
only, never a generated answer.

Two invariants shape this module:

- Tenant isolation is a server-side payload filter built *before* the query
  is embedded, so a foreign tenant's chunk is never scored (constitution §3).
- Raw email content is never ingested; ``ingest_corpus`` only accepts loaded
  ``KnowledgeDocument`` values from the approved corpus (constitution §16).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from uuid import NAMESPACE_URL, uuid4, uuid5

from langfuse import observe
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from cowork_agent.domain.target_contracts import (
    RetrievalStatus,
    SemanticChunk,
    SemanticRetrievalRequest,
    SemanticRetrievalResponse,
)

from .embeddings import EmbeddingPort
from .knowledge_base import KnowledgeChunk, KnowledgeDocument

logger = logging.getLogger(__name__)

DOCUMENT_STATUS_PAYLOAD_KEY = "document_status"
APPROVED_DOCUMENT_STATUS = "ready"

#: Stable namespace so re-ingesting the same chunk_id overwrites its point.
_POINT_NAMESPACE = uuid5(NAMESPACE_URL, "cowork-agent/rag/chunk")

_UPSERT_BATCH = 128


class QdrantSemanticMemory:
    """SemanticMemoryPort over one Qdrant collection of knowledge chunks."""

    def __init__(
        self,
        client: AsyncQdrantClient,
        collection_name: str,
        embedder: EmbeddingPort,
        *,
        top_k_default: int = 5,
        min_score_default: float = 0.2,
    ) -> None:
        self._client = client
        self._collection_name = collection_name
        self._embedder = embedder
        self._top_k_default = top_k_default
        self._min_score_default = min_score_default

    @observe(as_type="retriever", name="qdrant_semantic_retriever")
    async def retrieve(self, request: SemanticRetrievalRequest) -> SemanticRetrievalResponse:
        started = time.monotonic()
        document_status = request.filters.document_status
        if document_status != (APPROVED_DOCUMENT_STATUS,):
            return _response(request, (), RetrievalStatus.AUTHORIZATION_DENIED, started)
        query_filter = Filter(
            must=[
                FieldCondition(
                    key=DOCUMENT_STATUS_PAYLOAD_KEY,
                    match=MatchAny(any=list(document_status)),
                ),
            ]
        )

        try:
            async with asyncio.timeout(_timeout_seconds(request.limits.timeout_ms)):
                (query_vector,) = await self._embedder.embed(
                    (_query_text(request),), task="retrieval.query"
                )
                min_score = (
                    request.limits.min_score
                    if request.limits.min_score >= 0
                    else self._min_score_default
                )
                top_k = request.limits.top_k if request.limits.top_k > 0 else self._top_k_default
                result = await self._client.query_points(
                    collection_name=self._collection_name,
                    query=list(query_vector),
                    query_filter=query_filter,
                    limit=top_k,
                    score_threshold=min_score,
                    timeout=_qdrant_timeout(request.limits.timeout_ms),
                    with_payload=True,
                )
        except (TimeoutError, UnexpectedResponse, ResponseHandlingException, ValueError) as exc:
            logger.warning(
                "Qdrant retrieval unavailable (%s)",
                type(exc).__name__,
            )
            return _response(request, (), RetrievalStatus.TIMEOUT, started)

        chunks = tuple(
            _to_semantic_chunk(payload, point.score)
            for point in result.points
            if (payload := point.payload) is not None
        )
        status = RetrievalStatus.SUCCESS if chunks else RetrievalStatus.NO_RESULTS
        return _response(request, chunks, status, started)


async def ingest_corpus(
    client: AsyncQdrantClient,
    collection_name: str,
    documents: Sequence[KnowledgeDocument],
    embedder: EmbeddingPort,
    *,
    vector_size: int | None = None,
    reindex: bool = False,
) -> int:
    """(Re)create ``collection_name`` and upsert chunks; returns total count.

    Supports incremental ingestion: when ``reindex=False`` and the collection
    exists, only new/missing documents are embedded and upserted.
    """
    if not documents:
        raise ValueError("Qdrant ingestion requires a non-empty corpus")

    all_chunks = tuple(chunk for document in documents for chunk in document.chunks)
    if not all_chunks:
        raise ValueError("Qdrant ingestion requires a non-empty corpus")

    new_documents: list[KnowledgeDocument] = []
    if not reindex and await client.collection_exists(collection_name):
        info = await client.get_collection(collection_name)
        vectors_config = info.config.params.vectors
        size = getattr(vectors_config, "size", None)
        if size is not None and vector_size is not None and size != vector_size:
            raise ValueError(
                f"Configured vector size {vector_size} does not match the "
                f"embedder output dimension {size}"
            )

        existing_doc_ids: set[str] = set()
        offset = None
        while True:
            res = await client.scroll(
                collection_name=collection_name,
                limit=250,
                offset=offset,
                with_payload=["document_id", "doc_id", "source_url"],
                with_vectors=False,
            )
            points_list = res[0] if isinstance(res, tuple) else res
            offset = res[1] if isinstance(res, tuple) and len(res) > 1 else None
            for point in points_list:
                payload = getattr(point, "payload", None)
                if isinstance(payload, dict):
                    doc_id = payload.get("document_id") or payload.get("doc_id")
                    if doc_id:
                        existing_doc_ids.add(str(doc_id))
            if offset is None:
                break

        for doc in documents:
            if doc.document_id not in existing_doc_ids:
                new_documents.append(doc)
    else:
        new_documents = list(documents)

    if not new_documents and await client.collection_exists(collection_name):
        return len(all_chunks)

    chunks_to_embed = tuple(chunk for doc in new_documents for chunk in doc.chunks)
    if chunks_to_embed:
        vectors = await embedder.embed(
            tuple(chunk.text for chunk in chunks_to_embed), task="retrieval.passage"
        )
        observed_size = len(vectors[0])
        if vector_size is not None and vector_size != observed_size:
            raise ValueError(
                f"Configured vector size {vector_size} does not match the "
                f"embedder output dimension {observed_size}"
            )

        if reindex or not await client.collection_exists(collection_name):
            if await client.collection_exists(collection_name):
                await client.delete_collection(collection_name)
            await client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(size=observed_size, distance=Distance.COSINE),
            )
            for field_name in (DOCUMENT_STATUS_PAYLOAD_KEY,):
                await client.create_payload_index(
                    collection_name=collection_name,
                    field_name=field_name,
                    field_schema=PayloadSchemaType.KEYWORD,
                )

        points = [
            PointStruct(
                id=str(uuid5(_POINT_NAMESPACE, chunk.chunk_id)),
                vector=list(vector),
                payload=_payload(chunk),
            )
            for chunk, vector in zip(chunks_to_embed, vectors, strict=True)
        ]
        for start in range(0, len(points), _UPSERT_BATCH):
            await client.upsert(
                collection_name=collection_name, points=points[start : start + _UPSERT_BATCH]
            )

    return len(all_chunks)


def _payload(chunk: KnowledgeChunk) -> dict[str, object]:
    return {
        DOCUMENT_STATUS_PAYLOAD_KEY: APPROVED_DOCUMENT_STATUS,
        "chunk_id": chunk.chunk_id,
        "document_id": chunk.document_id,
        "document_title": chunk.document_title,
        "section": chunk.section,
        "text": chunk.text,
        "source_url": chunk.source_url,
    }


def _to_semantic_chunk(payload: dict[str, object], score: float) -> SemanticChunk:
    section = payload.get("section")
    return SemanticChunk(
        chunk_id=str(payload["chunk_id"]),
        document_id=str(payload["document_id"]),
        document_title=str(payload["document_title"]),
        section=None if section is None else str(section),
        text=str(payload["text"]),
        source_url=str(payload["source_url"]),
        document_version=None,
        relevance_score=float(score),
        rerank_score=None,
    )


def _query_text(request: SemanticRetrievalRequest) -> str:
    parts = [request.query, *request.knowledge_gaps]
    return "\n".join(part for part in parts if part)


def _timeout_seconds(timeout_ms: int) -> float | None:
    return timeout_ms / 1000 if timeout_ms > 0 else None


def _qdrant_timeout(timeout_ms: int) -> int | None:
    """Use only whole seconds that fit in the caller's remaining budget.

    The surrounding ``asyncio.timeout`` is the authoritative end-to-end
    deadline over embedding and Qdrant; sub-second budgets therefore never get
    rounded up into a longer server-side request.
    """
    if timeout_ms < 1000:
        return None
    return timeout_ms // 1000


def _response(
    request: SemanticRetrievalRequest,
    chunks: tuple[SemanticChunk, ...],
    status: RetrievalStatus,
    started: float,
) -> SemanticRetrievalResponse:
    return SemanticRetrievalResponse(
        query_id=f"q_{uuid4().hex}",
        chunks=chunks,
        retrieval_status=status,
        latency_ms=max(0, int((time.monotonic() - started) * 1000)),
    )
