"""Hybrid in-repo retrieval: dense plus BM25, fused before optional reranking."""

from __future__ import annotations

import time
import warnings
from collections.abc import Sequence
from dataclasses import replace
from typing import Final
from uuid import uuid4

from cowork_agent.domain.target_contracts import (
    RetrievalStatus,
    SemanticChunk,
    SemanticRetrievalRequest,
    SemanticRetrievalResponse,
)

from .bm25 import BM25SearchAdapter
from .embeddings import EmbeddingPort
from .jina_reranker import RerankerPort
from .knowledge_base import KnowledgeChunk, KnowledgeDocument
from .memory import InRepoSemanticMemory
from .rrf import ReciprocalRankFusion

_MIN_CANDIDATE_POOL: Final = 20
_MAX_CANDIDATE_POOL: Final = 100


class HybridSemanticMemory:
    """Compose the existing dense index with lexical fusion and reranking.

    Deprecated: ``QdrantSemanticMemory`` is the production store. This one
    remains for the offline retrieval-evaluation harness, which compares
    dense, BM25, fused, and reranked variants over the committed corpus.
    """

    def __init__(
        self,
        documents: Sequence[KnowledgeDocument],
        embedder: EmbeddingPort,
        *,
        reranker: RerankerPort | None = None,
        top_k_default: int = 5,
        min_score_default: float = 0.2,
    ) -> None:
        warnings.warn(
            "HybridSemanticMemory is deprecated; use QdrantSemanticMemory",
            DeprecationWarning,
            stacklevel=2,
        )
        self._chunks = tuple(chunk for document in documents for chunk in document.chunks)
        if not self._chunks:
            raise ValueError("HybridSemanticMemory requires a non-empty corpus")
        self._chunks_by_id = {chunk.chunk_id: chunk for chunk in self._chunks}
        self._dense = InRepoSemanticMemory(
            documents,
            embedder,
            top_k_default=top_k_default,
            min_score_default=min_score_default,
        )
        self._bm25 = BM25SearchAdapter(self._chunks)
        self._reranker = reranker
        self._top_k_default = top_k_default
        self._rrf = ReciprocalRankFusion()

    async def build_index(self) -> None:
        """Build the delegated dense index once for the static local corpus."""
        await self._dense.build_index()

    async def retrieve(
        self, request: SemanticRetrievalRequest
    ) -> SemanticRetrievalResponse:
        started = time.monotonic()
        # This outer ACL gate guarantees no query embedding or lexical scoring
        # occurs for a tenant that has no visible chunks.
        if not any(chunk.tenant_id == request.filters.tenant_scope for chunk in self._chunks):
            return _response(request, (), RetrievalStatus.NO_RESULTS, started)

        final_top_k = _final_top_k(request, self._top_k_default)
        candidate_limit = _candidate_limit(final_top_k)
        candidate_request = replace(
            request,
            limits=replace(request.limits, top_k=candidate_limit),
        )
        dense_response = await self._dense.retrieve(candidate_request)
        lexical_results = self._bm25.search(
            _query_text(request),
            tenant_id=request.filters.tenant_scope,
            top_k=candidate_limit,
        )
        dense_results = tuple(
            (chunk.chunk_id, chunk.relevance_score) for chunk in dense_response.chunks
        )
        fused = self._rrf.fuse(
            dense_results=dense_results,
            bm25_results=lexical_results,
        )
        candidates = tuple(
            _semantic_chunk(self._chunks_by_id[candidate.chunk_id], candidate.score)
            for candidate in fused
        )
        reranked = (
            await self._reranker.rerank(query=_query_text(request), candidates=candidates)
            if self._reranker is not None
            else candidates
        )
        chunks = reranked[:final_top_k]
        status = (
            RetrievalStatus.SUCCESS
            if chunks
            else (
                RetrievalStatus.TIMEOUT
                if dense_response.retrieval_status is RetrievalStatus.TIMEOUT
                else RetrievalStatus.NO_RESULTS
            )
        )
        return _response(request, chunks, status, started)


def _candidate_limit(final_top_k: int) -> int:
    """Bound each upstream retriever while retaining a stable rerank pool."""
    return min(max(final_top_k * 4, _MIN_CANDIDATE_POOL), _MAX_CANDIDATE_POOL)


def _final_top_k(request: SemanticRetrievalRequest, default: int) -> int:
    requested = request.limits.top_k if request.limits.top_k > 0 else default
    return min(requested, _MAX_CANDIDATE_POOL)


def _query_text(request: SemanticRetrievalRequest) -> str:
    return "\n".join(part for part in (request.query, *request.knowledge_gaps) if part)


def _semantic_chunk(chunk: KnowledgeChunk, relevance_score: float) -> SemanticChunk:
    return SemanticChunk(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        document_title=chunk.document_title,
        section=chunk.section,
        text=chunk.text,
        source_url=chunk.source_url,
        document_version=None,
        relevance_score=relevance_score,
        rerank_score=None,
    )


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
