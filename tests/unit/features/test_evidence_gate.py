"""Cohere evidence-gate boundaries for retrieve-first Email RAG."""

from cowork_agent.config import EmailRagQualitySettings
from cowork_agent.domain.target_contracts import (
    RetrievalStatus,
    SemanticChunk,
    SemanticRetrievalResponse,
)
from cowork_agent.features.email_action_plan.evidence import (
    EvidenceStatus,
    assess_retrieval_evidence,
)


def _chunk(chunk_id: str, score: float | None) -> SemanticChunk:
    return SemanticChunk(
        chunk_id=chunk_id,
        document_id="doc",
        document_title="Company document",
        section=None,
        text="content",
        source_url="data/extracted/doc.md",
        document_version=None,
        relevance_score=0.5,
        rerank_score=score,
    )


def _response(
    *chunks: SemanticChunk, status: RetrievalStatus = RetrievalStatus.SUCCESS
) -> SemanticRetrievalResponse:
    return SemanticRetrievalResponse("q1", chunks, status, 1)


def test_score_boundary_and_relative_cutoff_keep_only_supported_chunks() -> None:
    assessment = assess_retrieval_evidence(
        _response(_chunk("top", 0.80), _chunk("kept", 0.68), _chunk("dropped", 0.67)),
        EmailRagQualitySettings(),
    )
    assert assessment.status is EvidenceStatus.SUPPORTED
    assert assessment.top_rerank_score == 0.80
    assert tuple(chunk.chunk_id for chunk in assessment.response.chunks) == ("top", "kept")


def test_minimum_score_boundary_is_inclusive() -> None:
    supported = assess_retrieval_evidence(_response(_chunk("c", 0.300)), EmailRagQualitySettings())
    unsupported = assess_retrieval_evidence(
        _response(_chunk("c", 0.299)), EmailRagQualitySettings()
    )
    assert supported.status is EvidenceStatus.SUPPORTED
    assert unsupported.status is EvidenceStatus.UNSUPPORTED


def test_missing_score_or_retrieval_failure_is_unavailable() -> None:
    assert (
        assess_retrieval_evidence(
            _response(status=RetrievalStatus.NO_RESULTS), EmailRagQualitySettings()
        ).status
        is EvidenceStatus.UNSUPPORTED
    )
    assert (
        assess_retrieval_evidence(_response(_chunk("c", None)), EmailRagQualitySettings()).status
        is EvidenceStatus.UNAVAILABLE
    )
    assert (
        assess_retrieval_evidence(
            _response(status=RetrievalStatus.TIMEOUT), EmailRagQualitySettings()
        ).status
        is EvidenceStatus.UNAVAILABLE
    )
