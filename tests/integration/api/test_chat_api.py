import asyncio
import json
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI, Request

from cowork_agent.api.chat import create_chat_router
from cowork_agent.domain.chat_contracts import ChatMemoryScope, ChatMessageRequest
from cowork_agent.features.ai_chat.controller import (
    ChatController,
    InMemoryChatSessionRegistry,
)
from cowork_agent.features.ai_chat.memory_gateway import MemoryGateway
from cowork_agent.features.ai_chat.session_buffer import InMemoryChatSessionBuffer
from cowork_agent.identity import VerifiedPrincipal


class FakeReply:
    async def stream_reply(
        self, request: ChatMessageRequest, context: object
    ) -> AsyncIterator[str]:
        del request, context
        yield "Hello"
        yield " from chat"


def _app(principal: VerifiedPrincipal | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(create_chat_router())
    app.state.chat_sessions = InMemoryChatSessionRegistry(new_id=lambda: "session-1")
    app.state.chat_controllers = {}
    buffer = InMemoryChatSessionBuffer(max_turns=4, ttl_seconds=60)

    def controller_factory(scope: ChatMemoryScope) -> ChatController:
        return ChatController(
            scope=scope,
            memory=MemoryGateway(scope=scope, session_buffer=buffer),
            reply=FakeReply(),
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
                    "tool_choices": [],
                    "idempotency_key": "idem-1",
                },
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = _events(response.text)
        assert [event["event_type"] for event in events] == [
            "error",
            "delta",
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
                    "tool_choices": [],
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
                    "tool_choices": [],
                    "idempotency_key": "idem-3",
                },
            )

        assert response.status_code == 404

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
