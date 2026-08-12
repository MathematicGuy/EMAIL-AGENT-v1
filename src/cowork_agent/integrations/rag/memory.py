"""In-process Semantic Memory store over a chunked knowledge corpus.

Implements the retrieval-only ``SemanticMemoryPort`` (PRD-v1 FR-08) for the
local MVP: tenant/user namespace filtering happens BEFORE any scoring, then
cosine similarity over precomputed chunk embeddings (master-comparison §6.4/
§6.5). No retrieval-and-answer; the Generator owns generation.
"""

from __future__ import annotations

import time
import warnings
from collections.abc import Sequence
from uuid import uuid4

import numpy as np

from cowork_agent.domain.target_contracts import (
    RetrievalStatus,
    SemanticChunk,
    SemanticRetrievalRequest,
    SemanticRetrievalResponse,
)

from .embeddings import EmbeddingPort
from .knowledge_base import KnowledgeChunk, KnowledgeDocument


class InRepoSemanticMemory:
    """SemanticMemoryPort over an in-memory, ACL-filtered vector index.

    Deprecated: ``QdrantSemanticMemory`` is the production store. This one
    re-embeds the whole corpus in every process at startup and keeps the
    index in local heap, so it stays only for offline evaluation harnesses.
    """

    def __init__(
        self,
        documents: Sequence[KnowledgeDocument],
        embedder: EmbeddingPort,
        *,
        top_k_default: int = 5,
        min_score_default: float = 0.2,
    ) -> None:
        warnings.warn(
            "InRepoSemanticMemory is deprecated; use QdrantSemanticMemory",
            DeprecationWarning,
            stacklevel=2,
        )
        self._chunks: tuple[KnowledgeChunk, ...] = tuple(
            chunk for document in documents for chunk in document.chunks
        )
        if not self._chunks:
            raise ValueError("InRepoSemanticMemory requires a non-empty corpus")
        self._embedder = embedder
        self._top_k_default = top_k_default
        self._min_score_default = min_score_default
        self._matrix: np.ndarray | None = None

    async def build_index(self) -> None:
        """Embed every chunk once and unit-normalize the matrix."""
        vectors = await self._embedder.embed(
            tuple(chunk.text for chunk in self._chunks), task="retrieval.passage"
        )
        matrix = np.asarray(vectors, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self._matrix = matrix / norms

    async def retrieve(
        self, request: SemanticRetrievalRequest
    ) -> SemanticRetrievalResponse:
        if self._matrix is None:
            raise RuntimeError("build_index() must be called before retrieve()")
        started = time.monotonic()
        # ACL first: namespace filtering precedes any scoring or embedding.
        allowed = [
            (index, chunk)
            for index, chunk in enumerate(self._chunks)
            if chunk.tenant_id == request.filters.tenant_scope
        ]
        if not allowed:
            return _response(request, (), RetrievalStatus.NO_RESULTS, started)
        query_text = _query_text(request)
        try:
            (query_vector,) = await self._embedder.embed(
                (query_text,), task="retrieval.query"
            )
        except Exception:
            return _response(request, (), RetrievalStatus.TIMEOUT, started)

        query = np.asarray(query_vector, dtype=np.float32)
        norm = np.linalg.norm(query)
        if norm == 0:
            return _response(request, (), RetrievalStatus.NO_RESULTS, started)
        query = query / norm

        indices = np.asarray([index for index, _ in allowed])
        scores = self._matrix[indices] @ query
        min_score = (
            request.limits.min_score
            if request.limits.min_score >= 0
            else self._min_score_default
        )
        top_k = request.limits.top_k if request.limits.top_k > 0 else self._top_k_default
        ranked = sorted(
            (
                (float(score), allowed[position][1])
                for position, score in enumerate(scores)
                if float(score) >= min_score
            ),
            key=lambda item: (-item[0], item[1].chunk_id),
        )[:top_k]
        chunks = tuple(
            SemanticChunk(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                document_title=chunk.document_title,
                section=chunk.section,
                text=chunk.text,
                source_url=chunk.source_url,
                document_version=None,
                relevance_score=score,
                rerank_score=None,
            )
            for score, chunk in ranked
        )
        status = RetrievalStatus.SUCCESS if chunks else RetrievalStatus.NO_RESULTS
        return _response(request, chunks, status, started)


def _query_text(request: SemanticRetrievalRequest) -> str:
    parts = [request.query, *request.knowledge_gaps]
    return "\n".join(part for part in parts if part)


def _response(
    request: SemanticRetrievalRequest,
    chunks: tuple[SemanticChunk, ...],
    status: RetrievalStatus,
    started: float,
) -> SemanticRetrievalResponse:
    return SemanticRetrievalResponse(
        query_id=f"q_{uuid4().hex}",
        tenant_id=request.tenant_id,
        chunks=chunks,
        retrieval_status=status,
        latency_ms=max(0, int((time.monotonic() - started) * 1000)),
    )
