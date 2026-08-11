"""Production chat-controller composition tests for optional semantic reads."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from cowork_agent.app import _chat_controller_factory, _resolve_chat_principal
from cowork_agent.domain import MailboxConnection
from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatMessageRequest,
    EpisodeCitation,
    EpisodeSourceType,
    TaskEpisode,
)
from cowork_agent.domain.target_contracts import (
    RetrievalStatus,
    SemanticChunk,
    SemanticRetrievalResponse,
    ValidationStatus,
)
from cowork_agent.features.ai_chat.generation_context import GenerationContext
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
        self.contexts: list[GenerationContext] = []

    async def stream_reply(
        self,
        request: ChatMessageRequest,
        context: GenerationContext,
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


class RecordingEpisodes:
    def __init__(self) -> None:
        self.reads: list[object] = []

    async def read_episodes(self, namespace: object, query: object) -> tuple[TaskEpisode, ...]:
        del namespace
        self.reads.append(query)
        return (
            TaskEpisode(
                episode_id="episode-1", record_id="record-1", tenant_id="tenant-1",
                user_id="user@example.com", chat_session_id="earlier-session",
                chat_turn_id="turn-1", creation_reason="explicit_user_task_request",
                task_title="Submit travel report",
                minimal_request_paraphrase="Submit the travel report",
                action_plan=("Collect receipts",),
                rag_citations=(EpisodeCitation("travel-policy", "Travel Policy", None, "https://docs.example.com/travel"),),
                missing_information=(), validation_status=ValidationStatus.USER_APPROVED,
                retrieval_eligible=True,
                source_type=EpisodeSourceType.SYSTEM_GENERATED_CHAT_TASK,
                created_at=datetime(2026, 8, 11, tzinfo=UTC),
                updated_at=datetime(2026, 8, 11, tzinfo=UTC), pipeline_version="v2-m4",
                model_id=None, prompt_version=None, confidence=None,
            ),
        )


class ConnectionRepository:
    def __init__(self, connections: tuple[MailboxConnection, ...]) -> None:
        self._connections = connections

    async def list_all(self) -> tuple[MailboxConnection, ...]:
        return self._connections


def _connection(*, connection_id: str, email: str) -> MailboxConnection:
    created_at = datetime(2026, 8, 11, tzinfo=UTC)
    return MailboxConnection(
        id=connection_id,
        user_id=email,
        provider="gmail",
        external_account_id=email,
        email_address=email,
        encrypted_refresh_token="encrypted",
        scopes=("https://www.googleapis.com/auth/gmail.readonly",),
        status="active",
        created_at=created_at,
        updated_at=created_at,
    )


def _controller(
    semantic_memory: RecordingSemanticMemory,
    episodes: RecordingEpisodes | None = None,
) -> tuple[object, RecordingReply]:
    app = FastAPI()
    reply = RecordingReply()
    app.state.chat_session_buffer = InMemoryChatSessionBuffer(max_turns=4, ttl_seconds=60)
    app.state.chat_profile_repository = None
    app.state.chat_task_episode_repository = episodes
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
    assert reply.contexts[0].current_company_evidence is not None
    assert (
        reply.contexts[0].current_company_evidence.value.source_label
        == "current_company_evidence"
    )


def test_runtime_composition_keeps_ordinary_chat_selective() -> None:
    semantic_memory = RecordingSemanticMemory(_response())
    controller, reply = _controller(semantic_memory)

    asyncio.run(_events(controller, _request("Help me plan today.", key="2")))

    assert semantic_memory.requests == []
    assert reply.contexts[0].current_company_evidence is None


def test_runtime_composition_passes_eligible_cross_session_episodes_as_advisory_context() -> None:
    semantic_memory = RecordingSemanticMemory(_response())
    episodes = RecordingEpisodes()
    controller, reply = _controller(semantic_memory, episodes)

    asyncio.run(_events(controller, _request("Find my previous task about travel.", key="4")))

    assert len(episodes.reads) == 1
    assert reply.contexts[0].advisory_episodes is not None
    assert reply.contexts[0].advisory_episodes.advisory is True
    assert reply.contexts[0].advisory_episodes.value[0].chat_session_id == "earlier-session"


def test_runtime_chat_principal_requires_exactly_one_active_mailbox_connection() -> None:
    def request_for(connections: tuple[MailboxConnection, ...]) -> Request:
        app = FastAPI()
        app.state.connection_repository = ConnectionRepository(connections)
        return Request({"type": "http", "app": app, "headers": []})

    principal = asyncio.run(
        _resolve_chat_principal(
            request_for((_connection(connection_id="1", email="user@example.com"),))
        )
    )

    assert principal.user_id == "user@example.com"
    with pytest.raises(HTTPException) as no_connection:
        asyncio.run(_resolve_chat_principal(request_for(())))
    with pytest.raises(HTTPException) as ambiguous:
        asyncio.run(
            _resolve_chat_principal(
                request_for(
                    (
                        _connection(connection_id="1", email="one@example.com"),
                        _connection(connection_id="2", email="two@example.com"),
                    )
                )
            )
        )
    assert no_connection.value.status_code == ambiguous.value.status_code == 503


def test_runtime_composition_degrades_when_semantic_retrieval_fails() -> None:
    semantic_memory = RecordingSemanticMemory(RuntimeError("provider unavailable"))
    controller, reply = _controller(semantic_memory)

    events = asyncio.run(
        _events(controller, _request("What does the company procedure say?", key="3"))
    )

    assert [event.event_type.value for event in events] == ["error", "delta", "completed"]
    assert events[0].code == "optional_memory_degraded"
    assert len(semantic_memory.requests) == 1
    assert reply.contexts[0].current_company_evidence is None
