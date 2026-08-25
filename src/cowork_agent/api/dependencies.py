"""Request-scoped seams the routers share.

Two kinds of thing live here, and nothing else: typed accessors that read one
group off the composed runtime (ADR-013), and the identity chain built on them
that answers "who is asking, and may they touch this mailbox connection?".

A helper belongs here once a second router needs it. One that only ever serves
a single router stays in that router's module, where its caller can see it.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, Response

from cowork_agent.composition import ControlPlane, runtime
from cowork_agent.config import SessionSettings
from cowork_agent.domain import MailboxConnection
from cowork_agent.identity import (
    ConnectionNotOwnedError,
    OpaqueSessionResolver,
    VerifiedPrincipal,
    create_guest_session,
    ensure_principal_owns_connection,
    principal_for_connection,
    principal_from_opaque_session,
)

#: Where the per-request principal memo lives on ``request.state``.
_RESOLVED_PRINCIPALS = "cowork_resolved_principals"


def _resolved(request: Request) -> dict[str, VerifiedPrincipal]:
    """The principals already resolved for this request.

    A handler that verifies connection ownership resolves the caller, then the
    handler it returns to resolves the caller again -- two session-store reads
    for one cookie that cannot change mid-request. Memoizing on the request
    removes the second read without touching a single call site. Only
    successful resolutions are stored: a miss is a 401 either way, and caching
    one would let a ``required=False`` probe poison a later required read.
    """

    resolved: dict[str, VerifiedPrincipal] | None = getattr(
        request.state, _RESOLVED_PRINCIPALS, None
    )
    if resolved is None:
        resolved = {}
        setattr(request.state, _RESOLVED_PRINCIPALS, resolved)
    return resolved


def control_plane_required(request: Request) -> ControlPlane:
    """The control-plane group, or the loud failure its old direct reads had."""
    control_plane = runtime(request).control_plane
    if control_plane is None:
        raise RuntimeError("the control-plane group is not composed")
    return control_plane


def session_settings(request: Request) -> SessionSettings:
    return control_plane_required(request).session_settings


def set_session_cookie(response: Response, settings: SessionSettings, token: str) -> None:
    """Set the one HttpOnly cookie that carries the opaque session token."""
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def require_owned_connection(
    principal: VerifiedPrincipal, connection: MailboxConnection, *, detail: str
) -> None:
    """Translate the centralized ownership guard into the HTTP 404 contract."""
    try:
        ensure_principal_owns_connection(principal, connection)
    except ConnectionNotOwnedError as exc:
        raise HTTPException(status_code=404, detail=detail) from exc


async def authenticated_principal(
    request: Request, *, required: bool = True
) -> VerifiedPrincipal | None:
    """Resolve the opaque session only in the PostgreSQL multi-user runtime."""
    control_plane = runtime(request).control_plane
    sessions = control_plane.session_repository if control_plane is not None else None
    if sessions is None:
        return None
    return await _principal_from_session(request, sessions, "session", required=required)


async def authenticated_chat_principal(
    request: Request, *, required: bool = True
) -> VerifiedPrincipal | None:
    """Resolve a browser's opaque chat session in either persistence mode."""
    control_plane = runtime(request).control_plane
    sessions = (
        (control_plane.chat_opaque_session_repository or control_plane.session_repository)
        if control_plane is not None
        else None
    )
    if sessions is None:
        return None
    return await _principal_from_session(request, sessions, "chat_session", required=required)


async def _principal_from_session(
    request: Request, sessions: OpaqueSessionResolver, memo_key: str, *, required: bool
) -> VerifiedPrincipal | None:
    """Resolve the opaque session cookie once per request, per session store."""

    resolved = _resolved(request)
    principal = resolved.get(memo_key)
    if principal is None:
        principal = await principal_from_opaque_session(
            request.cookies.get(session_settings(request).cookie_name), sessions
        )
        if principal is not None:
            resolved[memo_key] = principal
    if principal is None and required:
        raise HTTPException(status_code=401, detail="Authentication required")
    return principal


async def issue_chat_guest_session(request: Request, response: Response) -> None:
    """Bootstrap an isolated guest workspace without replacing an existing session."""
    existing = await authenticated_chat_principal(request, required=False)
    if existing is not None:
        return

    control_plane = runtime(request).control_plane
    identities = (
        (control_plane.chat_identity_repository or control_plane.identity_repository)
        if control_plane is not None
        else None
    )
    sessions = (
        (control_plane.chat_opaque_session_repository or control_plane.session_repository)
        if control_plane is not None
        else None
    )
    if identities is None or sessions is None:
        raise HTTPException(status_code=503, detail="Guest chat is unavailable")

    settings = session_settings(request)
    _, token = await create_guest_session(
        identities,
        sessions,
        ttl_seconds=settings.session_ttl_seconds,
    )
    set_session_cookie(response, settings, token)


async def connection_principal(
    request: Request, connection: MailboxConnection
) -> VerifiedPrincipal:
    """Use the opaque session in Postgres mode and legacy identity locally."""
    control_plane = runtime(request).control_plane
    if control_plane is not None and control_plane.session_repository is not None:
        principal = await authenticated_principal(request)
        assert principal is not None
        return principal
    if connection.provider == "outlook":
        # Outlook is an auxiliary mailbox whose verified address may differ from
        # the Gmail identity that owns it. The persisted user_id is that binding.
        return VerifiedPrincipal(user_id=connection.user_id)
    return principal_for_connection(connection)


async def owned_connection(request: Request, connection_id: str, detail: str) -> MailboxConnection:
    repository = control_plane_required(request).connection_repository
    connection = await repository.get(connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail=detail)
    principal = await connection_principal(request, connection)
    require_owned_connection(principal, connection, detail=detail)
    return connection


__all__ = [
    "authenticated_chat_principal",
    "authenticated_principal",
    "connection_principal",
    "control_plane_required",
    "issue_chat_guest_session",
    "owned_connection",
    "require_owned_connection",
    "session_settings",
    "set_session_cookie",
]
