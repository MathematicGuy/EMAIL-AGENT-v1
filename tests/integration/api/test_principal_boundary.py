"""Verified Principal boundary: authorization behavior of the local MVP API.

Covers master-comparison §7 V1-M1 item 2: identity comes from the verified
OAuth grant stored on the Mailbox Connection, never from caller-provided
query parameters.
"""

import asyncio
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.routing import BaseRoute

from cowork_agent.api.digest_runs import CreateRunRequest
from cowork_agent.app import create_app
from cowork_agent.config import GMAIL_READONLY_SCOPE
from cowork_agent.domain import DigestRun, MailboxConnection, RunStatus, RunTrigger
from cowork_agent.features.email_action_plan.short_term import ShortTermStore
from cowork_agent.features.email_action_plan.workflow import DigestWorker
from cowork_agent.identity import principal_for_connection
from cowork_agent.integrations.gmail.auth import OAuthStateManager, TokenCipher
from cowork_agent.integrations.gmail.fakes import FakeMailbox, SafeTextAttachmentExtractor
from cowork_agent.integrations.gmail.provider import GmailConnectionService, GmailOAuthGrant
from cowork_agent.integrations.llm.fakes import FakePlanGenerator, FakeRouteClassifier

OWNER_EMAIL = "owner@example.com"
CONNECTION_ID = "mbx-principal"


@pytest.fixture(autouse=True)
def principal_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("GMAIL_CLIENT_ID", "principal.apps.googleusercontent.com")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "principal-secret")
    monkeypatch.setenv(
        "GMAIL_REDIRECT_URI", "http://localhost:8000/v1/mail-todo/oauth/gmail/callback"
    )
    monkeypatch.setenv("GMAIL_SCOPES", GMAIL_READONLY_SCOPE)
    monkeypatch.setenv("GMAIL_CONNECTION_DB_PATH", str(tmp_path / "principal-connections.db"))
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("OAUTH_STATE_SECRET", "principal-state-secret-at-least-32-characters")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("FRONTEND_URL", "")


@asynccontextmanager
async def running_app() -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    app = create_app()
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    try:
        # The digest route now reads the worker through the typed runtime
        # (ADR-013), so the fake worker must land on the composed group.
        control_plane = app.state.runtime.control_plane
        app.state.runtime = replace(
            app.state.runtime,
            email_rag=replace(
                app.state.runtime.email_rag,
                digest_worker=DigestWorker(
                    control_plane.run_repository,
                    control_plane.result_repository,
                    FakeMailbox([]),
                    SafeTextAttachmentExtractor(),
                    FakeRouteClassifier(),
                    FakePlanGenerator(),
                    ShortTermStore(),
                    control_plane.task_repository,
                ),
            ),
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://principal.test"
        )
        await client.__aenter__()
        try:
            yield app, client
        finally:
            await client.__aexit__(None, None, None)
    finally:
        await lifespan.__aexit__(None, None, None)


class FakeOAuthDriver:
    def __init__(self) -> None:
        self.state = ""

    def authorization_url(self, state: str, code_verifier: str) -> str:
        self.state = state
        return f"https://accounts.google.test/auth?state={state}"

    async def exchange(
        self, state: str, authorization_response: str, code_verifier: str
    ) -> GmailOAuthGrant:
        assert state == self.state
        assert "code=test-code" in authorization_response
        return GmailOAuthGrant(OWNER_EMAIL, "refresh-token", (GMAIL_READONLY_SCOPE,))


def _seed_connection(user_id: str, email_address: str) -> MailboxConnection:
    now = datetime.now(UTC)
    return MailboxConnection(
        id=CONNECTION_ID,
        user_id=user_id,
        provider="gmail",
        external_account_id=email_address.lower(),
        email_address=email_address,
        encrypted_refresh_token="not-used-by-fakes",
        scopes=(GMAIL_READONLY_SCOPE,),
        status="active",
        created_at=now,
        updated_at=now,
    )


def test_connect_flow_stores_verified_identity(principal_env) -> None:
    async def scenario() -> None:
        async with running_app() as (app, client):
            settings = app.state.runtime.mailbox.gmail_settings
            # The connect/callback routes now read the Gmail service through
            # the typed mailbox group (ADR-013); swap it there.
            app.state.runtime = replace(
                app.state.runtime,
                mailbox=replace(
                    app.state.runtime.mailbox,
                    gmail_connections=GmailConnectionService(
                        settings,
                        app.state.runtime.control_plane.connection_repository,
                        TokenCipher(settings.token_encryption_key),
                        OAuthStateManager(
                            settings.oauth_state_secret, settings.oauth_state_ttl_seconds
                        ),
                        FakeOAuthDriver(),
                    ),
                ),
            )
            redirect = await client.get("/v1/mail-todo/oauth/gmail/connect", follow_redirects=False)
            assert redirect.status_code == 302
            location = urlparse(redirect.headers["location"])
            state = parse_qs(location.query)["state"][0]
            callback = await client.get(
                "/v1/mail-todo/oauth/gmail/callback",
                params={"state": state, "code": "test-code"},
            )
            assert callback.status_code == 200
            connections = await app.state.runtime.control_plane.connection_repository.list_all()
            assert len(connections) == 1
            connection = connections[0]
            assert connection.user_id == OWNER_EMAIL == connection.email_address
            principal = principal_for_connection(connection)
            assert principal.user_id == OWNER_EMAIL

    asyncio.run(scenario())


def test_oauth_callback_redirects_to_configured_frontend(
    principal_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:5173")

    async def scenario() -> None:
        async with running_app() as (app, client):
            settings = app.state.runtime.mailbox.gmail_settings
            # Typed-runtime override: see the sibling connect-flow test.
            app.state.runtime = replace(
                app.state.runtime,
                mailbox=replace(
                    app.state.runtime.mailbox,
                    gmail_connections=GmailConnectionService(
                        settings,
                        app.state.runtime.control_plane.connection_repository,
                        TokenCipher(settings.token_encryption_key),
                        OAuthStateManager(
                            settings.oauth_state_secret, settings.oauth_state_ttl_seconds
                        ),
                        FakeOAuthDriver(),
                    ),
                ),
            )
            connect = await client.get("/v1/mail-todo/oauth/gmail/connect", follow_redirects=False)
            state = parse_qs(urlparse(connect.headers["location"]).query)["state"][0]
            callback = await client.get(
                "/v1/mail-todo/oauth/gmail/callback",
                params={"state": state, "code": "test-code"},
                follow_redirects=False,
            )
            assert callback.status_code == 302
            location = urlparse(callback.headers["location"])
            assert location.netloc == "localhost:5173"
            assert location.fragment == "dashboard"
            assert parse_qs(location.query) == {
                "page": ["dashboard"],
                "view": ["mail"],
                "gmail": ["connected"],
            }

            denied = await client.get(
                "/v1/mail-todo/oauth/gmail/callback",
                params={"state": "safe", "error": "access_denied"},
                follow_redirects=False,
            )
            assert parse_qs(urlparse(denied.headers["location"]).query)["gmail"] == ["denied"]

    asyncio.run(scenario())


def _mounted_api_routes(routes: Iterable[BaseRoute]) -> list[APIRoute]:
    """Every APIRoute reachable from ``app.routes``, mounted routers included.

    ``include_router`` does not copy a router's routes onto the app: FastAPI
    leaves a lazy proxy in ``app.routes`` and resolves through it per request.
    A plain scan therefore sees only the handlers still declared inline on
    ``create_app`` -- which, after the router extractions, is none of the ones
    this invariant cares about. Reach through each proxy to its own router.
    """
    found: list[APIRoute] = []
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            found.extend(_mounted_api_routes(included.routes))
        elif isinstance(route, APIRoute):
            found.append(route)
    return found


def test_no_route_accepts_caller_provided_identity(principal_env) -> None:
    async def scenario() -> None:
        async with running_app() as (app, _):
            routes = _mounted_api_routes(app.routes)
            # The count is a floor, not the point: it fails loudly if the
            # flattening above ever stops seeing the mounted routers.
            assert len(routes) > 50, len(routes)
            for route in routes:
                parameters = (
                    *route.dependant.path_params,
                    *route.dependant.query_params,
                    *route.dependant.header_params,
                    *route.dependant.cookie_params,
                )
                assert all(param.name != "user_id" for param in parameters), route.path
            assert "user_id" not in CreateRunRequest.model_fields

    asyncio.run(scenario())


def test_legacy_mismatched_identity_is_denied(principal_env) -> None:
    async def scenario() -> None:
        async with running_app() as (app, client):
            connection = _seed_connection(user_id="legacy-caller-id", email_address=OWNER_EMAIL)
            await app.state.runtime.control_plane.connection_repository.upsert(connection)
            legacy_run = DigestRun(
                id="run_legacy",
                user_id="legacy-caller-id",
                mailbox_connection_id=CONNECTION_ID,
                trigger=RunTrigger.ON_DEMAND,
                status=RunStatus.QUEUED,
                query="is:unread in:inbox",
                idempotency_key="legacy-key",
                max_emails=10,
            )
            await app.state.runtime.control_plane.run_repository.save(legacy_run)
            orphan_run = DigestRun(
                id="run_orphan",
                user_id=OWNER_EMAIL,
                mailbox_connection_id="mbx-missing",
                trigger=RunTrigger.ON_DEMAND,
                status=RunStatus.QUEUED,
                query="is:unread in:inbox",
                idempotency_key="orphan-key",
                max_emails=10,
            )
            await app.state.runtime.control_plane.run_repository.save(orphan_run)

            assert (await client.get("/v1/mail-todo/runs/run_legacy")).status_code == 404
            assert (await client.get("/v1/mail-todo/runs/run_legacy/result")).status_code == 404
            assert (await client.get("/v1/mail-todo/runs/run_orphan")).status_code == 404
            assert (
                await client.delete(f"/v1/mail-todo/connections/{CONNECTION_ID}")
            ).status_code == 404
            connections = app.state.runtime.control_plane.connection_repository
            assert await connections.get(CONNECTION_ID) is not None

    asyncio.run(scenario())


def test_create_run_persists_verified_identity(principal_env) -> None:
    async def scenario() -> None:
        async with running_app() as (app, client):
            connection = _seed_connection(user_id=OWNER_EMAIL, email_address=OWNER_EMAIL)
            await app.state.runtime.control_plane.connection_repository.upsert(connection)
            response = await client.post(
                "/v1/mail-todo/runs",
                json={"mailboxConnectionId": CONNECTION_ID},
                headers={"Idempotency-Key": "principal-1"},
            )
            assert response.status_code == 202
            payload = response.json()
            assert payload["statusUrl"] == f"/v1/mail-todo/runs/{payload['id']}"
            assert "user_id" not in payload["statusUrl"]
            run = await app.state.runtime.control_plane.run_repository.get(payload["id"])
            assert run is not None
            assert run.user_id == OWNER_EMAIL == connection.email_address
            assert run.max_emails == 10

    asyncio.run(scenario())


def test_processed_emails_are_development_only(
    principal_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dev-only debug metadata must not reach a production response.

    ``processedEmails`` carries per-message subjects and senders. app.py gates
    it on ``_is_development()`` in two places (run status and run result); this
    pins both, since a regression leaks mailbox metadata to any caller.
    """

    async def scenario() -> None:
        async with running_app() as (app, client):
            await app.state.runtime.control_plane.connection_repository.upsert(
                _seed_connection(user_id=OWNER_EMAIL, email_address=OWNER_EMAIL)
            )
            created = await client.post(
                "/v1/mail-todo/runs",
                json={"mailboxConnectionId": CONNECTION_ID},
                headers={"Idempotency-Key": "dev-metadata"},
            )
            run_id = created.json()["id"]

            assert "processedEmails" in (await client.get(f"/v1/mail-todo/runs/{run_id}")).json()
            assert (
                "processedEmails"
                in (await client.get(f"/v1/mail-todo/runs/{run_id}/result")).json()
            )

            monkeypatch.setenv("APP_ENV", "production")
            assert (
                "processedEmails" not in (await client.get(f"/v1/mail-todo/runs/{run_id}")).json()
            )
            assert (
                "processedEmails"
                not in (await client.get(f"/v1/mail-todo/runs/{run_id}/result")).json()
            )

    asyncio.run(scenario())


def test_run_history_is_scoped_ordered_and_body_free(principal_env) -> None:
    async def scenario() -> None:
        async with running_app() as (app, client):
            connection = _seed_connection(user_id=OWNER_EMAIL, email_address=OWNER_EMAIL)
            await app.state.runtime.control_plane.connection_repository.upsert(connection)
            now = datetime.now(UTC)
            older = DigestRun(
                id="run-old",
                user_id=OWNER_EMAIL,
                mailbox_connection_id=CONNECTION_ID,
                trigger=RunTrigger.ON_DEMAND,
                status=RunStatus.SUCCEEDED,
                query="is:unread",
                idempotency_key="history-old",
                max_emails=20,
                action_items_count=1,
                created_at=now,
                completed_at=now,
            )
            newer = DigestRun(
                id="run-new",
                user_id=OWNER_EMAIL,
                mailbox_connection_id=CONNECTION_ID,
                trigger=RunTrigger.ON_DEMAND,
                status=RunStatus.PARTIAL,
                query="is:unread",
                idempotency_key="history-new",
                max_emails=20,
                emails_matched=3,
                emails_processed=2,
                created_at=now + timedelta(seconds=1),
                completed_at=now + timedelta(seconds=2),
            )
            await app.state.runtime.control_plane.run_repository.create(older)
            await app.state.runtime.control_plane.run_repository.create(newer)

            response = await client.get(
                "/v1/mail-todo/runs",
                params={"mailboxConnectionId": CONNECTION_ID, "limit": 1},
            )
            assert response.status_code == 200
            payload = response.json()
            assert [run["id"] for run in payload["runs"]] == ["run-new"]
            assert payload["runs"][0]["progress"] == {
                "emailsMatched": 3,
                "emailsProcessed": 2,
                "emailsToProcess": 3,
                "maxEmails": 20,
                "actionItemsCount": 0,
            }
            assert "body" not in response.text.lower()

            missing = await client.get(
                "/v1/mail-todo/runs",
                params={"mailboxConnectionId": "missing"},
            )
            assert missing.status_code == 404

    asyncio.run(scenario())
