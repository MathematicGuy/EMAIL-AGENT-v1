import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace

import httpx
from fastapi import FastAPI, Request

from cowork_agent.api.chat import create_chat_router
from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatMessageRequest,
    EpisodeTransition,
    TaskEpisode,
)
from cowork_agent.domain.target_contracts import ValidationStatus
from cowork_agent.features.ai_chat.controller import (
    ChatController,
    InMemoryChatSessionRegistry,
)
from cowork_agent.features.ai_chat.generation_context import GenerationContext
from cowork_agent.features.ai_chat.memory_gateway import MemoryGateway
from cowork_agent.features.ai_chat.ports import ChatReplyChunk, ChatTaskProposal
from cowork_agent.features.ai_chat.session_buffer import InMemoryChatSessionBuffer
from cowork_agent.identity import VerifiedPrincipal


class FakeReply:
    def __init__(self) -> None:
        self.calls = 0

    async def stream_reply(
        self, request: ChatMessageRequest, context: GenerationContext
    ) -> AsyncIterator[str | ChatReplyChunk]:
        del request, context
        self.calls += 1
        yield ChatReplyChunk(
            "Hello from chat",
            ChatTaskProposal(
                task_title="Chat task",
                minimal_request_paraphrase="Explicit chat task request",
                action_plan=("Review the task",),
                rag_citations=(),
                missing_information=(),
                model_id="fake-model",
                prompt_version="test",
                confidence=None,
            ),
        )


class EpisodeStore:
    def __init__(self) -> None:
        self.writes: list[TaskEpisode] = []
        self.transitions: list[EpisodeTransition] = []
        self.deletes: list[str] = []

    async def write_task_episode(
        self, namespace: object, episode: TaskEpisode, *, expires_at: object
    ) -> TaskEpisode:
        del namespace, expires_at
        self.writes.append(episode)
        return episode

    async def transition_task_episode(self, transition: EpisodeTransition) -> TaskEpisode | None:
        self.transitions.append(transition)
        for index, episode in enumerate(self.writes):
            if episode.episode_id == transition.episode_id:
                updated = replace(
                    episode,
                    validation_status=transition.to_status,
                    retrieval_eligible=transition.retrieval_eligible,
                    updated_at=transition.transitioned_at,
                )
                self.writes[index] = updated
                return updated
        return None

    async def delete_task_episode(self, namespace: object, *, episode_id: str) -> bool:
        del namespace
        self.deletes.append(episode_id)
        return True


def _app(
    principal: VerifiedPrincipal | None = None,
    episodes: EpisodeStore | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(create_chat_router())
    app.state.chat_sessions = InMemoryChatSessionRegistry(new_id=lambda: "session-1")
    app.state.chat_controllers = {}
    buffer = InMemoryChatSessionBuffer(max_turns=4, ttl_seconds=60)
    reply = FakeReply()
    app.state.chat_test_reply = reply

    def controller_factory(scope: ChatMemoryScope) -> ChatController:
        return ChatController(
            scope=scope,
            memory=MemoryGateway(scope=scope, session_buffer=buffer, episodic_memory=episodes),
            reply=reply,
        )

    app.state.chat_controller_factory = controller_factory
    if principal is not None:

        async def resolve_principal(request: Request) -> VerifiedPrincipal:
            del request
            return principal

        app.state.chat_principal_resolver = resolve_principal
    return app


def _events(body: str) -> list[dict[str, object]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def test_session_message_endpoint_streams_existing_typed_events_in_order() -> None:
    async def scenario() -> None:
        app = _app(VerifiedPrincipal(tenant_id="tenant-1", user_id="user@example.com"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://chat.test"
        ) as client:
            created = await client.post("/v1/cowork/chat/sessions")
            assert created.status_code == 201
            assert created.json() == {"session_id": "session-1", "feature": "ai_chat"}

            response = await client.post(
                "/v1/cowork/chat/sessions/session-1/messages",
                json={
                    "session_id": "session-1",
                    "user_message": "Hello",
                    "idempotency_key": "idem-1",
                },
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = _events(response.text)
        assert [event["event_type"] for event in events] == [
            "error",
            "delta",
            "completed",
        ]
        assert events[0]["code"] == "optional_memory_degraded"
        assert all(event["session_id"] == "session-1" for event in events)

    asyncio.run(scenario())


def test_message_endpoint_rejects_a_path_and_payload_session_mismatch() -> None:
    async def scenario() -> None:
        app = _app(VerifiedPrincipal(tenant_id="tenant-1", user_id="user@example.com"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://chat.test"
        ) as client:
            await client.post("/v1/cowork/chat/sessions")
            response = await client.post(
                "/v1/cowork/chat/sessions/session-1/messages",
                json={
                    "session_id": "session-2",
                    "user_message": "Wrong session",
                    "idempotency_key": "idem-2",
                },
            )

        assert response.status_code == 422

    asyncio.run(scenario())


def test_message_endpoint_hides_a_session_owned_by_another_principal() -> None:
    async def scenario() -> None:
        owner = VerifiedPrincipal(tenant_id="tenant-1", user_id="owner@example.com")
        app = _app(owner)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://chat.test"
        ) as client:
            await client.post("/v1/cowork/chat/sessions")

            async def foreign_principal(request: Request) -> VerifiedPrincipal:
                del request
                return VerifiedPrincipal(tenant_id="tenant-1", user_id="other@example.com")

            app.state.chat_principal_resolver = foreign_principal
            response = await client.post(
                "/v1/cowork/chat/sessions/session-1/messages",
                json={
                    "session_id": "session-1",
                    "user_message": "Not mine",
                    "idempotency_key": "idem-3",
                },
            )

        assert response.status_code == 404

    asyncio.run(scenario())


def test_message_endpoint_rejects_retired_tool_choices_before_controller_dispatch() -> None:
    async def scenario() -> None:
        app = _app(VerifiedPrincipal(tenant_id="tenant-1", user_id="user@example.com"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://chat.test"
        ) as client:
            await client.post("/v1/cowork/chat/sessions")
            response = await client.post(
                "/v1/cowork/chat/sessions/session-1/messages",
                json={
                    "session_id": "session-1",
                    "user_message": "Hello",
                    "tool_choices": ["@Email"],
                    "idempotency_key": "idem-retired-tool",
                },
            )

        assert response.status_code == 422
        assert app.state.chat_test_reply.calls == 0

    asyncio.run(scenario())


def test_chat_api_fails_closed_when_no_verified_principal_resolver_is_configured() -> None:
    async def scenario() -> None:
        app = _app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://chat.test"
        ) as client:
            response = await client.post("/v1/cowork/chat/sessions")

        assert response.status_code == 503
        assert response.json() == {"detail": "Chat identity is unavailable"}

    asyncio.run(scenario())


def test_task_episode_controls_use_only_the_originating_session_and_gateway_lifecycle() -> None:
    async def scenario() -> None:
        episodes = EpisodeStore()
        app = _app(VerifiedPrincipal(tenant_id="tenant-1", user_id="user@example.com"), episodes)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://chat.test"
        ) as client:
            await client.post("/v1/cowork/chat/sessions")
            created = await client.post(
                "/v1/cowork/chat/sessions/session-1/messages",
                json={
                    "session_id": "session-1",
                    "user_message": "Create a task for this request.",
                    "idempotency_key": "task-1",
                },
            )
            assert created.status_code == 200
            assert len(episodes.writes) == 1
            episode_id = episodes.writes[0].episode_id
            events = _events(created.text)
            assert [event["event_type"] for event in events] == [
                "error",
                "delta",
                "memory_citation",
                "completed",
            ]
            assert events[-2]["source_id"] == episode_id

            approved = await client.post(
                f"/v1/cowork/chat/sessions/session-1/task-episodes/{episode_id}/approve"
            )
            rejected_cross_session = await client.post(
                f"/v1/cowork/chat/sessions/session-2/task-episodes/{episode_id}/reject"
            )
            deleted = await client.delete(
                f"/v1/cowork/chat/sessions/session-1/task-episodes/{episode_id}"
            )

        assert approved.status_code == 200
        assert approved.json() == {
            "episode_id": episode_id,
            "validation_status": "user_approved",
            "retrieval_eligible": True,
        }
        assert rejected_cross_session.status_code == 404
        assert deleted.status_code == 204
        assert len(episodes.transitions) == 1
        assert episodes.transitions[0].to_status is ValidationStatus.USER_APPROVED
        assert episodes.deletes == [episode_id]

    asyncio.run(scenario())
