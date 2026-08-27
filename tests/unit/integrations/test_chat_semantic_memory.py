"""Contract tests for the retrieval-only semantic chat-memory adapter."""

import asyncio
from uuid import NAMESPACE_URL, uuid5

import pytest

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    MemoryNamespace,
    MemoryType,
    SemanticMemoryQuery,
)
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
from cowork_agent.integrations.rag.chat_memory import SemanticChatMemoryAdapter


def _namespace(*, memory_type: MemoryType = MemoryType.SEMANTIC) -> MemoryNamespace:
    return MemoryNamespace(
        scope=ChatMemoryScope(
            user_id="user@example.com",
            session_id="session-1",
        ),
        memory_type=memory_type,
        record_id=None,
        source_id=None,
    )


def _query(*, max_items: int = 2) -> SemanticMemoryQuery:
    return SemanticMemoryQuery(
        query="What is the current travel expense policy?",
        max_items=max_items,
        min_score=0.7,
        timeout_ms=750,
    )


def _chunk(chunk_id: str) -> SemanticChunk:
    return SemanticChunk(
        chunk_id=chunk_id,
        document_id="travel-policy",
        document_title="Travel Expense Policy",
        section="Receipts",
        text="Submit receipts within five business days.",
        source_url="https://docs.example.com/travel",
        document_version="2026-08",
        relevance_score=0.91,
        rerank_score=0.88,
    )


def _run_id_name() -> str:
    return "cowork-agent/chat-semantic/user@example.com/session-1"


class RecordingSemanticMemory:
    def __init__(self, response: SemanticRetrievalResponse | Exception) -> None:
        self.response = response
        self.requests: list[SemanticRetrievalRequest] = []

    async def retrieve(self, request: SemanticRetrievalRequest) -> SemanticRetrievalResponse:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _response(
    *,
    status: RetrievalStatus = RetrievalStatus.SUCCESS,
    chunks: tuple[SemanticChunk, ...] = (_chunk("chunk-1"),),
) -> SemanticRetrievalResponse:
    return SemanticRetrievalResponse(
        query_id="provider-query-id",
        chunks=chunks,
        retrieval_status=status,
        latency_ms=9,
    )


def test_adapter_derives_exact_retrieval_request_and_serializes_current_company_evidence() -> None:
    memory = RecordingSemanticMemory(_response())
    context = asyncio.run(
        SemanticChatMemoryAdapter(memory).read_semantic_context(_namespace(), _query())
    )

    assert memory.requests == [
        SemanticRetrievalRequest(
            run_id=(f"chat-semantic-{uuid5(NAMESPACE_URL, _run_id_name()).hex}"),
            user_id="user@example.com",
            query="What is the current travel expense policy?",
            knowledge_gaps=(),
            filters=RetrievalFilters(document_status=("ready",)),
            limits=RetrievalLimits(top_k=2, min_score=0.7, timeout_ms=750),
        )
    ]
    assert context == {
        "source_label": "current_company_evidence",
        "retrieval_status": "success",
        "chunks": (_chunk("chunk-1").to_dict(),),
        "citations": (
            {
                "chunk_id": "chunk-1",
                "document_id": "travel-policy",
                "document_title": "Travel Expense Policy",
                "section": "Receipts",
                "source_url": "https://docs.example.com/travel",
                "document_version": "2026-08",
            },
        ),
        "scores": ({"chunk_id": "chunk-1", "relevance_score": 0.91, "rerank_score": 0.88},),
    }


def test_adapter_caps_untrusted_provider_overreturn_to_the_typed_query_limit() -> None:
    memory = RecordingSemanticMemory(_response(chunks=(_chunk("one"), _chunk("two"))))

    context = asyncio.run(
        SemanticChatMemoryAdapter(memory).read_semantic_context(_namespace(), _query(max_items=1))
    )

    assert context is not None
    assert [chunk["chunk_id"] for chunk in context["chunks"]] == ["one"]
    assert [citation["chunk_id"] for citation in context["citations"]] == ["one"]
    assert [score["chunk_id"] for score in context["scores"]] == ["one"]


def test_adapter_returns_labeled_no_results_context() -> None:
    memory = RecordingSemanticMemory(_response(status=RetrievalStatus.NO_RESULTS, chunks=()))

    context = asyncio.run(
        SemanticChatMemoryAdapter(memory).read_semantic_context(_namespace(), _query())
    )

    assert context == {
        "source_label": "current_company_evidence",
        "retrieval_status": "no_results",
        "chunks": (),
        "citations": (),
        "scores": (),
    }


@pytest.mark.parametrize(
    "response",
    [
        _response(status=RetrievalStatus.TIMEOUT, chunks=()),
        _response(status=RetrievalStatus.PARTIAL, chunks=()),
        TimeoutError("provider timed out"),
        RuntimeError("provider disconnected"),
    ],
    ids=["timeout_status", "unexpected_partial_status", "timeout_error", "provider_error"],
)
def test_adapter_translates_operational_failures_to_safe_unavailability(
    response: SemanticRetrievalResponse | Exception,
) -> None:
    memory = RecordingSemanticMemory(response)

    with pytest.raises(MemorySourceUnavailableError, match="semantic"):
        asyncio.run(SemanticChatMemoryAdapter(memory).read_semantic_context(_namespace(), _query()))


def test_adapter_fails_closed_on_wrong_namespace_or_provider_authorization_denial() -> None:
    memory = RecordingSemanticMemory(_response(status=RetrievalStatus.AUTHORIZATION_DENIED))
    adapter = SemanticChatMemoryAdapter(memory)

    with pytest.raises(NamespaceAccessDenied, match="semantic"):
        asyncio.run(
            adapter.read_semantic_context(_namespace(memory_type=MemoryType.EPISODIC), _query())
        )
    assert memory.requests == []

    with pytest.raises(NamespaceAccessDenied, match="semantic"):
        asyncio.run(adapter.read_semantic_context(_namespace(), _query()))


def test_adapter_translates_malformed_chunk_shape_to_safe_unavailability() -> None:
    response = _response()
    object.__setattr__(response, "chunks", ("not-a-semantic-chunk",))
    memory = RecordingSemanticMemory(response)

    with pytest.raises(MemorySourceUnavailableError, match="malformed"):
        asyncio.run(SemanticChatMemoryAdapter(memory).read_semantic_context(_namespace(), _query()))
