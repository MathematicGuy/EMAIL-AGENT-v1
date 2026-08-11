"""Runnable FastAPI entry point for Gmail OAuth connection management."""

import logging
import os
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import uvicorn
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from cowork_agent.config import (
    ChatMemorySettings,
    FaucetSettings,
    GeminiSettings,
    GmailSettings,
    GroqSettings,
    database_url,
    redis_url,
)
from cowork_agent.domain import DigestRun, MailboxConnection
from cowork_agent.domain.chat_contracts import ChatMemoryScope
from cowork_agent.domain.target_contracts import (
    RetrievalFilters,
    RetrievalLimits,
    SemanticRetrievalRequest,
)
from cowork_agent.features.ai_chat.controller import (
    ChatController,
    InMemoryChatSessionRegistry,
    UnavailableChatReply,
)
from cowork_agent.features.ai_chat.memory_gateway import MemoryGateway
from cowork_agent.features.ai_chat.ports import ChatReplyPort, DeclarativeMemoryPort
from cowork_agent.features.ai_chat.session_buffer import InMemoryChatSessionBuffer
from cowork_agent.features.email_action_plan.observability import (
    LoggingTraceSink,
    dev_trace_sink_from_env,
)
from cowork_agent.features.email_action_plan.policies import DEFAULT_QUERY
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
from cowork_agent.integrations.rag.bootstrap import (
    RAG_CORPUS_PATH,
    build_semantic_memory,
)
from cowork_agent.integrations.rag.chat_memory import SemanticChatMemoryAdapter
from cowork_agent.integrations.rag.knowledge_base import KnowledgeDocument, load_corpus
from cowork_agent.integrations.rag.null_memory import NullSemanticMemory
from cowork_agent.orchestration.local import InMemoryOutbox
from cowork_agent.persistence.repositories.local import InMemoryResultRepository
from cowork_agent.persistence.repositories.mailbox_connections import (
    SQLiteMailboxConnectionRepository,
)
from cowork_agent.persistence.repositories.runs import SQLiteRunRepository
from cowork_agent.persistence.repositories.tasks import SQLiteTaskRepository

from .api.chat import create_chat_router
from .api.handlers import _jsonable

logger = logging.getLogger(__name__)


class CreateRunRequest(BaseModel):
    mailbox_connection_id: str = Field(alias="mailboxConnectionId")
    query: str = DEFAULT_QUERY
    max_emails: int = Field(default=200, alias="maxEmails", ge=1, le=500)


class KnowledgeChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    top_k: int = Field(default=5, ge=1, le=20)


def _chat_controller_factory(
    app: FastAPI,
) -> Callable[[ChatMemoryScope], ChatController]:
    """Compose each scoped controller from runtime state at creation time."""

    def factory(scope: ChatMemoryScope) -> ChatController:
        semantic_memory = cast(
            SemanticMemoryPort,
            getattr(app.state, "semantic_memory", NullSemanticMemory()),
        )
        return ChatController(
            scope=scope,
            memory=MemoryGateway(
                scope=scope,
                session_buffer=app.state.chat_session_buffer,
                declarative_memory=cast(
                    DeclarativeMemoryPort | None,
                    app.state.chat_profile_repository,
                ),
                semantic_memory=SemanticChatMemoryAdapter(semantic_memory),
            ),
            reply=cast(ChatReplyPort, app.state.chat_reply),
        )

    return factory


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
                    PostgresChatProfileRepository,
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
                app.state.chat_profile_repository = PostgresChatProfileRepository(pool)
                app.state.pg_pool = pool
            else:
                task_repository = SQLiteTaskRepository(
                    settings.connection_db_path.parent / "tasks.db"
                )
                await task_repository.initialize()
                sqlite_run_repository = SQLiteRunRepository(
                    settings.connection_db_path.parent / "runs.db"
                )
                await sqlite_run_repository.initialize()
                run_repository = sqlite_run_repository
                app.state.outbox_repository = InMemoryOutbox()
                app.state.chat_profile_repository = None
                app.state.pg_pool = None
            chat_memory_settings = ChatMemorySettings.from_env()
            app.state.chat_sessions = InMemoryChatSessionRegistry()
            app.state.chat_session_buffer = InMemoryChatSessionBuffer(
                max_turns=chat_memory_settings.max_turns,
                ttl_seconds=chat_memory_settings.ttl_seconds,
            )
            app.state.chat_controllers = {}
            app.state.chat_reply = UnavailableChatReply()
            app.state.chat_principal_resolver = None

            app.state.chat_controller_factory = _chat_controller_factory(app)
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
                    semantic_memory = await build_semantic_memory(gemini_settings)
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
                app.state.semantic_memory = semantic_memory
                try:
                    app.state.knowledge_documents = load_corpus(
                        RAG_CORPUS_PATH, tenant_id=LOCAL_TENANT_ID
                    )
                except Exception:
                    app.state.knowledge_documents = ()
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
                app.state.semantic_memory = NullSemanticMemory()
                app.state.knowledge_documents = ()
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
    app.include_router(create_chat_router())

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/mail-todo/oauth/gmail/connect")
    async def connect_gmail(request: Request) -> RedirectResponse:
        service = _connection_service(request)
        return RedirectResponse(service.begin(), status_code=302)

    @app.get("/v1/mail-todo/oauth/gmail/callback", response_model=None)
    async def gmail_callback(
        request: Request,
        state: str,
        code: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any] | RedirectResponse:
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
            return _frontend_mail_redirect(settings.frontend_url, "connected")
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
                connection_id, DEFAULT_QUERY, limit
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

    @app.get("/v1/mail-todo/runs")
    async def list_digest_runs(
        request: Request,
        mailbox_connection_id: str = Query(alias="mailboxConnectionId", min_length=1),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, Any]:
        connection_repository: SQLiteMailboxConnectionRepository = (
            request.app.state.connection_repository
        )
        connection = await connection_repository.get(mailbox_connection_id)
        if connection is None:
            raise HTTPException(status_code=404, detail="Gmail connection not found")
        principal = principal_for_connection(connection)
        _require_owned_connection(principal, connection, detail="Gmail connection not found")
        repository = cast(RunRepository, request.app.state.run_repository)
        runs = await repository.list_recent(
            user_id=principal.user_id,
            mailbox_connection_id=mailbox_connection_id,
            limit=limit,
        )
        return {"runs": [_run_history_item(run) for run in runs]}

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

    # ── Knowledge endpoints (V1-M3): corpus inspection + ad-hoc retrieval ──

    @app.get("/v1/mail-todo/knowledge/ready")
    async def knowledge_ready(request: Request) -> dict[str, Any]:
        documents: tuple[KnowledgeDocument, ...] = cast(
            tuple[KnowledgeDocument, ...],
            getattr(request.app.state, "knowledge_documents", ()),
        )
        memory = cast(
            SemanticMemoryPort,
            getattr(request.app.state, "semantic_memory", NullSemanticMemory()),
        )
        chunk_count = sum(len(doc.chunks) for doc in documents)
        is_null = type(memory).__name__ == "NullSemanticMemory"
        if not documents:
            status = "unavailable"
        elif is_null:
            status = "degraded"
        else:
            status = "ready"
        return {
            "status": status,
            "document_count": len(documents),
            "chunk_count": chunk_count,
        }

    @app.get("/v1/mail-todo/knowledge/documents")
    async def knowledge_documents(request: Request) -> dict[str, Any]:
        documents: tuple[KnowledgeDocument, ...] = cast(
            tuple[KnowledgeDocument, ...],
            getattr(request.app.state, "knowledge_documents", ()),
        )
        items = [
            {
                "document_id": doc.document_id,
                "title": doc.title,
                "section_count": len(doc.chunks),
                "source_url": doc.source_url,
            }
            for doc in documents
        ]
        return {"documents": items}

    @app.post("/v1/mail-todo/knowledge/chat")
    async def knowledge_chat(
        body: KnowledgeChatRequest, request: Request
    ) -> dict[str, Any]:
        memory = cast(
            SemanticMemoryPort,
            getattr(request.app.state, "semantic_memory", NullSemanticMemory()),
        )
        retrieval_request = SemanticRetrievalRequest(
            run_id="knowledge-adhoc",
            tenant_id=LOCAL_TENANT_ID,
            user_id="demo-gui",
            query=body.query,
            knowledge_gaps=(),
            filters=RetrievalFilters(
                tenant_scope=LOCAL_TENANT_ID, document_status=("ready",)
            ),
            limits=RetrievalLimits(
                top_k=body.top_k, min_score=-1.0, timeout_ms=8_000
            ),
        )
        response = await memory.retrieve(retrieval_request)
        return response.to_dict()

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


def _run_history_item(run: DigestRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "mailboxConnectionId": run.mailbox_connection_id,
        "status": run.status.value,
        "createdAt": run.created_at.isoformat() if run.created_at else None,
        "completedAt": run.completed_at.isoformat() if run.completed_at else None,
        "progress": {
            "emailsMatched": run.emails_matched,
            "emailsProcessed": run.emails_processed,
            "emailsToProcess": min(run.emails_matched, run.max_emails),
            "maxEmails": run.max_emails,
            "actionItemsCount": run.action_items_count,
        },
        "error": (
            {"code": run.error_code, "message": run.error_message_safe}
            if run.error_code
            else None
        ),
    }


def _frontend_mail_redirect(frontend_url: str, outcome: str) -> RedirectResponse:
    parts = urlsplit(frontend_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({"page": "dashboard", "view": "mail", "gmail": outcome})
    location = urlunsplit(
        (parts.scheme, parts.netloc, parts.path or "/", urlencode(query), "dashboard")
    )
    return RedirectResponse(location, status_code=302)


def main() -> None:
    # Without a root handler the stdlib drops every INFO record, which silently
    # discards the whole trace-sink stream (§13) — the observability surface is
    # log lines, so an unconfigured logger means no observability at all.
    # .upper() because basicConfig rejects "debug" with a ValueError, which would
    # kill the process at startup over a lowercase env var.
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    loop = "auto"
    if (database_url() or redis_url()) and sys.platform == "win32":
        # psycopg async cannot run on Windows' ProactorEventLoop. uvicorn builds
        # the loop from its own loop_factory, so setting the event loop *policy*
        # here has no effect — the loop has to be named in the config instead.
        loop = "asyncio:SelectorEventLoop"
    uvicorn.run(
        "cowork_agent.app:create_app",
        factory=True,
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "8000")),
        loop=loop,
    )


if __name__ == "__main__":
    main()
