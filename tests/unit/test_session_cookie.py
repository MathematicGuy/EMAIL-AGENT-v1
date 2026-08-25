import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse, Response
from starlette.requests import Request

from cowork_agent.api.dependencies import (
    authenticated_principal,
    connection_principal,
    issue_chat_guest_session,
    set_session_cookie,
)
from cowork_agent.composition import CoworkRuntime
from cowork_agent.config import SessionSettings
from cowork_agent.domain import MailboxConnection
from cowork_agent.identity import VerifiedPrincipal


def test_session_cookie_is_httponly_secure_lax_and_never_added_to_redirect_url() -> None:
    response = RedirectResponse("https://app.example.test/dashboard")
    settings = SessionSettings(3600, "cowork_session", True)

    set_session_cookie(response, settings, "opaque-token")

    cookie = response.headers["set-cookie"]
    assert "cowork_session=opaque-token" in cookie
    assert "HttpOnly" in cookie
    assert "Max-Age=3600" in cookie
    assert "Path=/" in cookie
    assert "SameSite=lax" in cookie
    assert "Secure" in cookie
    assert "opaque-token" not in response.headers["location"]


def test_postgres_runtime_rejects_a_request_without_an_opaque_session_cookie() -> None:
    app = FastAPI()
    app.state.runtime = CoworkRuntime(
        reports=None,  # type: ignore[arg-type]
        control_plane=SimpleNamespace(
            session_repository=object(),
            session_settings=SessionSettings(3600, "cowork_session", True),
            identity_repository=None,
            chat_identity_repository=None,
            chat_opaque_session_repository=None,
            connection_repository=None,
        ),
    )
    request = Request({"type": "http", "app": app, "headers": [], "path": "/"})

    try:
        asyncio.run(authenticated_principal(request))
    except HTTPException as exc:
        assert exc.status_code == 401
    else:
        raise AssertionError("missing opaque session cookie must be rejected")


def test_postgres_runtime_never_falls_back_to_mailbox_identity_without_a_cookie() -> None:
    app = FastAPI()
    app.state.runtime = CoworkRuntime(
        reports=None,  # type: ignore[arg-type]
        control_plane=SimpleNamespace(
            session_repository=object(),
            session_settings=SessionSettings(3600, "cowork_session", True),
            identity_repository=None,
            chat_identity_repository=None,
            chat_opaque_session_repository=None,
            connection_repository=None,
        ),
    )
    request = Request({"type": "http", "app": app, "headers": [], "path": "/"})
    now = datetime.now(UTC)
    connection = MailboxConnection(
        id="mbx-1",
        user_id="internal-user",
        provider="gmail",
        external_account_id="owner@example.com",
        email_address="owner@example.com",
        encrypted_refresh_token="encrypted",
        scopes=("scope",),
        status="active",
        created_at=now,
        updated_at=now,
    )

    try:
        asyncio.run(connection_principal(request, connection))
    except HTTPException as exc:
        assert exc.status_code == 401
    else:
        raise AssertionError("session runtime must not authorize from a mailbox record")


def test_guest_bootstrap_preserves_an_existing_opaque_session() -> None:
    principal = VerifiedPrincipal(tenant_id="workspace-1", user_id="guest-1")

    class Sessions:
        async def resolve(self, token: str, *, now: datetime) -> VerifiedPrincipal:
            del now
            assert token == "existing-token"
            return principal

        async def create(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("an existing browser session must not be replaced")

    app = FastAPI()
    app.state.runtime = CoworkRuntime(
        reports=None,  # type: ignore[arg-type]
        control_plane=SimpleNamespace(
            session_repository=Sessions(),
            session_settings=SessionSettings(3600, "cowork_session", True),
            identity_repository=object(),
            chat_identity_repository=None,
            chat_opaque_session_repository=None,
            connection_repository=None,
        ),
    )
    request = Request({
        "type": "http",
        "app": app,
        "headers": [(b"cookie", b"cowork_session=existing-token")],
        "path": "/",
    })
    response = Response(status_code=204)

    asyncio.run(issue_chat_guest_session(request, response))

    assert "set-cookie" not in response.headers
