"""FastAPI transport for mailbox connections and their OAuth handshakes.

Gmail is the account of record: its OAuth callback is what mints the browser's
opaque session, and an Outlook connection may only be started by an active
Gmail owner. Both callbacks answer a browser, not an API client, so when a
frontend URL is configured every outcome — denied, failed, connected — becomes
a redirect carrying the outcome in the query string, and only a headless
deployment sees the JSON and HTTP error forms.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from cowork_agent.composition import runtime
from cowork_agent.config import GmailSettings, OutlookSettings
from cowork_agent.features.email_action_plan.policies import DEFAULT_QUERY
from cowork_agent.integrations.gmail.provider import GmailConnectionService
from cowork_agent.integrations.mailbox import (
    MailboxNotConnectedError,
    MailboxPermissionDeniedError,
    MailboxReauthRequiredError,
    MailboxTemporaryError,
    ProviderRoutingMailboxAdapter,
)
from cowork_agent.integrations.outlook import OutlookConnectionService

from .dependencies import (
    authenticated_principal,
    connection_principal,
    control_plane_required,
    owned_connection,
    session_settings,
    set_session_cookie,
)


def _connection_service(request: Request) -> GmailConnectionService:
    mailbox_group = runtime(request).mailbox
    if mailbox_group is None:
        raise RuntimeError("the mailbox group is not composed")
    return mailbox_group.gmail_connections



def _outlook_connection_service(request: Request) -> OutlookConnectionService | None:
    mailbox_group = runtime(request).mailbox
    return mailbox_group.outlook_connections if mailbox_group is not None else None



def _gmail_settings(request: Request) -> GmailSettings:
    # Slice 02-8 gave the settings a home: the mailbox group carries them
    # (ADR-013), so the last ``app.state`` settings forward could die. An
    # uncomposed group fails as loudly as the old missing-key read did.
    mailbox_group = runtime(request).mailbox
    if mailbox_group is None:
        raise RuntimeError("the mailbox group is not composed")
    return mailbox_group.gmail_settings



def _outlook_settings(request: Request) -> OutlookSettings | None:
    mailbox_group = runtime(request).mailbox
    return mailbox_group.outlook_settings if mailbox_group is not None else None



def _mailbox(request: Request) -> ProviderRoutingMailboxAdapter:
    mailbox_group = runtime(request).mailbox
    if mailbox_group is None:
        raise RuntimeError("the mailbox group is not composed")
    return mailbox_group.mailbox



def _public_connection(connection: Any) -> dict[str, Any]:
    return {
        "id": connection.id,
        "provider": connection.provider,
        "emailAddress": connection.email_address,
        "scopes": list(connection.scopes),
        "status": connection.status,
        "createdAt": connection.created_at.isoformat(),
    }



def _frontend_mail_redirect(
    frontend_url: str, outcome: str, *, provider: str = "gmail"
) -> RedirectResponse:
    parts = urlsplit(frontend_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({"page": "dashboard", "view": "mail", provider: outcome})
    location = urlunsplit(
        (parts.scheme, parts.netloc, parts.path or "/", urlencode(query), "dashboard")
    )
    return RedirectResponse(location, status_code=302)



def create_mailbox_router() -> APIRouter:
    """Mount the Gmail/Outlook OAuth handshakes and the connections CRUD."""

    router = APIRouter(tags=["mailboxes"])

    @router.get("/v1/mail-todo/oauth/gmail/connect")
    async def connect_gmail(request: Request) -> RedirectResponse:
        service = _connection_service(request)
        return RedirectResponse(service.begin(), status_code=302)

    @router.get("/v1/mail-todo/oauth/gmail/callback", response_model=None)
    async def gmail_callback(
        request: Request,
        state: str,
        code: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any] | Response:
        settings = _gmail_settings(request)
        if error:
            if settings.frontend_url:
                return _frontend_mail_redirect(settings.frontend_url, "denied")
            raise HTTPException(status_code=400, detail=f"Google OAuth was denied: {error}")
        if not code:
            if settings.frontend_url:
                return _frontend_mail_redirect(settings.frontend_url, "error")
            raise HTTPException(status_code=400, detail="Missing OAuth authorization code")
        authorization_response = f"{settings.redirect_uri}?{request.url.query}"
        try:
            connection = await _connection_service(request).complete(state, authorization_response)
        except ValueError as exc:
            if settings.frontend_url:
                return _frontend_mail_redirect(settings.frontend_url, "error")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if settings.frontend_url:
            response: Response = _frontend_mail_redirect(settings.frontend_url, "connected")
        else:
            response = JSONResponse(
                {
                    "status": "connected",
                    "connection": _public_connection(connection),
                    "next": "Create a digest run with this mailbox connection ID.",
                }
            )
        control_plane = runtime(request).control_plane
        identity_repository = (
            control_plane.identity_repository if control_plane is not None else None
        )
        session_repository = (
            control_plane.session_repository if control_plane is not None else None
        )
        if identity_repository is not None and session_repository is not None:
            principal = await identity_repository.resolve_or_create_principal(
                connection.email_address
            )
            token, _ = await session_repository.create(
                principal,
                now=datetime.now(UTC),
                ttl_seconds=session_settings(request).session_ttl_seconds,
            )
            set_session_cookie(response, session_settings(request), token)
        return response

    @router.get("/v1/mail-todo/oauth/outlook/connect")
    async def connect_outlook(
        request: Request,
        owner_connection_id: str = Query(alias="ownerConnectionId", min_length=1),
    ) -> RedirectResponse:
        service = _outlook_connection_service(request)
        if service is None:
            mailbox_group = runtime(request).mailbox
            availability = (
                mailbox_group.provider_availability if mailbox_group is not None else None
            )
            reason = (
                availability["outlook"]["reason"]
                if availability is not None
                else "the mailbox group is not composed"
            )
            raise HTTPException(
                status_code=503,
                detail=f"Outlook connection is unavailable: {reason}",
            )
        owner = await owned_connection(
            request, owner_connection_id, "Gmail owner connection not found"
        )
        if owner.provider != "gmail" or owner.status != "active":
            raise HTTPException(status_code=400, detail="An active Gmail owner is required")
        return RedirectResponse(service.begin(owner), status_code=302)

    @router.get("/v1/mail-todo/oauth/outlook/callback", response_model=None)
    async def outlook_callback(
        request: Request,
        state: str,
        code: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any] | Response:
        service = _outlook_connection_service(request)
        settings = _outlook_settings(request)
        if service is None or settings is None:
            raise HTTPException(status_code=503, detail="Outlook connection is unavailable")
        if error:
            if settings.frontend_url:
                return _frontend_mail_redirect(settings.frontend_url, "denied", provider="outlook")
            raise HTTPException(status_code=400, detail="Microsoft OAuth was denied")
        if not code:
            if settings.frontend_url:
                return _frontend_mail_redirect(settings.frontend_url, "error", provider="outlook")
            raise HTTPException(status_code=400, detail="Missing OAuth authorization code")
        try:
            connection = await service.complete(state, code)
        except (
            ValueError,
            MailboxNotConnectedError,
            MailboxReauthRequiredError,
            MailboxPermissionDeniedError,
        ) as exc:
            if settings.frontend_url:
                return _frontend_mail_redirect(settings.frontend_url, "error", provider="outlook")
            safe_message = getattr(exc, "safe_message", None)
            raise HTTPException(
                status_code=400,
                detail=safe_message if isinstance(safe_message, str) else str(exc),
            ) from exc
        except MailboxTemporaryError as exc:
            if settings.frontend_url:
                return _frontend_mail_redirect(settings.frontend_url, "error", provider="outlook")
            raise HTTPException(status_code=503, detail=exc.safe_message) from exc
        if settings.frontend_url:
            return _frontend_mail_redirect(settings.frontend_url, "connected", provider="outlook")
        return {
            "status": "connected",
            "connection": _public_connection(connection),
            "next": "Create a digest run with this mailbox connection ID.",
        }

    @router.get("/v1/mail-todo/connections")
    async def list_connections(request: Request) -> dict[str, Any]:
        control_plane = control_plane_required(request)
        repository = control_plane.connection_repository
        principal = await authenticated_principal(
            request, required=control_plane.session_repository is not None
        )
        connections = (
            await repository.list_for_user(principal.user_id)
            if principal is not None
            else await cast(Any, repository).list_all()
        )
        mailbox_group = runtime(request).mailbox
        if mailbox_group is None:
            raise RuntimeError("the mailbox group is not composed")
        return {
            "connections": [_public_connection(item) for item in connections],
            "providerAvailability": mailbox_group.provider_availability,
        }

    @router.delete("/v1/mail-todo/connections/{connection_id}")
    async def disconnect_mailbox(connection_id: str, request: Request) -> dict[str, bool]:
        connection = await owned_connection(request, connection_id, "Mailbox connection not found")
        principal = await connection_principal(request, connection)
        repository = control_plane_required(request).connection_repository
        deleted = await repository.delete(connection_id, principal.user_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Mailbox connection not found")
        return {"disconnected": True}

    @router.get("/v1/mail-todo/connections/{connection_id}/unread-preview")
    async def unread_preview(
        connection_id: str,
        request: Request,
        limit: int = Query(default=10, ge=1, le=20),
    ) -> dict[str, Any]:
        connection = await owned_connection(request, connection_id, "Mailbox connection not found")
        try:
            mailbox = _mailbox(request)
            page = await mailbox.search_unread(connection_id, DEFAULT_QUERY, limit)
            messages = []
            seen_threads: set[str] = set()
            for reference in page.messages:
                if reference.thread_id in seen_threads:
                    continue
                seen_threads.add(reference.thread_id)
                thread = await mailbox.get_thread(connection_id, reference.thread_id)
                if not thread:
                    continue
                message = thread[-1]
                messages.append(
                    {
                        "messageId": message.gmail_message_id,
                        "threadId": message.gmail_thread_id,
                        "subject": message.subject,
                        "sender": message.sender_email,
                        "receivedAt": message.received_at.isoformat(),
                        # ADR-003: presence only — attachment content is never processed.
                        "attachmentsPresent": message.attachments_present,
                        "deepLink": message.gmail_url,
                    }
                )
            return {
                "emailsMatched": page.estimated_total,
                "messages": messages,
                "nextCursor": page.next_cursor,
            }
        except MailboxNotConnectedError as exc:
            raise HTTPException(status_code=404, detail="Mailbox connection not found") from exc
        except MailboxReauthRequiredError as exc:
            raise HTTPException(
                status_code=409,
                detail=f"{connection.provider.title()} reauthorization required",
            ) from exc
        except MailboxPermissionDeniedError as exc:
            raise HTTPException(
                status_code=403,
                detail=f"{connection.provider.title()} mailbox permission denied",
            ) from exc
        except MailboxTemporaryError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"{connection.provider.title()} is temporarily unavailable",
            ) from exc

    return router


__all__ = ["create_mailbox_router"]
