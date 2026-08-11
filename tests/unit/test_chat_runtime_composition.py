"""Production chat-controller composition tests for optional semantic reads."""

import asyncio
from collections.abc import AsyncIterator

from fastapi import FastAPI

from cowork_agent.app import _chat_controller_factory
from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatMessageRequest,
    MemoryContextResponse,
)
from cowork_agent.domain.target_contracts import (
    RetrievalStatus,
    SemanticChunk,
    SemanticRetrievalResponse,
)
from cowork_agent.features.ai_chat.session_buffer import InMemoryChatSessionBuffer


class RecordingSemanticMemory:
    def __init__(self, response: SemanticRetrievalResponse | Exception) -> None:
        self._response = response
        self.requests: list[object] = []

    async def retrieve(self, request: object) -> SemanticRetrievalResponse:
        self.requests.append(request)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class RecordingReply:
    def __init__(self) -> None:
        self.contexts: list[MemoryContextResponse] = []

    async def stream_reply(
        self,
        request: ChatMessageRequest,
        context: MemoryContextResponse,
    ) -> AsyncIterator[str]:
        del request
        self.contexts.append(context)
        yield "reply"


def _scope() -> ChatMemoryScope:
    return ChatMemoryScope(
        tenant_id="tenant-1",
        user_id="user@example.com",
        session_id="session-1",
    )


def _request(message: str, *, key: str) -> ChatMessageRequest:
    return ChatMessageRequest(
        session_id="session-1",
        user_message=message,
        tool_choices=(),
        idempotency_key=key,
    )


def _response() -> SemanticRetrievalResponse:
    return SemanticRetrievalResponse(
        query_id="provider-query-id",
        tenant_id="tenant-1",
        chunks=(
            SemanticChunk(
                chunk_id="chunk-1",
                document_id="travel-policy",
                document_title="Travel Policy",
                section="Receipts",
                text="Submit receipts within five business days.",
                source_url="https://docs.example.com/travel",
                document_version="2026-08",
                relevance_score=0.91,
                rerank_score=0.88,
            ),
        ),
        retrieval_status=RetrievalStatus.SUCCESS,
        latency_ms=9,
    )


def _controller(
    semantic_memory: RecordingSemanticMemory,
) -> tuple[object, RecordingReply]:
    app = FastAPI()
    reply = RecordingReply()
    app.state.chat_session_buffer = InMemoryChatSessionBuffer(max_turns=4, ttl_seconds=60)
    app.state.chat_profile_repository = None
    app.state.chat_reply = reply

    factory = _chat_controller_factory(app)
    app.state.semantic_memory = semantic_memory
    return factory(_scope()), reply


async def _events(controller: object, request: ChatMessageRequest) -> list[object]:
    return [event async for event in controller.stream_message(request)]


def test_runtime_composition_passes_current_company_evidence_only_through_gateway() -> None:
    semantic_memory = RecordingSemanticMemory(_response())
    controller, reply = _controller(semantic_memory)

    events = asyncio.run(
        _events(controller, _request("What does the company policy say about travel?", key="1"))
    )

    assert [event.event_type.value for event in events] == ["error", "delta", "completed"]
    assert len(semantic_memory.requests) == 1
    assert reply.contexts[0].semantic_context is not None
    assert reply.contexts[0].semantic_context["source_label"] == "current_company_evidence"


def test_runtime_composition_keeps_ordinary_chat_selective() -> None:
    semantic_memory = RecordingSemanticMemory(_response())
    controller, reply = _controller(semantic_memory)

    asyncio.run(_events(controller, _request("Help me plan today.", key="2")))

    assert semantic_memory.requests == []
    assert reply.contexts[0].semantic_context is None


def test_runtime_composition_degrades_when_semantic_retrieval_fails() -> None:
    semantic_memory = RecordingSemanticMemory(RuntimeError("provider unavailable"))
    controller, reply = _controller(semantic_memory)

    events = asyncio.run(
        _events(controller, _request("What does the company procedure say?", key="3"))
    )

    assert [event.event_type.value for event in events] == ["error", "delta", "completed"]
    assert events[0].code == "optional_memory_degraded"
    assert len(semantic_memory.requests) == 1
    assert reply.contexts[0].semantic_context is None
