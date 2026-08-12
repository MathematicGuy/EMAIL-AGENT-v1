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
from .mmr import mmr_diversify
from .query_guard import is_retrieval_query
from .query_transform import QueryTransformerPort
from .rrf import ReciprocalRankFusion

_MIN_CANDIDATE_POOL: Final = 20
_MAX_CANDIDATE_POOL: Final = 100


class HybridSemanticMemory:
    """Compose the dense index with lexical fusion, multi-query expansion, reranking, and MMR."""

    def __init__(
        self,
        documents: Sequence[KnowledgeDocument],
        embedder: EmbeddingPort,
        *,
        reranker: RerankerPort | None = None,
        query_transformer: QueryTransformerPort | None = None,
        enable_mmr: bool = False,
        lambda_mult: float = 0.7,
        min_rerank_score: float = 0.0,
        relative_cutoff_ratio: float = 0.0,
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
        self._embedder = embedder
        self._dense = InRepoSemanticMemory(
            documents,
            embedder,
            top_k_default=top_k_default,
            min_score_default=min_score_default,
        )
        self._bm25 = BM25SearchAdapter(self._chunks)
        self._reranker = reranker
        self._query_transformer = query_transformer
        self._enable_mmr = enable_mmr
        self._lambda_mult = lambda_mult
        self._min_rerank_score = min_rerank_score
        self._relative_cutoff_ratio = relative_cutoff_ratio
        self._top_k_default = top_k_default
        self._rrf = ReciprocalRankFusion()

    async def build_index(self) -> None:
        """Build the delegated dense index once for the static local corpus."""
        await self._dense.build_index()

    async def retrieve(
        self, request: SemanticRetrievalRequest
    ) -> SemanticRetrievalResponse:
        started = time.monotonic()
        query_str = _query_text(request)
        if not is_retrieval_query(query_str):
            return _response(request, (), RetrievalStatus.NO_RESULTS, started)

        if not any(chunk.tenant_id == request.filters.tenant_scope for chunk in self._chunks):
            return _response(request, (), RetrievalStatus.NO_RESULTS, started)

        final_top_k = _final_top_k(request, self._top_k_default)
        candidate_limit = _candidate_limit(final_top_k)

        try:
            # Multi-Query Expansion & Multi-HyDE if transformer present
            queries_to_search = [query_str]
            if self._query_transformer is not None:
                transformed = await self._query_transformer.transform(
                    request.query, request.knowledge_gaps
                )
                for eq in transformed.expanded_queries:
                    if eq != query_str and eq not in queries_to_search:
                        queries_to_search.append(eq)
                queries_to_search.extend(transformed.hypothetical_docs)

            all_dense_results: list[tuple[str, float]] = []
            all_lexical_results: list[tuple[str, float]] = []

            for q_text in queries_to_search:
                q_request = replace(
                    request,
                    query=q_text,
                    knowledge_gaps=(),
                    limits=replace(request.limits, top_k=candidate_limit),
                )
                dense_response = await self._dense.retrieve(q_request)
                lexical = self._bm25.search(
                    q_text,
                    tenant_id=request.filters.tenant_scope,
                    top_k=candidate_limit,
                )
                all_dense_results.extend(
                    (chunk.chunk_id, chunk.relevance_score) for chunk in dense_response.chunks
                )
                all_lexical_results.extend(lexical)

            fused = self._rrf.fuse(
                dense_results=tuple(all_dense_results),
                bm25_results=tuple(all_lexical_results),
            )
            candidates = tuple(
                _semantic_chunk(self._chunks_by_id[candidate.chunk_id], candidate.score)
                for candidate in fused
            )

            reranked = (
                await self._reranker.rerank(query=query_str, candidates=candidates)
                if self._reranker is not None
                else candidates
            )

            # Filter candidates by dynamic relative score cutoff & threshold
            filtered: list[SemanticChunk] = []
            if reranked:
                has_rerank = reranked[0].rerank_score is not None
                if has_rerank:
                    top_score = reranked[0].rerank_score or 0.0
                    min_threshold = max(
                        self._min_rerank_score, top_score * self._relative_cutoff_ratio
                    )
                    for chunk in reranked:
                        if (
                            chunk.rerank_score is not None
                            and chunk.rerank_score >= min_threshold
                        ):
                            filtered.append(chunk)
                else:
                    top_rrf = reranked[0].relevance_score
                    min_rrf_threshold = (
                        top_rrf * self._relative_cutoff_ratio
                        if self._relative_cutoff_ratio > 0.0
                        else 0.0
                    )
                    for chunk in reranked:
                        if chunk.relevance_score >= min_rrf_threshold:
                            filtered.append(chunk)

            filtered_reranked = tuple(filtered)

            # Apply MMR Diversification if enabled
            if self._enable_mmr and filtered_reranked:
                cand_chunks = filtered_reranked[: candidate_limit]
                cand_texts = tuple(c.text for c in cand_chunks)
                cand_vecs = await self._embedder.embed(cand_texts)
                (query_vec,) = await self._embedder.embed((query_str,))
                chunks = mmr_diversify(
                    chunks=cand_chunks,
                    chunk_vectors=cand_vecs,
                    query_vector=query_vec,
                    top_k=final_top_k,
                    lambda_mult=self._lambda_mult,
                )
            else:
                chunks = filtered_reranked[:final_top_k]

            status = RetrievalStatus.SUCCESS if chunks else RetrievalStatus.NO_RESULTS
            return _response(request, chunks, status, started)
        except Exception:
            return _response(request, (), RetrievalStatus.TIMEOUT, started)


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
