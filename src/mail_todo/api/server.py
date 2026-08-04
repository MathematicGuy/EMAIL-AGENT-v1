"""Runnable FastAPI entry point for Gmail OAuth connection management."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import uvicorn
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from mail_todo.application.ports import ActionExtractorPort
from mail_todo.application.services import CreateDigestRun, DigestWorker, GetDigestResult
from mail_todo.infrastructure.config import GeminiSettings, GmailSettings, GroqSettings
from mail_todo.infrastructure.connections import SQLiteMailboxConnectionRepository
from mail_todo.infrastructure.gemini import GeminiActionExtractor
from mail_todo.infrastructure.gmail import (
    GmailConnectionService,
    GmailMailboxAdapter,
    MailboxNotConnectedError,
    MailboxReauthRequiredError,
    MailboxTemporaryError,
)
from mail_todo.infrastructure.groq import GroqActionExtractor
from mail_todo.infrastructure.memory import (
    InMemoryOutbox,
    InMemoryQueue,
    InMemoryResultRepository,
    InMemoryRunRepository,
    SafeTextAttachmentExtractor,
)
from mail_todo.infrastructure.security import OAuthStateManager, TokenCipher

from .handlers import _jsonable


class CreateRunRequest(BaseModel):
    mailbox_connection_id: str = Field(alias="mailboxConnectionId")
    query: str = "is:unread in:inbox"
    max_emails: int = Field(default=200, alias="maxEmails", ge=1, le=500)


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            settings = GmailSettings.from_env()
            repository = SQLiteMailboxConnectionRepository(settings.connection_db_path)
            await repository.initialize()
            app.state.gmail_settings = settings
            app.state.connection_repository = repository
            app.state.gmail_connections = GmailConnectionService(
                settings,
                repository,
                TokenCipher(settings.token_encryption_key),
                OAuthStateManager(
                    settings.oauth_state_secret,
                    settings.oauth_state_ttl_seconds,
                ),
            )
            app.state.gmail_mailbox = GmailMailboxAdapter(
                settings,
                repository,
                TokenCipher(settings.token_encryption_key),
            )
            run_repository = InMemoryRunRepository()
            result_repository = InMemoryResultRepository()
            app.state.run_repository = run_repository
            app.state.create_run = CreateDigestRun(run_repository, InMemoryQueue())
            app.state.get_result = GetDigestResult(run_repository, result_repository)
            app.state.result_repository = result_repository
            try:
                provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
                actions: ActionExtractorPort
                if provider == "gemini":
                    actions = GeminiActionExtractor(GeminiSettings.from_env())
                elif provider == "groq":
                    actions = GroqActionExtractor(GroqSettings.from_env())
                else:
                    raise ValueError("LLM_PROVIDER must be either 'gemini' or 'groq'")
                app.state.digest_worker = DigestWorker(
                    run_repository,
                    result_repository,
                    app.state.gmail_mailbox,
                    SafeTextAttachmentExtractor(),
                    actions,
                    InMemoryOutbox(),
                )
                app.state.gemini_configuration_error = None
            except ValueError as exc:
                app.state.digest_worker = None
                app.state.gemini_configuration_error = str(exc)
        except ValueError as exc:
            raise RuntimeError(f"Invalid Gmail configuration: {exc}") from exc
        yield

    app = FastAPI(title="Module Mail", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/mail-todo/oauth/gmail/connect")
    async def connect_gmail(
        request: Request,
        user_id: str = Query(min_length=1, description="Local user identifier"),
    ) -> RedirectResponse:
        service = _connection_service(request)
        return RedirectResponse(service.begin(user_id), status_code=302)

    @app.get("/v1/mail-todo/oauth/gmail/callback")
    async def gmail_callback(
        request: Request,
        state: str,
        code: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        if error:
            raise HTTPException(status_code=400, detail=f"Google OAuth was denied: {error}")
        if not code:
            raise HTTPException(status_code=400, detail="Missing OAuth authorization code")
        settings = _gmail_settings(request)
        authorization_response = f"{settings.redirect_uri}?{request.url.query}"
        try:
            connection = await _connection_service(request).complete(state, authorization_response)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "status": "connected",
            "connection": _public_connection(connection),
            "next": "Create a digest run with this mailbox connection ID.",
        }

    @app.get("/v1/mail-todo/connections")
    async def list_connections(
        request: Request, user_id: str = Query(min_length=1)
    ) -> dict[str, Any]:
        connections = await _connection_service(request).list_connections(user_id)
        return {"connections": [_public_connection(item) for item in connections]}

    @app.delete("/v1/mail-todo/connections/{connection_id}")
    async def disconnect_gmail(
        connection_id: str,
        request: Request,
        user_id: str = Query(min_length=1),
    ) -> dict[str, bool]:
        deleted = await _connection_service(request).disconnect(connection_id, user_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Gmail connection not found")
        return {"disconnected": True}

    @app.get("/v1/mail-todo/connections/{connection_id}/unread-preview")
    async def unread_preview(
        connection_id: str,
        request: Request,
        user_id: str = Query(min_length=1),
        limit: int = Query(default=10, ge=1, le=20),
    ) -> dict[str, Any]:
        repository: SQLiteMailboxConnectionRepository = request.app.state.connection_repository
        connection = await repository.get(connection_id)
        if connection is None or connection.user_id != user_id:
            raise HTTPException(status_code=404, detail="Gmail connection not found")
        try:
            page = await _gmail_mailbox(request).search_unread(
                connection_id, "is:unread in:inbox", limit
            )
            messages = []
            seen_threads: set[str] = set()
            for reference in page.messages:
                if reference.thread_id in seen_threads:
                    continue
                seen_threads.add(reference.thread_id)
                thread = await _gmail_mailbox(request).get_thread(
                    connection_id, reference.thread_id
                )
                if not thread:
                    continue
                message = thread[-1]
                messages.append(
                    {
                        "messageId": message.provider_message_id,
                        "threadId": message.provider_thread_id,
                        "subject": message.subject,
                        "sender": message.sender_address,
                        "receivedAt": message.received_at.isoformat(),
                        "attachmentCount": len(message.attachments),
                        "deepLink": message.deep_link,
                    }
                )
            return {
                "emailsMatched": page.estimated_total,
                "messages": messages,
                "nextCursor": page.next_cursor,
            }
        except MailboxNotConnectedError as exc:
            raise HTTPException(status_code=404, detail="Gmail connection not found") from exc
        except MailboxReauthRequiredError as exc:
            raise HTTPException(status_code=409, detail="Gmail reauthorization required") from exc
        except MailboxTemporaryError as exc:
            raise HTTPException(status_code=503, detail="Gmail is temporarily unavailable") from exc

    @app.post("/v1/mail-todo/runs", status_code=202)
    async def create_digest_run(
        payload: CreateRunRequest,
        request: Request,
        background_tasks: BackgroundTasks,
        user_id: str = Query(min_length=1),
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> dict[str, str]:
        connection_repository: SQLiteMailboxConnectionRepository = (
            request.app.state.connection_repository
        )
        connection = await connection_repository.get(payload.mailbox_connection_id)
        if connection is None or connection.user_id != user_id:
            raise HTTPException(status_code=404, detail="Gmail connection not found")
        worker = _digest_worker(request)
        if worker is None:
            raise HTTPException(
                status_code=503,
                detail=f"Gemini is not configured: {request.app.state.gemini_configuration_error}",
            )
        creator = cast(CreateDigestRun, request.app.state.create_run)
        run = await creator.execute(
            user_id=user_id,
            mailbox_connection_id=payload.mailbox_connection_id,
            idempotency_key=idempotency_key,
            query=payload.query,
            max_emails=payload.max_emails,
        )
        background_tasks.add_task(worker.execute, run.id)
        return {
            "id": run.id,
            "status": run.status.value,
            "statusUrl": f"/v1/mail-todo/runs/{run.id}?user_id={user_id}",
        }

    @app.get("/v1/mail-todo/runs/{run_id}")
    async def get_digest_run(
        run_id: str, request: Request, user_id: str = Query(min_length=1)
    ) -> dict[str, Any]:
        repository = cast(InMemoryRunRepository, request.app.state.run_repository)
        run = await repository.get(run_id)
        if run is None or run.user_id != user_id:
            raise HTTPException(status_code=404, detail="Digest run not found")
        response: dict[str, Any] = {
            "id": run.id,
            "status": run.status.value,
            "progress": {
                "emailsMatched": run.emails_matched,
                "emailsProcessed": run.emails_processed,
                "emailsToProcess": min(run.emails_matched, run.max_emails),
                "maxEmails": run.max_emails,
            },
            "error": (
                {"code": run.error_code, "message": run.error_message_safe}
                if run.error_code
                else None
            ),
        }
        if _is_development():
            results = cast(InMemoryResultRepository, request.app.state.result_repository)
            processed = await results.list_processed_emails(run_id)
            response["processedEmails"] = [
                {
                    "messageId": item.provider_message_id,
                    "threadId": item.provider_thread_id,
                    "subject": item.subject,
                    "sender": item.sender_address,
                    "receivedAt": item.received_at.isoformat(),
                }
                for item in processed
            ]
        return response

    @app.get("/v1/mail-todo/runs/{run_id}/result")
    async def get_digest_result(
        run_id: str, request: Request, user_id: str = Query(min_length=1)
    ) -> dict[str, Any]:
        repository = cast(InMemoryRunRepository, request.app.state.run_repository)
        run = await repository.get(run_id)
        if run is None or run.user_id != user_id:
            raise HTTPException(status_code=404, detail="Digest run not found")
        if run.status.value in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="RUN_NOT_COMPLETE")
        result_service = cast(GetDigestResult, request.app.state.get_result)
        payload = cast(dict[str, Any], _jsonable(await result_service.execute(run_id)))
        if not _is_development():
            payload.pop("processedEmails", None)
        return payload

    return app


def _connection_service(request: Request) -> GmailConnectionService:
    return cast(GmailConnectionService, request.app.state.gmail_connections)


def _gmail_settings(request: Request) -> GmailSettings:
    return cast(GmailSettings, request.app.state.gmail_settings)


def _gmail_mailbox(request: Request) -> GmailMailboxAdapter:
    return cast(GmailMailboxAdapter, request.app.state.gmail_mailbox)


def _digest_worker(request: Request) -> DigestWorker | None:
    return cast(DigestWorker | None, request.app.state.digest_worker)


def _is_development() -> bool:
    return os.getenv("APP_ENV", "development").lower() in {"development", "dev", "local"}


def _public_connection(connection: Any) -> dict[str, Any]:
    return {
        "id": connection.id,
        "provider": connection.provider,
        "emailAddress": connection.email_address,
        "scopes": list(connection.scopes),
        "status": connection.status,
        "createdAt": connection.created_at.isoformat(),
    }


def main() -> None:
    uvicorn.run(
        "mail_todo.api.server:create_app",
        factory=True,
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
