"""Null Semantic Memory adapter: structured unavailable retrieval result."""

from uuid import uuid4

from cowork_agent.domain.target_contracts import (
    RetrievalStatus,
    SemanticRetrievalRequest,
    SemanticRetrievalResponse,
)


class NullSemanticMemory:
    """SemanticMemoryPort implementation without any knowledge corpus.

    Always returns a structured empty result so RETRIEVE_RAG candidates
    degrade into Partial Plans with explicit missing information (FR-11)
    instead of failing.
    """

    async def retrieve(self, request: SemanticRetrievalRequest) -> SemanticRetrievalResponse:
        return SemanticRetrievalResponse(
            query_id=f"q_{uuid4().hex}",
            chunks=(),
            retrieval_status=RetrievalStatus.UNAVAILABLE,
            latency_ms=0,
        )
