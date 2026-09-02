import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
from fastapi import FastAPI, Request, Response

from cowork_agent.api.chat import create_chat_router
from cowork_agent.composition import ChatRuntime, CoworkRuntime
from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatMessageRequest,
    ChatTurn,
    EpisodeTransition,
    MemoryNamespace,
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

    async def read_episodes(self, namespace: object, query: object) -> tuple[TaskEpisode, ...]:
        # A task-creation turn now reads episodic memory so a revision can name
        # the episode it replaces; this double has nothing stored to return.
        del namespace, query
        return ()

    async def write_task_episode(
        self, namespace: object, episode: TaskEpisode, *, expires_at: object
    ) -> TaskEpisode:
        del namespace, expires_at
        self.writes.append(episode)
        return episode

    async def list_episodes(
        self, namespace: object, *, limit: int = 100
    ) -> tuple[TaskEpisode, ...]:
        del namespace, limit
        return tuple(self.writes)

    async def read_task_episode(self, namespace: object, *, episode_id: str) -> TaskEpisode | None:
        session_id = namespace.scope.session_id
        return next(
            (
                episode
                for episode in self.writes
                if episode.episode_id == episode_id and episode.chat_session_id == session_id
            ),
            None,
        )

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


class ProfileStore:
    def __init__(self) -> None:
        self.profile: object = None

    async def read_profile(self, namespace: object) -> object:
        del namespace
        return self.profile

    async def write_profile(self, namespace: object, profile: object) -> object:
        del namespace
        self.profile = profile
        return profile

    async def delete_profile(self, namespace: object) -> bool:
        del namespace
        existed = self.profile is not None
        self.profile = None
        return existed


class HistoryStore:
    def __init__(self) -> None:
        self.turn = ChatTurn(
            turn_id="turn-history-1",
            session_id="session-1",
            user_message="Saved question",
            assistant_message="Saved answer",
            created_at=datetime(2026, 8, 14, tzinfo=UTC),
        )
        self.owner_tenant_id = "tenant-1"
        self.owner_user_id = "user@example.com"

    async def list_turns(
        self, scope: ChatMemoryScope, *, connection: object | None = None
    ) -> tuple[ChatTurn, ...]:
        del connection
        return (self.turn,) if scope.session_id == self.turn.session_id else ()

    async def list_owned_turns(
        self, *, session_id: str, tenant_id: str, user_id: str
    ) -> tuple[ChatMemoryScope, tuple[ChatTurn, ...]] | None:
        if (
            session_id != self.turn.session_id
            or tenant_id != self.owner_tenant_id
            or user_id != self.owner_user_id
        ):
            return None
        scope = ChatMemoryScope(tenant_id=tenant_id, user_id=user_id, session_id=session_id)
        return scope, (self.turn,)

    async def titles_for(self, scopes: tuple[ChatMemoryScope, ...]) -> dict[str, str]:
        return {scope.session_id: "Saved conversation" for scope in scopes}

    async def latest_turns_for(self, scopes: tuple[ChatMemoryScope, ...]) -> dict[str, ChatTurn]:
        return {
            scope.session_id: self.turn
            for scope in scopes
            if scope.session_id == self.turn.session_id
        }


class DurableSessionRegistry:
    """Small async registry double: a durable adapter is the HTTP contract."""

    def __init__(self) -> None:
        self.scope = ChatMemoryScope(
            tenant_id="tenant-1", user_id="user@example.com", session_id="session-1"
        )
        self.created: list[tuple[str, str]] = []
        self.required: list[tuple[str, str, str]] = []

    async def create(self, *, tenant_id: str, user_id: str) -> ChatMemoryScope:
        self.created.append((tenant_id, user_id))
        return self.scope

    async def require(
        self,
        session_id: str,
        *,
        tenant_id: str,
        user_id: str,
        connection: object | None = None,
    ) -> ChatMemoryScope:
        del connection
        self.required.append((session_id, tenant_id, user_id))
        if self.scope != ChatMemoryScope(
            tenant_id=tenant_id, user_id=user_id, session_id=session_id
        ):
            from cowork_agent.features.ai_chat.controller import ChatSessionAccessDenied

            raise ChatSessionAccessDenied(session_id)
        return self.scope

    async def list_for(self, *, tenant_id: str, user_id: str) -> tuple[ChatMemoryScope, ...]:
        return (self.scope,) if (tenant_id, user_id) == ("tenant-1", "user@example.com") else ()


class CancelController:
    def __init__(self) -> None:
        self.cancelled: list[str] = []
        self.cancelled_keys: list[str] = []

    async def cancel_turn(self, turn_id: str) -> bool:
        self.cancelled.append(turn_id)
        return turn_id == "turn-active"

    async def cancel_turn_by_idempotency_key(self, idempotency_key: str) -> bool:
        self.cancelled_keys.append(idempotency_key)
        return idempotency_key == "idem-active"


def _app(
    principal: VerifiedPrincipal | None = None,
    episodes: EpisodeStore | None = None,
    profiles: ProfileStore | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(create_chat_router())
    buffer = InMemoryChatSessionBuffer(max_turns=4, ttl_seconds=60)
    reply = FakeReply()

    def controller_factory(scope: ChatMemoryScope) -> ChatController:
        return ChatController(
            scope=scope,
            memory=MemoryGateway(scope=scope, session_buffer=buffer, episodic_memory=episodes),
            reply=reply,
        )

    resolver = None
    if principal is not None:

        async def resolve_principal(request: Request) -> VerifiedPrincipal:
            del request
            return principal

        resolver = resolve_principal

    # The router reads through the typed runtime seam (ADR-013). The memo
    # cache, the controller factory, and the test-only reply probe stay on
    # ``app.state``: the cache is a request-time write the frozen runtime
    # cannot hold, and the factory seam is rewired in a later slice.
    app.state.chat_controllers = {}
    app.state.chat_test_reply = reply
    app.state.chat_controller_factory = controller_factory
    app.state.runtime = CoworkRuntime(
        reports=None,
        control_plane=SimpleNamespace(
            chat_task_episode_repository=episodes,
            chat_profile_repository=profiles,
            chat_history_repository=None,
            project_repository=None,
        ),
        chat=ChatRuntime(
            chat_memory_settings=None,
            chat_sessions=InMemoryChatSessionRegistry(new_id=lambda: "session-1"),
            chat_session_buffer=buffer,
            memory_metrics=None,
            memory_operation_sink=None,
            user_documents_settings=None,
            ready_document_catalog=None,
            chat_principal_resolver=resolver,
            chat_guest_session_issuer=None,
            chat_reply=reply,
            chat_intent_settings=None,
            chat_routing_service=None,
            chat_tool_runner=None,
        ),
    )
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
        non_activity_events = [event for event in events if event["event_type"] != "activity"]
        assert [event["event_type"] for event in non_activity_events] == [
            "started",
            "error",
            "delta",
            "completed",
        ]
        assert non_activity_events[1]["code"] == "optional_memory_degraded"
        activity_events = [event for event in events if event["event_type"] == "activity"]
        assert activity_events
        assert activity_events[-1]["activities"][-1]["status"] == "completed"
        assert all(event["session_id"] == "session-1" for event in events)

    asyncio.run(scenario())


def test_guest_session_endpoint_sets_an_opaque_http_only_cookie() -> None:
    async def scenario() -> None:
        app = _app()

        async def issue_guest_session(request: Request, response: Response) -> None:
            del request
            response.set_cookie(
                "cowork_session",
                "opaque-guest-token",
                httponly=True,
                secure=True,
                samesite="lax",
            )

        app.state.runtime = replace(
            app.state.runtime,
            chat=replace(app.state.runtime.chat, chat_guest_session_issuer=issue_guest_session),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://chat.test"
        ) as client:
            response = await client.post("/v1/cowork/chat/guest-session")

        assert response.status_code == 204
        cookie = response.headers["set-cookie"]
        assert "cowork_session=opaque-guest-token" in cookie
        assert "HttpOnly" in cookie
        assert "Secure" in cookie
        assert "SameSite=lax" in cookie

    asyncio.run(scenario())


def test_message_endpoint_rebuilds_a_controller_from_an_async_owned_session_scope() -> None:
    async def scenario() -> None:
        principal = VerifiedPrincipal(tenant_id="tenant-1", user_id="user@example.com")
        app = _app(principal)
        registry = DurableSessionRegistry()
        factory_scopes: list[ChatMemoryScope] = []
        original_factory = app.state.chat_controller_factory

        def factory(scope: ChatMemoryScope) -> ChatController:
            factory_scopes.append(scope)
            return original_factory(scope)

        app.state.runtime = replace(
            app.state.runtime, chat=replace(app.state.runtime.chat, chat_sessions=registry)
        )
        app.state.chat_controller_factory = factory
        del app.state.chat_controllers
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://chat.test"
        ) as client:
            created = await client.post("/v1/cowork/chat/sessions")
            response = await client.post(
                "/v1/cowork/chat/sessions/session-1/messages",
                json={
                    "session_id": "session-1",
                    "user_message": "Hello",
                    "idempotency_key": "async-registry",
                },
            )

        assert created.status_code == 201
        assert response.status_code == 200
        assert registry.created == [("tenant-1", "user@example.com")]
        assert registry.required == [("session-1", "tenant-1", "user@example.com")]
        assert factory_scopes == [registry.scope]

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


def test_cancel_endpoint_targets_one_owned_chat_turn() -> None:
    async def scenario() -> None:
        app = _app(VerifiedPrincipal(tenant_id="tenant-1", user_id="user@example.com"))
        controller = CancelController()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://chat.test"
        ) as client:
            await client.post("/v1/cowork/chat/sessions")
            app.state.chat_controllers["session-1"] = controller
            cancelled = await client.post(
                "/v1/cowork/chat/sessions/session-1/turns/turn-active/cancel"
            )
            missing = await client.post(
                "/v1/cowork/chat/sessions/session-1/turns/turn-finished/cancel"
            )

        assert cancelled.status_code == 204
        assert missing.status_code == 404
        assert controller.cancelled == ["turn-active", "turn-finished"]

    asyncio.run(scenario())


def test_cancel_endpoint_accepts_idempotency_key_before_started_event() -> None:
    async def scenario() -> None:
        app = _app(VerifiedPrincipal(tenant_id="tenant-1", user_id="user@example.com"))
        controller = CancelController()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://chat.test"
        ) as client:
            await client.post("/v1/cowork/chat/sessions")
            app.state.chat_controllers["session-1"] = controller
            cancelled = await client.post(
                "/v1/cowork/chat/sessions/session-1/turns/cancel",
                json={"idempotency_key": "idem-active"},
            )

        assert cancelled.status_code == 204
        assert controller.cancelled_keys == ["idem-active"]

    asyncio.run(scenario())


def test_message_endpoint_rejects_oversized_idempotency_key() -> None:
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
                    "idempotency_key": "x" * 129,
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

            app.state.runtime = replace(
                app.state.runtime,
                chat=replace(app.state.runtime.chat, chat_principal_resolver=foreign_principal),
            )
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
            non_activity_events = [event for event in events if event["event_type"] != "activity"]
            assert [event["event_type"] for event in non_activity_events] == [
                "started",
                "error",
                "delta",
                "memory_citation",
                "task_proposal",
                "completed",
            ]
            assert non_activity_events[3]["source_id"] == episode_id
            proposal = non_activity_events[4]["proposal"]
            assert proposal["episode_id"] == episode_id
            assert proposal["task_title"] == "Chat task"
            assert proposal["validation_status"] == "system_generated"

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


def test_session_and_message_read_contracts_return_owned_history() -> None:
    async def scenario() -> None:
        app = _app(VerifiedPrincipal(tenant_id="tenant-1", user_id="user@example.com"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://chat.test"
        ) as client:
            await client.post("/v1/cowork/chat/sessions")
            await client.post(
                "/v1/cowork/chat/sessions/session-1/messages",
                json={
                    "session_id": "session-1",
                    "user_message": "Hello",
                    "idempotency_key": "read-1",
                },
            )

            listed = await client.get("/v1/cowork/chat/sessions")
            history = await client.get("/v1/cowork/chat/sessions/session-1/messages")
            foreign = await client.get("/v1/cowork/chat/sessions/session-9/messages")

        assert listed.status_code == 200
        assert listed.json() == {"sessions": [{"session_id": "session-1", "feature": "ai_chat"}]}
        assert history.status_code == 200
        turns = history.json()["turns"]
        assert [turn["user_message"] for turn in turns] == ["Hello"]
        assert foreign.status_code == 404

    asyncio.run(scenario())


def test_list_messages_omits_rag_evidence_content_unless_requested() -> None:
    from cowork_agent.domain.chat_contracts import ChatRagEvidence, MemoryType

    async def scenario() -> None:
        principal = VerifiedPrincipal(tenant_id="tenant-1", user_id="user@example.com")
        app = _app(principal)
        scope = ChatMemoryScope("tenant-1", "user@example.com", "session-1")
        evidence = ChatRagEvidence(
            source="company_knowledge",
            retrieval_status="success",
            chunk_id="chunk-slim",
            document_id="doc-slim",
            document_title="Policy.md",
            section="Overview",
            source_url=None,
            relevance_score=0.81,
            rerank_score=0.77,
            preview="Short preview of the chunk.",
            content="FULL CHUNK BODY THAT MUST NOT SHIP ON THE LIST PATH.",
        )
        app.state.runtime.chat.chat_session_buffer.append(
            MemoryNamespace(
                scope=scope,
                memory_type=MemoryType.SHORT_TERM,
                record_id="session-1",
                source_id=None,
            ),
            ChatTurn(
                turn_id="turn-slim",
                session_id="session-1",
                user_message="What is the policy?",
                assistant_message="See the retrieved policy.",
                created_at=datetime(2026, 8, 17, tzinfo=UTC),
                rag_evidence=(evidence,),
                retrieval_status="success",
            ),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://chat.test"
        ) as client:
            await client.post("/v1/cowork/chat/sessions")
            slim = await client.get("/v1/cowork/chat/sessions/session-1/messages")
            full = await client.get(
                "/v1/cowork/chat/sessions/session-1/messages",
                params={"include_content": "true"},
            )

        assert slim.status_code == 200
        slim_item = slim.json()["turns"][0]["rag_evidence"][0]
        assert slim_item["preview"] == "Short preview of the chunk."
        assert "content" not in slim_item
        assert full.status_code == 200
        full_item = full.json()["turns"][0]["rag_evidence"][0]
        assert full_item["content"] == "FULL CHUNK BODY THAT MUST NOT SHIP ON THE LIST PATH."

    asyncio.run(scenario())


def test_mail_scan_turn_is_saved_without_calling_the_llm_and_reloads_with_its_card_data() -> None:
    async def scenario() -> None:
        app = _app(VerifiedPrincipal(tenant_id="tenant-1", user_id="user@example.com"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://chat.test"
        ) as client:
            await client.post("/v1/cowork/chat/sessions")
            saved = await client.post(
                "/v1/cowork/chat/sessions/session-1/mail-scans",
                json={
                    "turn_id": "mail-turn-1",
                    "user_message": "@mail",
                    "assistant_message": "Đã quét xong: đã quét 10 email và tạo 5 action item.",
                    "mail_scan": {
                        "status": "succeeded",
                        "emails_matched": 201,
                        "emails_processed": 10,
                        "emails_to_process": 10,
                        "action_items_count": 5,
                    },
                },
            )
            history = await client.get("/v1/cowork/chat/sessions/session-1/messages")

        assert saved.status_code == 201
        assert app.state.chat_test_reply.calls == 0
        assert history.status_code == 200
        assert history.json()["turns"] == [saved.json()]

    asyncio.run(scenario())


def test_mail_scan_activity_lifecycle_is_server_stamped_and_reloads() -> None:
    async def scenario() -> None:
        app = _app(VerifiedPrincipal(tenant_id="tenant-1", user_id="user@example.com"))
        base = {
            "turn_id": "mail-turn-live",
            "idempotency_key": "mail-idem-live",
            "user_message": "@mail",
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://chat.test"
        ) as client:
            await client.post("/v1/cowork/chat/sessions")
            started = await client.post(
                "/v1/cowork/chat/sessions/session-1/mail-scans",
                json={
                    **base,
                    "assistant_message": None,
                    "turn_status": "generating",
                    "mail_scan": {
                        "status": "connecting",
                        "emails_matched": 0,
                        "emails_processed": 0,
                        "emails_to_process": 0,
                    },
                    "activities": [
                        {"code": "checking_mail", "status": "running"},
                        {"code": "processing_email", "status": "pending"},
                        {"code": "preparing_mail_results", "status": "pending"},
                    ],
                },
            )
            running = await client.post(
                "/v1/cowork/chat/sessions/session-1/mail-scans",
                json={
                    **base,
                    "assistant_message": None,
                    "turn_status": "generating",
                    "mail_scan": {
                        "status": "running",
                        "emails_matched": 10,
                        "emails_processed": 4,
                        "emails_to_process": 10,
                    },
                    "activities": [
                        {
                            "code": "checking_mail",
                            "status": "completed",
                            "outcome": "success",
                        },
                        {
                            "code": "processing_email",
                            "status": "running",
                            "detail": {
                                "kind": "emails_processed",
                                "current": 4,
                                "total": 10,
                            },
                        },
                        {"code": "preparing_mail_results", "status": "pending"},
                    ],
                },
            )
            completed = await client.post(
                "/v1/cowork/chat/sessions/session-1/mail-scans",
                json={
                    **base,
                    "assistant_message": "Đã xử lý 10 email và chuẩn bị 3 công việc.",
                    "turn_status": "completed",
                    "mail_scan": {
                        "status": "succeeded",
                        "emails_matched": 10,
                        "emails_processed": 10,
                        "emails_to_process": 10,
                        "action_items_count": 3,
                    },
                    "activities": [
                        {
                            "code": "checking_mail",
                            "status": "completed",
                            "outcome": "success",
                        },
                        {
                            "code": "processing_email",
                            "status": "completed",
                            "outcome": "success",
                            "detail": {
                                "kind": "emails_processed",
                                "current": 10,
                                "total": 10,
                            },
                        },
                        {
                            "code": "preparing_mail_results",
                            "status": "completed",
                            "outcome": "success",
                            "detail": {
                                "kind": "action_items_prepared",
                                "current": 3,
                                "total": 3,
                            },
                        },
                    ],
                },
            )
            history = await client.get("/v1/cowork/chat/sessions/session-1/messages")

        assert started.status_code == running.status_code == completed.status_code == 201
        assert started.json()["status"] == "generating"
        assert started.json()["activities"][0]["started_at"] is not None
        assert (
            running.json()["activities"][0]["started_at"]
            == started.json()["activities"][0]["started_at"]
        )
        saved = completed.json()
        assert saved["status"] == "completed"
        assert saved["completed_at"] is not None
        assert [item["status"] for item in saved["activities"]] == [
            "completed",
            "completed",
            "completed",
        ]
        assert history.json()["turns"] == [saved]
        assert app.state.chat_test_reply.calls == 0

    asyncio.run(scenario())


def test_mail_scan_failure_is_terminalized_and_cannot_regress() -> None:
    async def scenario() -> None:
        app = _app(VerifiedPrincipal(tenant_id="tenant-1", user_id="user@example.com"))
        base = {
            "turn_id": "mail-turn-failed",
            "idempotency_key": "mail-idem-failed",
            "user_message": "@mail",
            "activities": [
                {"code": "checking_mail", "status": "running"},
                {"code": "processing_email", "status": "pending"},
                {"code": "preparing_mail_results", "status": "pending"},
            ],
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://chat.test"
        ) as client:
            await client.post("/v1/cowork/chat/sessions")
            started = await client.post(
                "/v1/cowork/chat/sessions/session-1/mail-scans",
                json={
                    **base,
                    "turn_status": "generating",
                    "mail_scan": {
                        "status": "connecting",
                        "emails_matched": 0,
                        "emails_processed": 0,
                        "emails_to_process": 0,
                    },
                },
            )
            failed = await client.post(
                "/v1/cowork/chat/sessions/session-1/mail-scans",
                json={
                    **base,
                    "turn_status": "failed",
                    "mail_scan": {
                        "status": "failed",
                        "emails_matched": 0,
                        "emails_processed": 0,
                        "emails_to_process": 0,
                    },
                },
            )
            replay = await client.post(
                "/v1/cowork/chat/sessions/session-1/mail-scans",
                json={
                    **base,
                    "turn_status": "failed",
                    "mail_scan": {
                        "status": "failed",
                        "emails_matched": 99,
                        "emails_processed": 0,
                        "emails_to_process": 0,
                    },
                },
            )
            regression = await client.post(
                "/v1/cowork/chat/sessions/session-1/mail-scans",
                json={
                    **base,
                    "turn_status": "generating",
                    "mail_scan": {
                        "status": "running",
                        "emails_matched": 1,
                        "emails_processed": 0,
                        "emails_to_process": 1,
                    },
                },
            )

        assert started.status_code == failed.status_code == replay.status_code == 201
        assert failed.json()["status"] == "failed"
        assert [item["status"] for item in failed.json()["activities"]] == [
            "failed",
            "skipped",
            "skipped",
        ]
        assert replay.json() == failed.json()
        assert regression.status_code == 409

    asyncio.run(scenario())


def test_delete_session_removes_its_history_and_rejects_future_reads() -> None:
    async def scenario() -> None:
        app = _app(VerifiedPrincipal(tenant_id="tenant-1", user_id="user@example.com"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://chat.test"
        ) as client:
            await client.post("/v1/cowork/chat/sessions")
            await client.post(
                "/v1/cowork/chat/sessions/session-1/messages",
                json={
                    "session_id": "session-1",
                    "user_message": "Hello",
                    "idempotency_key": "delete-1",
                },
            )

            deleted = await client.delete("/v1/cowork/chat/sessions/session-1")
            listed = await client.get("/v1/cowork/chat/sessions")
            history = await client.get("/v1/cowork/chat/sessions/session-1/messages")
            repeated_delete = await client.delete("/v1/cowork/chat/sessions/session-1")

        assert deleted.status_code == 204
        assert listed.json() == {"sessions": []}
        assert history.status_code == 404
        assert repeated_delete.status_code == 404

    asyncio.run(scenario())


def test_history_endpoint_reads_durable_turns_and_exposes_the_saved_title() -> None:
    async def scenario() -> None:
        app = _app(VerifiedPrincipal(tenant_id="tenant-1", user_id="user@example.com"))
        app.state.runtime.control_plane.chat_history_repository = HistoryStore()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://chat.test"
        ) as client:
            await client.post("/v1/cowork/chat/sessions")
            listed = await client.get("/v1/cowork/chat/sessions")
            history = await client.get("/v1/cowork/chat/sessions/session-1/messages")

        assert listed.json()["sessions"][0]["title"] == "Saved conversation"
        assert listed.json()["sessions"][0]["latest_turn_status"] == "completed"
        assert listed.json()["sessions"][0]["latest_turn_id"] == "turn-history-1"
        assert history.json()["turns"] == [HistoryStore().turn.to_dict()]

    asyncio.run(scenario())


def test_history_endpoint_hides_durable_turns_from_a_non_owner() -> None:
    async def scenario() -> None:
        app = _app(VerifiedPrincipal(tenant_id="tenant-1", user_id="other@example.com"))
        app.state.runtime.control_plane.chat_history_repository = HistoryStore()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://chat.test"
        ) as client:
            response = await client.get("/v1/cowork/chat/sessions/session-1/messages")

        assert response.status_code == 404

    asyncio.run(scenario())


def test_episode_listing_returns_written_episodes_and_requires_the_store() -> None:
    async def scenario() -> None:
        episodes = EpisodeStore()
        app = _app(VerifiedPrincipal(tenant_id="tenant-1", user_id="user@example.com"), episodes)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://chat.test"
        ) as client:
            await client.post("/v1/cowork/chat/sessions")
            await client.post(
                "/v1/cowork/chat/sessions/session-1/messages",
                json={
                    "session_id": "session-1",
                    "user_message": "Create a task for this request.",
                    "idempotency_key": "list-1",
                },
            )
            listed = await client.get("/v1/cowork/chat/episodes")

        bare = _app(VerifiedPrincipal(tenant_id="tenant-1", user_id="user@example.com"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=bare), base_url="http://chat.test"
        ) as client:
            missing = await client.get("/v1/cowork/chat/episodes")

        assert listed.status_code == 200
        body = listed.json()
        assert len(body["episodes"]) == 1
        assert body["episodes"][0]["episode_id"] == episodes.writes[0].episode_id
        assert missing.status_code == 503

    asyncio.run(scenario())


def test_profile_crud_round_trip_through_the_read_contract() -> None:
    async def scenario() -> None:
        app = _app(
            VerifiedPrincipal(tenant_id="tenant-1", user_id="user@example.com"),
            profiles=ProfileStore(),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://chat.test"
        ) as client:
            before = await client.get("/v1/cowork/chat/profile")
            created = await client.post(
                "/v1/cowork/chat/profile",
                json={"language": "vi", "response_tone": "concise"},
            )
            read = await client.get("/v1/cowork/chat/profile")
            updated = await client.put(
                "/v1/cowork/chat/profile", json={"response_tone": "detailed"}
            )
            oversized = await client.post("/v1/cowork/chat/profile", json={"language": "x" * 201})
            deleted = await client.delete("/v1/cowork/chat/profile")
            after = await client.get("/v1/cowork/chat/profile")

        bare = _app(VerifiedPrincipal(tenant_id="tenant-1", user_id="user@example.com"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=bare), base_url="http://chat.test"
        ) as client:
            missing = await client.get("/v1/cowork/chat/profile")

        assert before.status_code == 404
        assert created.status_code == 201
        assert created.json()["language"] == "vi"
        assert read.status_code == 200
        assert read.json()["response_tone"] == "concise"
        assert updated.status_code == 200
        assert updated.json()["response_tone"] == "detailed"
        assert updated.json()["language"] is None
        assert oversized.status_code == 422
        assert deleted.status_code == 204
        assert after.status_code == 404
        assert missing.status_code == 503

    asyncio.run(scenario())
