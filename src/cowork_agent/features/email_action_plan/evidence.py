"""Deterministic evidence gate for company Email RAG."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from cowork_agent.config import EmailRagQualitySettings
from cowork_agent.domain.target_contracts import (
    RetrievalStatus,
    SemanticRetrievalResponse,
)

GATE_VERSION = "email-rag-gate-v1"


class EvidenceStatus(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class EvidenceAssessment:
    status: EvidenceStatus
    response: SemanticRetrievalResponse
    top_rerank_score: float | None


def assess_retrieval_evidence(
    response: SemanticRetrievalResponse,
    settings: EmailRagQualitySettings,
) -> EvidenceAssessment:
    """Accept only Cohere-scored chunks that satisfy the configured gate.

    An unscored result means the reranker was not composed or fell back after
    an upstream error. It is intentionally unavailable, never evidence.
    """
    if response.retrieval_status is RetrievalStatus.NO_RESULTS:
        return EvidenceAssessment(EvidenceStatus.UNSUPPORTED, _empty(response), None)
    if response.retrieval_status is not RetrievalStatus.SUCCESS:
        return EvidenceAssessment(EvidenceStatus.UNAVAILABLE, _empty(response), None)
    if not response.chunks:
        return EvidenceAssessment(EvidenceStatus.UNSUPPORTED, _empty(response), None)
    scores = tuple(chunk.rerank_score for chunk in response.chunks)
    if any(score is None for score in scores):
        return EvidenceAssessment(EvidenceStatus.UNAVAILABLE, _empty(response), None)
    top_score = max(score for score in scores if score is not None)
    cutoff = max(settings.min_rerank_score, top_score * settings.relative_cutoff_ratio)
    accepted = tuple(
        chunk
        for chunk in response.chunks
        if chunk.rerank_score is not None and chunk.rerank_score >= cutoff
    )
    if not accepted:
        return EvidenceAssessment(EvidenceStatus.UNSUPPORTED, _empty(response), top_score)
    return EvidenceAssessment(
        EvidenceStatus.SUPPORTED,
        replace(response, chunks=accepted),
        top_score,
    )


def _empty(response: SemanticRetrievalResponse) -> SemanticRetrievalResponse:
    return replace(response, chunks=(), retrieval_status=RetrievalStatus.NO_RESULTS)
