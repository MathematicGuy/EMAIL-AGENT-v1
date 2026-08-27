"""FastAPI transport for the per-user Google Calendar grant.

The second leg of the connect journey. The Gmail callback mints the session
cookie and then redirects here, so every handler below runs with a principal
already resolved — which is why this callback never creates one
(SPEC-per-user-google-calendar-oauth J5).

The invariant worth holding in mind while reading: **every path out of the
callback preserves what the first leg earned.** A user who denies the calendar
consent, or hits a failure in it, still lands on the frontend logged in with
their mail connected. That is J4, and it is the reason each error branch
redirects rather than raising.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from cowork_agent.composition import CalendarRuntime, runtime

from .dependencies import authenticated_principal

CONNECT_PATH = "/v1/calendar/oauth/google/connect"


def _calendar(request: Request) -> CalendarRuntime:
    plane = runtime(request).calendar
    if plane is None:
        raise HTTPException(status_code=503, detail="Calendar connections are not configured")
    return plane


def calendar_redirect(frontend_url: str, outcome: str, *, mail: str | None = None) -> Response:
    """Land the browser back on the frontend carrying both outcomes.

    `mail` is passed explicitly by the chained path so a calendar failure cannot
    erase the fact that mail connected. Omitting it is for the standalone entry,
    where this leg is the only thing that happened.
    """

    parts = urlsplit(frontend_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({"page": "dashboard", "view": "mail", "calendar": outcome})
    if mail is not None:
        query["gmail"] = mail
    return RedirectResponse(
        urlunsplit((parts.scheme, parts.netloc, parts.path or "/", urlencode(query), "dashboard")),
        status_code=302,
    )


def _public_connection(connection: Any) -> dict[str, Any]:
    # Never the token, encrypted or otherwise, and never the scopes' grant
    # material -- only what a settings screen needs to render.
    return {
        "id": connection.id,
        "provider": connection.provider,
        "account": connection.external_account_id,
        "calendar_id": connection.calendar_id,
        "timezone": connection.timezone,
        "status": connection.status,
        "connected_at": connection.created_at.isoformat(),
    }


def create_calendar_router() -> APIRouter:
    """Mount the calendar OAuth handshake and its connection read."""

    router = APIRouter(tags=["calendars"])

    @router.get(CONNECT_PATH, response_model=None)
    async def connect_calendar(request: Request) -> Response:
        plane = _calendar(request)
        principal = await authenticated_principal(request, required=False)
        if principal is None:
            # No session means no principal to attach a grant to (J5). Send the
            # browser home rather than starting a consent whose result would
            # have nowhere to go.
            if plane.oauth_settings.frontend_url:
                return calendar_redirect(plane.oauth_settings.frontend_url, "unauthenticated")
            raise HTTPException(status_code=401, detail="Authentication required")
        return RedirectResponse(plane.connections.begin(), status_code=302)

    @router.get("/v1/calendar/oauth/google/callback", response_model=None)
    async def calendar_callback(
        request: Request,
        state: str,
        code: str | None = None,
        error: str | None = None,
    ) -> Response:
        plane = _calendar(request)
        frontend_url = plane.oauth_settings.frontend_url
        principal = await authenticated_principal(request, required=False)

        # J4: from here down, every failure keeps `gmail=connected`. Losing the
        # mail connection because the second consent failed is the single worst
        # outcome this router can produce, and it is invisible unless each
        # branch says so.
        if principal is None:
            return _declined(frontend_url, "unauthenticated", 401, "Authentication required")
        if error:
            return _declined(frontend_url, "denied", 400, f"Calendar OAuth was denied: {error}")
        if not code:
            return _declined(frontend_url, "error", 400, "Missing OAuth authorization code")

        authorization_response = f"{plane.oauth_settings.redirect_uri}?{request.url.query}"
        try:
            connection = await plane.connections.complete(
                state, authorization_response, user_id=principal.user_id
            )
        except Exception as exc:  # noqa: BLE001 - a failed grant must not cost the session
            return _declined(frontend_url, "error", 400, str(exc))

        if frontend_url:
            return calendar_redirect(frontend_url, "connected", mail="connected")
        return JSONResponse({"status": "connected", "connection": _public_connection(connection)})

    @router.get("/v1/calendar/connection")
    async def read_connection(request: Request) -> dict[str, Any]:
        plane = _calendar(request)
        principal = await authenticated_principal(request)
        assert principal is not None
        connection = await plane.repository.get_for_user(principal.user_id)
        return {
            "connected": connection is not None,
            "connection": None if connection is None else _public_connection(connection),
        }

    @router.delete("/v1/calendar/connection")
    async def disconnect_calendar(request: Request) -> dict[str, bool]:
        plane = _calendar(request)
        principal = await authenticated_principal(request)
        assert principal is not None
        # J6: this ends calendar access and touches nothing about mail.
        return {"disconnected": await plane.repository.delete_for_user(principal.user_id)}

    return router


def _declined(frontend_url: str, outcome: str, status: int, detail: str) -> Response:
    if frontend_url:
        return calendar_redirect(frontend_url, outcome, mail="connected")
    raise HTTPException(status_code=status, detail=detail)


__all__ = ["CONNECT_PATH", "calendar_redirect", "create_calendar_router"]
