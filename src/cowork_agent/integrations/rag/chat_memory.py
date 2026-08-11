"""Retrieval-only semantic context adapter for AI Chat.

This adapter translates the chat feature's verified namespace into the older
``SemanticMemoryPort`` request contract. It deliberately cannot write to a
semantic store and never makes a generated answer from retrieved evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import NAMESPACE_URL, uuid5

from cowork_agent.domain.chat_contracts import MemoryNamespace, MemoryType, SemanticMemoryQuery
from cowork_agent.domain.target_contracts import (
    RetrievalFilters,
    RetrievalLimits,
    RetrievalStatus,
    SemanticChunk,
    SemanticRetrievalRequest,
    SemanticRetrievalResponse,
)
from cowork_agent.features.ai_chat.memory_gateway import (
    MemorySourceUnavailableError,
    NamespaceAccessDenied,
)
from cowork_agent.features.email_action_plan.ports import SemanticMemoryPort

_SOURCE_LABEL = "current_company_evidence"
_APPROVED_DOCUMENT_STATUSES = ("ready",)


class SemanticChatMemoryAdapter:
    """Read company evidence through the existing retrieval-only memory port."""

    def __init__(self, semantic_memory: SemanticMemoryPort) -> None:
        self._semantic_memory = semantic_memory

    async def read_semantic_context(
        self, namespace: MemoryNamespace, query: SemanticMemoryQuery
    ) -> Mapping[str, object]:
        if namespace.memory_type is not MemoryType.SEMANTIC:
            raise NamespaceAccessDenied("semantic namespace is required")

        request = SemanticRetrievalRequest(
            run_id=_ephemeral_run_id(namespace),
            tenant_id=namespace.tenant_id,
            user_id=namespace.user_id,
            query=query.query,
            knowledge_gaps=(),
            filters=RetrievalFilters(
                tenant_scope=namespace.tenant_id,
                document_status=_APPROVED_DOCUMENT_STATUSES,
            ),
            limits=RetrievalLimits(
                top_k=query.max_items,
                min_score=query.min_score,
                timeout_ms=query.timeout_ms,
            ),
        )
        response = await self._retrieve(request)

        if response.tenant_id != namespace.tenant_id:
            raise NamespaceAccessDenied("semantic response tenant does not match the namespace")
        if response.retrieval_status is RetrievalStatus.AUTHORIZATION_DENIED:
            raise NamespaceAccessDenied("semantic retrieval authorization was denied")
        if response.retrieval_status not in {RetrievalStatus.SUCCESS, RetrievalStatus.NO_RESULTS}:
            raise MemorySourceUnavailableError("semantic retrieval is unavailable")

        if not isinstance(response.chunks, tuple) or any(
            not isinstance(chunk, SemanticChunk) for chunk in response.chunks
        ):
            raise MemorySourceUnavailableError("semantic retrieval returned malformed evidence")
        chunks = response.chunks[: query.max_items]
        return _serialize_context(response.retrieval_status, chunks)

    async def _retrieve(self, request: SemanticRetrievalRequest) -> SemanticRetrievalResponse:
        try:
            response = await self._semantic_memory.retrieve(request)
        except NamespaceAccessDenied:
            raise
        except Exception as exc:
            raise MemorySourceUnavailableError("semantic retrieval is unavailable") from exc
        if not isinstance(response, SemanticRetrievalResponse):
            raise MemorySourceUnavailableError("semantic retrieval returned an invalid response")
        return response


def _ephemeral_run_id(namespace: MemoryNamespace) -> str:
    """Return a deterministic metadata-only identifier, never based on query text."""

    identifier = uuid5(
        NAMESPACE_URL,
        "cowork-agent/chat-semantic/"
        f"{namespace.tenant_id}/{namespace.user_id}/{namespace.session_id}",
    )
    return f"chat-semantic-{identifier.hex}"


def _serialize_context(
    status: RetrievalStatus, chunks: tuple[SemanticChunk, ...]
) -> Mapping[str, object]:
    return {
        "source_label": _SOURCE_LABEL,
        "retrieval_status": status.value,
        "chunks": tuple(chunk.to_dict() for chunk in chunks),
        "citations": tuple(
            {
                "chunk_id": chunk.chunk_id,
                "document_id": chunk.document_id,
                "document_title": chunk.document_title,
                "section": chunk.section,
                "source_url": chunk.source_url,
                "document_version": chunk.document_version,
            }
            for chunk in chunks
        ),
        "scores": tuple(
            {
                "chunk_id": chunk.chunk_id,
                "relevance_score": chunk.relevance_score,
                "rerank_score": chunk.rerank_score,
            }
            for chunk in chunks
        ),
    }
