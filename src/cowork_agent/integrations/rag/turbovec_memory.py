"""Turbovec Vector Index adapter for SemanticMemoryPort using TurboQuant."""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

import numpy as np

try:
    from turbovec import IdMapIndex  # type: ignore[import-untyped]

    TURBOVEC_AVAILABLE = True
except ImportError:
    TURBOVEC_AVAILABLE = False
    IdMapIndex = None

from cowork_agent.domain.target_contracts import (
    RetrievalStatus,
    SemanticChunk,
    SemanticRetrievalRequest,
    SemanticRetrievalResponse,
)

from .embeddings import EmbeddingPort
from .knowledge_base import KnowledgeChunk, KnowledgeDocument

logger = logging.getLogger(__name__)


def _pad_vector_dim(matrix: np.ndarray) -> tuple[np.ndarray, int]:
    """Ensures feature dimension is a positive multiple of 8 required by Turbovec."""
    dim = matrix.shape[1]
    remainder = dim % 8
    if remainder == 0:
        return matrix, dim
    pad_width = 8 - remainder
    padded = np.pad(matrix, ((0, 0), (0, pad_width)), mode="constant", constant_values=0.0)
    return padded, dim + pad_width


class TurbovecSemanticMemory:
    """SemanticMemoryPort adapter backed by Turbovec (4-bit TurboQuant quantization)."""

    def __init__(
        self,
        documents: Sequence[KnowledgeDocument],
        embedder: EmbeddingPort,
        *,
        bit_width: int = 4,
        top_k_default: int = 5,
        min_score_default: float = 0.2,
        index_path: Path | str | None = None,
    ) -> None:
        if not TURBOVEC_AVAILABLE:
            raise RuntimeError("turbovec package is required for TurbovecSemanticMemory")
        self._chunks: tuple[KnowledgeChunk, ...] = tuple(
            chunk for document in documents for chunk in document.chunks
        )
        if not self._chunks:
            raise ValueError("TurbovecSemanticMemory requires a non-empty corpus")
        self._embedder = embedder
        self._bit_width = bit_width
        self._top_k_default = top_k_default
        self._min_score_default = min_score_default
        self._index_path = Path(index_path) if index_path else None
        self._index: IdMapIndex | None = None
        self._padded_dim: int = 0

    async def build_index(self) -> None:
        """Builds or loads snapshot index from disk."""
        if self._index_path and self._index_path.exists():
            try:
                self._index = IdMapIndex.load(str(self._index_path))
                logger.info("Loaded Turbovec index from snapshot: %s", self._index_path)
                return
            except Exception as exc:
                logger.warning("Failed to load Turbovec snapshot (%s), rebuilding...", exc)

        vectors = await self._embedder.embed(tuple(chunk.text for chunk in self._chunks))
        matrix = np.asarray(vectors, dtype=np.float32)

        # Unit normalization for Cosine Similarity inner product
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        normalized = (matrix / norms).astype(np.float32)

        # Pad to multiple of 8 if needed
        padded_matrix, self._padded_dim = _pad_vector_dim(normalized)

        self._index = IdMapIndex(dim=self._padded_dim, bit_width=self._bit_width)
        ids = np.arange(len(self._chunks), dtype=np.uint64)
        self._index.add_with_ids(padded_matrix, ids)

        if self._index_path:
            self._index_path.parent.mkdir(parents=True, exist_ok=True)
            self._index.write(str(self._index_path))
            logger.info("Saved Turbovec index snapshot to: %s", self._index_path)

    async def retrieve(
        self, request: SemanticRetrievalRequest
    ) -> SemanticRetrievalResponse:
        if self._index is None:
            raise RuntimeError("build_index() must be called before retrieve()")
        started = time.monotonic()

        allowed_indices = list(range(len(self._chunks)))
        if not allowed_indices:
            return _response(request, (), RetrievalStatus.NO_RESULTS, started)

        # Embed query text
        query_text = _query_text(request)
        try:
            (query_vector,) = await self._embedder.embed((query_text,))
        except Exception:
            return _response(request, (), RetrievalStatus.TIMEOUT, started)

        query = np.asarray([query_vector], dtype=np.float32)
        norm = np.linalg.norm(query)
        if norm == 0:
            return _response(request, (), RetrievalStatus.NO_RESULTS, started)

        normalized_query = (query / norm).astype(np.float32)
        padded_query, _ = _pad_vector_dim(normalized_query)

        # Turbovec SIMD Search with allowlist filtering
        top_k = request.limits.top_k if request.limits.top_k > 0 else self._top_k_default
        min_score = (
            request.limits.min_score
            if request.limits.min_score >= 0
            else self._min_score_default
        )
        allowed_arr = np.array(allowed_indices, dtype=np.uint64)

        # Turbovec search returns (scores_2d, ids_2d)
        scores_arr, ids_arr = self._index.search(
            padded_query, k=top_k, allowlist=allowed_arr
        )

        raw_scores = scores_arr[0]
        raw_ids = ids_arr[0]

        ranked: list[tuple[float, KnowledgeChunk]] = []
        for score, chunk_idx in zip(raw_scores, raw_ids, strict=False):
            val_score = float(score)
            if val_score >= min_score and chunk_idx < len(self._chunks):
                ranked.append((val_score, self._chunks[chunk_idx]))

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
        chunks=chunks,
        retrieval_status=status,
        latency_ms=max(0, int((time.monotonic() - started) * 1000)),
    )
