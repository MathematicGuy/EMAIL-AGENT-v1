"""Runnable FastAPI entry point for Gmail OAuth connection management."""

import asyncio
import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import uvicorn
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from cowork_agent.config import (
    FaucetSettings,
    GeminiSettings,
    GmailSettings,
    GroqSettings,
    database_url,
    redis_url,
)
from cowork_agent.domain import DigestRun, MailboxConnection
from cowork_agent.features.email_action_plan.observability import (
    LoggingTraceSink,
    dev_trace_sink_from_env,
)
from cowork_agent.features.email_action_plan.ports import (
    ActionPlanGeneratorPort,
    MailboxTemporaryError,
    RouteClassifierPort,
    RunRepository,
    SemanticMemoryPort,
    TaskRepository,
)
from cowork_agent.features.email_action_plan.short_term import ShortTermStore
from cowork_agent.features.email_action_plan.workflow import (
    CreateDigestRun,
    DigestWorker,
    GetDigestResult,
)
from cowork_agent.identity import (
    LOCAL_TENANT_ID,
    ConnectionNotOwnedError,
    VerifiedPrincipal,
    ensure_principal_owns_connection,
    principal_for_connection,
)
from cowork_agent.integrations.gmail.auth import OAuthStateManager, TokenCipher
from cowork_agent.integrations.gmail.fakes import SafeTextAttachmentExtractor
from cowork_agent.integrations.gmail.provider import (
    GmailConnectionService,
    GmailMailboxAdapter,
    MailboxNotConnectedError,
    MailboxReauthRequiredError,
)
from cowork_agent.integrations.llm.providers.faucet import (
    FaucetActionPlanGenerator,
    FaucetRouteClassifier,
)
from cowork_agent.integrations.llm.providers.gemini import (
    GeminiActionPlanGenerator,
    GeminiRouteClassifier,
)
from cowork_agent.integrations.llm.providers.groq import (
    GroqActionPlanGenerator,
    GroqRouteClassifier,
)
from cowork_agent.integrations.rag.embeddings import GeminiEmbeddingAdapter
from cowork_agent.integrations.rag.hybrid import HybridSemanticMemory
from cowork_agent.integrations.rag.jina_reranker import JinaRerankerAdapter
from cowork_agent.integrations.rag.knowledge_base import load_corpus
from cowork_agent.integrations.rag.null_memory import NullSemanticMemory
from cowork_agent.orchestration.local import InMemoryOutbox
from cowork_agent.persistence.repositories.local import (
    InMemoryResultRepository,
    InMemoryRunRepository,
)
from cowork_agent.persistence.repositories.mailbox_connections import (
    SQLiteMailboxConnectionRepository,
)
from cowork_agent.persistence.repositories.tasks import SQLiteTaskRepository

from .api.handlers import _jsonable

logger = logging.getLogger(__name__)

#: Committed in-repo knowledge corpus (V1-M3), resolved from the package root.
_RAG_CORPUS_PATH = Path(__file__).resolve().parents[2] / "data" / "extracted"


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
            run_repository: RunRepository
            task_repository: TaskRepository
            result_repository = InMemoryResultRepository()
            if database_url():
                # V1-H T5.1 durable control plane: PostgreSQL is the source
                # of truth for runs, tasks, and outbox events. Imports stay
                # lazy so the app boots without the optional postgres extra.
                from psycopg_pool import AsyncConnectionPool

                from cowork_agent.persistence.migrate import apply_migrations
                from cowork_agent.persistence.repositories.postgres import (
                    PostgresOutboxRepository,
                    PostgresRunRepository,
                    PostgresTaskRepository,
                )

                pool = AsyncConnectionPool(
                    database_url(), min_size=1, max_size=8, open=False
                )
                await pool.open(wait=True)
                await apply_migrations(pool)
                run_repository = PostgresRunRepository(pool)
                task_repository = PostgresTaskRepository(pool)
                app.state.outbox_repository = PostgresOutboxRepository(pool)
                app.state.pg_pool = pool
            else:
                task_repository = SQLiteTaskRepository(
                    settings.connection_db_path.parent / "tasks.db"
                )
                await task_repository.initialize()
                run_repository = InMemoryRunRepository()
                app.state.outbox_repository = InMemoryOutbox()
                app.state.pg_pool = None
            app.state.run_repository = run_repository
            app.state.create_run = CreateDigestRun(run_repository)
            app.state.get_result = GetDigestResult(
                run_repository, result_repository, task_repository
            )
            app.state.result_repository = result_repository
            app.state.task_repository = task_repository
            queue_url = redis_url()
            if queue_url:
                # V1-H T5.2: durable queue dispatch replaces BackgroundTasks.
                # The durable claim lives in PostgreSQL, so the queue
                # requires the PG repositories.
                if app.state.pg_pool is None:
                    raise RuntimeError("REDIS_URL requires DATABASE_URL")
                from redis.asyncio import Redis as AsyncRedis

                from cowork_agent.orchestration.redis_queue import RedisRunQueue

                redis_client = AsyncRedis.from_url(queue_url, decode_responses=True)
                app.state.redis_client = redis_client
                app.state.run_queue = RedisRunQueue(redis_client)
            else:
                app.state.redis_client = None
                app.state.run_queue = None
            try:
                provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
                provider_label = {
                    "gemini": "Gemini",
                    "groq": "Groq",
                    "faucet": "Faucet",
                }.get(provider, "LLM provider")
                classifier: RouteClassifierPort
                generator: ActionPlanGeneratorPort
                if provider == "gemini":
                    gemini_settings = GeminiSettings.from_env()
                    classifier = GeminiRouteClassifier(gemini_settings)
                    generator = GeminiActionPlanGenerator(gemini_settings)
                    semantic_memory = await _build_semantic_memory(gemini_settings)
                elif provider == "groq":
                    groq_settings = GroqSettings.from_env()
                    classifier = GroqRouteClassifier(groq_settings)
                    generator = GroqActionPlanGenerator(groq_settings)
                    semantic_memory = NullSemanticMemory()
                elif provider == "faucet":
                    faucet_settings = FaucetSettings.from_env()
                    classifier = FaucetRouteClassifier(faucet_settings)
                    generator = FaucetActionPlanGenerator(faucet_settings)
                    semantic_memory = NullSemanticMemory()
                else:
                    raise ValueError("LLM_PROVIDER must be 'gemini', 'groq', or 'faucet'")
                app.state.digest_worker = DigestWorker(
                    run_repository,
                    result_repository,
                    app.state.gmail_mailbox,
                    SafeTextAttachmentExtractor(),
                    classifier,
                    generator,
                    ShortTermStore(),
                    task_repository,
                    semantic_memory=semantic_memory,
                    trace_sink=LoggingTraceSink(),
                    dev_trace=dev_trace_sink_from_env(
                        settings.connection_db_path.parent, settings.token_encryption_key
                    ),
                    completion_outbox=app.state.outbox_repository,
                )
                app.state.llm_configuration_error = None
                app.state.llm_provider_label = provider_label
            except ValueError as exc:
                app.state.digest_worker = None
                app.state.llm_configuration_error = str(exc)
                app.state.llm_provider_label = provider_label
        except ValueError as exc:
            raise RuntimeError(f"Invalid Gmail configuration: {exc}") from exc
        yield
        pg_pool = getattr(app.state, "pg_pool", None)
        if pg_pool is not None:
            await pg_pool.close()
        redis_client = getattr(app.state, "redis_client", None)
        if redis_client is not None:
            await redis_client.aclose()

    app = FastAPI(title="Module Mail", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/mail-todo/oauth/gmail/connect")
    async def connect_gmail(request: Request) -> RedirectResponse:
        service = _connection_service(request)
        return RedirectResponse(service.begin(), status_code=302)

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
    async def list_connections(request: Request) -> dict[str, Any]:
        # Local single-user MVP: no identity parameter, so every Mailbox Connection is listed.
        repository: SQLiteMailboxConnectionRepository = request.app.state.connection_repository
        connections = await repository.list_all()
        return {"connections": [_public_connection(item) for item in connections]}

    @app.delete("/v1/mail-todo/connections/{connection_id}")
    async def disconnect_gmail(connection_id: str, request: Request) -> dict[str, bool]:
        repository: SQLiteMailboxConnectionRepository = request.app.state.connection_repository
        connection = await repository.get(connection_id)
        if connection is None:
            raise HTTPException(status_code=404, detail="Gmail connection not found")
        principal = principal_for_connection(connection)
        _require_owned_connection(principal, connection, detail="Gmail connection not found")
        deleted = await _connection_service(request).disconnect(connection_id, principal.user_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Gmail connection not found")
        return {"disconnected": True}

    @app.get("/v1/mail-todo/connections/{connection_id}/unread-preview")
    async def unread_preview(
        connection_id: str,
        request: Request,
        limit: int = Query(default=10, ge=1, le=20),
    ) -> dict[str, Any]:
        repository: SQLiteMailboxConnectionRepository = request.app.state.connection_repository
        connection = await repository.get(connection_id)
        if connection is None:
            raise HTTPException(status_code=404, detail="Gmail connection not found")
        principal = principal_for_connection(connection)
        _require_owned_connection(principal, connection, detail="Gmail connection not found")
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
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> dict[str, str]:
        connection_repository: SQLiteMailboxConnectionRepository = (
            request.app.state.connection_repository
        )
        connection = await connection_repository.get(payload.mailbox_connection_id)
        if connection is None:
            raise HTTPException(status_code=404, detail="Gmail connection not found")
        principal = principal_for_connection(connection)
        _require_owned_connection(principal, connection, detail="Gmail connection not found")
        worker = _digest_worker(request)
        if worker is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"{request.app.state.llm_provider_label} is not configured: "
                    f"{request.app.state.llm_configuration_error}"
                ),
            )
        creator = cast(CreateDigestRun, request.app.state.create_run)
        run = await creator.execute(
            user_id=principal.user_id,
            mailbox_connection_id=payload.mailbox_connection_id,
            idempotency_key=idempotency_key,
            query=payload.query,
            max_emails=payload.max_emails,
        )
        run_queue = getattr(request.app.state, "run_queue", None)
        if run_queue is not None:
            await run_queue.enqueue_digest_run(
                run.id, user_id=principal.user_id, tenant_id=principal.tenant_id
            )
        else:
            background_tasks.add_task(worker.execute, run.id)
        return {
            "id": run.id,
            "status": run.status.value,
            "statusUrl": f"/v1/mail-todo/runs/{run.id}",
        }

    @app.get("/v1/mail-todo/runs/{run_id}")
    async def get_digest_run(run_id: str, request: Request) -> dict[str, Any]:
        repository = cast(RunRepository, request.app.state.run_repository)
        run = await repository.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Digest run not found")
        await _ensure_run_connection_owned(request, run, detail="Digest run not found")
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
    async def get_digest_result(run_id: str, request: Request) -> dict[str, Any]:
        repository = cast(RunRepository, request.app.state.run_repository)
        run = await repository.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Digest run not found")
        await _ensure_run_connection_owned(request, run, detail="Digest run not found")
        if run.status.value in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="RUN_NOT_COMPLETE")
        result_service = cast(GetDigestResult, request.app.state.get_result)
        payload = cast(dict[str, Any], _jsonable(await result_service.execute(run_id)))
        if not _is_development():
            payload.pop("processedEmails", None)
        return payload

    @app.get("/v1/mail-todo/runs/{run_id}/tasks")
    async def get_digest_tasks(run_id: str, request: Request) -> dict[str, Any]:
        """Persisted §6.6 Tasks for presentation (T4.3): citations, missing
        information, and confidences that the legacy result shape drops."""
        repository = cast(RunRepository, request.app.state.run_repository)
        run = await repository.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Digest run not found")
        await _ensure_run_connection_owned(request, run, detail="Digest run not found")
        if run.status.value in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="RUN_NOT_COMPLETE")
        task_repository = cast(TaskRepository, request.app.state.task_repository)
        records = await task_repository.list_for_run(run_id)
        return {"tasks": [record.task.to_dict() for record in records]}

    return app


def _connection_service(request: Request) -> GmailConnectionService:
    return cast(GmailConnectionService, request.app.state.gmail_connections)


def _require_owned_connection(
    principal: VerifiedPrincipal, connection: MailboxConnection, *, detail: str
) -> None:
    """Translate the centralized ownership guard into the HTTP 404 contract."""
    try:
        ensure_principal_owns_connection(principal, connection)
    except ConnectionNotOwnedError as exc:
        raise HTTPException(status_code=404, detail=detail) from exc


async def _ensure_run_connection_owned(request: Request, run: DigestRun, *, detail: str) -> None:
    """Verify Run → Mailbox Connection ownership integrity; 404 on any mismatch."""
    connection_repository: SQLiteMailboxConnectionRepository = (
        request.app.state.connection_repository
    )
    connection = await connection_repository.get(run.mailbox_connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail=detail)
    principal = VerifiedPrincipal(tenant_id=LOCAL_TENANT_ID, user_id=run.user_id)
    _require_owned_connection(principal, connection, detail=detail)


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


async def _build_semantic_memory(settings: GeminiSettings) -> SemanticMemoryPort:
    """Best-effort in-repo RAG store; null memory on any setup failure.

    RETRIEVE_RAG candidates degrade to structured empty retrieval (§12.3)
    when the corpus or the embedding API is unavailable, so a missing index
    never blocks digest runs.
    """
    try:
        documents = load_corpus(_RAG_CORPUS_PATH, tenant_id=LOCAL_TENANT_ID)
        memory = HybridSemanticMemory(
            documents,
            GeminiEmbeddingAdapter(settings),
            reranker=JinaRerankerAdapter(api_key=os.getenv("JINA_API_KEY")),
        )
        await memory.build_index()
        return memory
    except Exception as exc:
        logger.warning(
            "Semantic memory unavailable (%s); retrieval returns structured empty results",
            type(exc).__name__,
        )
        return NullSemanticMemory()


def main() -> None:
    if (database_url() or redis_url()) and sys.platform == "win32":
        # psycopg async cannot run on Windows' ProactorEventLoop.
        from asyncio import windows_events

        asyncio.set_event_loop_policy(windows_events.WindowsSelectorEventLoopPolicy())
    uvicorn.run(
        "cowork_agent.app:create_app",
        factory=True,
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
