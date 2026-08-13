"""Runnable FastAPI entry point for Gmail OAuth connection management."""

import logging
import os
import shutil
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from cowork_agent.config import (
    ChatIntentSettings,
    ChatMemorySettings,
    FaucetSettings,
    GeminiSettings,
    GmailSettings,
    GroqSettings,
    JinaEmbeddingSettings,
    KnowledgeIngestionSettings,
    QdrantSettings,
    SessionSettings,
    SupabaseStorageSettings,
    UserDocumentsSettings,
    database_url,
)
from cowork_agent.domain import DigestRun, MailboxConnection
from cowork_agent.domain.chat_contracts import ChatMemoryScope
from cowork_agent.domain.project_documents import ProjectDocument
from cowork_agent.domain.target_contracts import (
    RetrievalFilters,
    RetrievalLimits,
    SemanticRetrievalRequest,
)
from cowork_agent.features.ai_chat.controller import (
    ChatController,
    ChatSessionRegistryPort,
    InMemoryChatSessionRegistry,
    UnavailableChatReply,
)
from cowork_agent.features.ai_chat.intent.observability import LoggingIntentRoutingSink
from cowork_agent.features.ai_chat.intent.service import (
    ChatRoutingService,
    RepositoryReadyDocumentCatalog,
)
from cowork_agent.features.ai_chat.memory_gateway import MemoryGateway
from cowork_agent.features.ai_chat.memory_observability import (
    LoggingMemoryOperationSink,
    MemoryOperationMetrics,
)
from cowork_agent.features.ai_chat.ports import (
    ChatReplyPort,
    DeclarativeMemoryPort,
    EpisodicMemoryPort,
    IntentClassifierPort,
)
from cowork_agent.features.ai_chat.session_buffer import (
    InMemoryChatSessionBuffer,
)
from cowork_agent.features.email_action_plan.observability import (
    LoggingTraceSink,
    dev_trace_sink_from_env,
)
from cowork_agent.features.email_action_plan.policies import DEFAULT_QUERY
from cowork_agent.features.email_action_plan.ports import (
    ActionPlanGeneratorPort,
    MailboxConnectionRepository,
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
    principal_from_opaque_session,
)
from cowork_agent.integrations.gmail.auth import OAuthStateManager, TokenCipher
from cowork_agent.integrations.gmail.fakes import SafeTextAttachmentExtractor
from cowork_agent.integrations.gmail.provider import (
    GmailConnectionService,
    GmailMailboxAdapter,
    MailboxNotConnectedError,
    MailboxReauthRequiredError,
)
from cowork_agent.integrations.llm.chat_intent import (
    FaucetIntentClassifier,
    GeminiIntentClassifier,
    GroqIntentClassifier,
)
from cowork_agent.integrations.llm.chat_reply import (
    FaucetChatReply,
    GeminiChatReply,
    GroqChatReply,
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
from cowork_agent.integrations.project_documents.encrypted_store import EncryptedDocumentStore
from cowork_agent.integrations.project_documents.ingestion import ProjectDocumentIngestionService
from cowork_agent.integrations.project_documents.mistral_ocr import MistralOcrClient
from cowork_agent.integrations.project_documents.qdrant_store import QdrantProjectDocumentStore
from cowork_agent.integrations.rag.bootstrap import (
    RAG_CORPUS_PATH,
    build_semantic_memory,
)
from cowork_agent.integrations.rag.chat_memory import SemanticChatMemoryAdapter
from cowork_agent.integrations.rag.embeddings import GeminiEmbeddingAdapter
from cowork_agent.integrations.rag.knowledge_base import KnowledgeDocument, load_corpus
from cowork_agent.integrations.rag.null_memory import NullSemanticMemory
from cowork_agent.integrations.storage.supabase import SupabasePrivateStorage
from cowork_agent.orchestration.document_ingestion import DocumentIngestionDispatcher
from cowork_agent.orchestration.document_retention import DocumentRetentionManager
from cowork_agent.orchestration.local import InMemoryOutbox
from cowork_agent.persistence.repositories.local import InMemoryResultRepository
from cowork_agent.persistence.repositories.mailbox_connections import (
    SQLiteMailboxConnectionRepository,
)
from cowork_agent.persistence.repositories.project_documents import (
    InMemoryProjectDocumentRepository,
    InMemoryProjectRepository,
)
from cowork_agent.persistence.repositories.runs import SQLiteRunRepository
from cowork_agent.persistence.repositories.tasks import SQLiteTaskRepository

from .api.chat import create_chat_router
from .api.handlers import _jsonable
from .api.projects import create_project_router

logger = logging.getLogger(__name__)


async def _resolve_chat_principal(request: Request) -> VerifiedPrincipal:
    """Resolve chat identity from a session, with the local MVP fallback."""

    if getattr(request.app.state, "session_repository", None) is not None:
        principal = await _authenticated_principal(request)
        assert principal is not None
        return principal

    repository: SQLiteMailboxConnectionRepository = request.app.state.connection_repository
    candidates = tuple(
        connection for connection in await repository.list_all() if connection.status == "active"
    )
    if len(candidates) != 1:
        raise HTTPException(status_code=503, detail="Chat identity is unavailable")
    return principal_for_connection(candidates[0])


class CreateRunRequest(BaseModel):
    mailbox_connection_id: str = Field(alias="mailboxConnectionId")
    query: str = DEFAULT_QUERY
    max_emails: int = Field(default=200, alias="maxEmails", ge=1, le=500)


class KnowledgeChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    top_k: int = Field(default=5, ge=1, le=20)


class SaveReportRequest(BaseModel):
    filename: str
    content: str


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
                episodic_memory=cast(
                    EpisodicMemoryPort | None,
                    app.state.chat_task_episode_repository,
                ),
                semantic_memory=SemanticChatMemoryAdapter(semantic_memory),
                project_documents=getattr(app.state, "project_document_vectors", None),
                memory_operation_sink=getattr(app.state, "memory_operation_sink", None),
            ),
            reply=cast(ChatReplyPort, app.state.chat_reply),
            routing=cast(
                ChatRoutingService | None,
                getattr(app.state, "chat_routing_service", None),
            ),
            company_rag_enabled=getattr(
                getattr(app.state, "chat_intent_settings", None),
                "company_rag_enabled",
                True,
            ),
            episode_retention_seconds=getattr(
                getattr(app.state, "chat_memory_settings", None),
                "episode_retention_seconds",
                None,
            ),
        )

    return factory


def create_chat_session_buffer(
    settings: ChatMemorySettings, *, durable: bool
) -> InMemoryChatSessionBuffer:
    """Keep bounded working turns local; durability belongs to Postgres metadata."""
    del durable
    return InMemoryChatSessionBuffer(
        max_turns=settings.max_turns,
        ttl_seconds=settings.ttl_seconds,
    )

def create_app() -> FastAPI:
    log_level = (os.getenv("LOG_LEVEL") or os.getenv("APP_LOG_LEVEL") or "INFO").upper()
    log_file = os.getenv("LOG_FILE", ".data/app.log")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    logging.getLogger("cowork_agent").setLevel(logging.INFO)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            try:
                import importlib.util

                script_path = Path(__file__).resolve().parents[2] / "scripts" / "update_corpus2skill.py"
                if script_path.exists():
                    spec = importlib.util.spec_from_file_location("update_corpus2skill", script_path)
                    if spec and spec.loader:
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        if hasattr(mod, "main"):
                            mod.main()
            except Exception as exc:
                logger.warning("Corpus skill tree auto-update skipped: %s", exc)

            settings = GmailSettings.from_env()
            session_settings = SessionSettings.from_env()
            repository: MailboxConnectionRepository = SQLiteMailboxConnectionRepository(
                settings.connection_db_path
            )
            local_repository = cast(SQLiteMailboxConnectionRepository, repository)
            await local_repository.initialize()
            app.state.gmail_settings = settings
            app.state.session_settings = session_settings
            app.state.identity_repository = None
            app.state.session_repository = None
            run_repository: RunRepository
            task_repository: TaskRepository
            chat_session_registry: ChatSessionRegistryPort
            result_repository = InMemoryResultRepository()
            if database_url():
                # V1-H T5.1 durable control plane: PostgreSQL is the source
                # of truth for runs, tasks, and outbox events. Imports stay
                # lazy so the app boots without the optional postgres extra.
                from psycopg_pool import AsyncConnectionPool

                from cowork_agent.persistence.migrate import apply_migrations
                from cowork_agent.persistence.repositories.identity import (
                    PostgresIdentityRepository,
                    PostgresMailboxConnectionRepository,
                    PostgresSessionRepository,
                )
                from cowork_agent.persistence.repositories.postgres import (
                    PostgresChatProfileRepository,
                    PostgresOutboxRepository,
                    PostgresRunRepository,
                    PostgresTaskEpisodeRepository,
                    PostgresTaskRepository,
                )
                from cowork_agent.persistence.repositories.project_documents import (
                    PostgresChatSessionRepository,
                )
                from cowork_agent.persistence.repositories.project_documents import (
                    PostgresProjectDocumentRepository as LegacyProjectDocumentRepository,
                )
                from cowork_agent.persistence.repositories.project_documents import (
                    PostgresProjectRepository as LegacyProjectRepository,
                )
                from cowork_agent.persistence.repositories.projects import (
                    PostgresProjectRepository,
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
                app.state.chat_task_episode_repository = PostgresTaskEpisodeRepository(pool)
                app.state.chat_project_repository = LegacyProjectRepository(pool)
                app.state.project_document_repository = LegacyProjectDocumentRepository(pool)
                chat_session_registry = PostgresChatSessionRepository(pool)
                app.state.chat_session_repository = chat_session_registry
                app.state.pg_pool = pool
                app.state.identity_repository = PostgresIdentityRepository(pool)
                app.state.session_repository = PostgresSessionRepository(pool)
                app.state.project_repository = PostgresProjectRepository(pool)
                repository = PostgresMailboxConnectionRepository(pool)
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
                app.state.chat_task_episode_repository = None
                app.state.chat_project_repository = InMemoryProjectRepository()
                app.state.project_document_repository = InMemoryProjectDocumentRepository()
                app.state.chat_session_repository = None
                app.state.pg_pool = None
                app.state.project_repository = None
                chat_session_registry = InMemoryChatSessionRegistry()
            app.state.connection_repository = repository
            app.state.gmail_connections = GmailConnectionService(
                settings,
                repository,
                TokenCipher(settings.token_encryption_key),
                OAuthStateManager(
                    settings.oauth_state_secret,
                    settings.oauth_state_ttl_seconds,
                ),
                principal_resolver=(
                    app.state.identity_repository.resolve_or_create_principal
                    if app.state.identity_repository is not None
                    else None
                ),
            )
            app.state.gmail_mailbox = GmailMailboxAdapter(
                settings,
                repository,
                TokenCipher(settings.token_encryption_key),
            )
            if os.getenv("SUPABASE_URL", "").strip():
                storage_settings = SupabaseStorageSettings.from_env()
                storage_client = httpx.AsyncClient(timeout=30.0)
                app.state.private_storage_client = storage_client
                app.state.private_storage = SupabasePrivateStorage(
                    storage_settings.url,
                    storage_settings.secret_key,
                    storage_settings.bucket,
                    storage_client,
                )
            else:
                app.state.private_storage_client = None
                app.state.private_storage = None
            chat_memory_settings = ChatMemorySettings.from_env()
            app.state.chat_memory_settings = chat_memory_settings
            app.state.chat_sessions = chat_session_registry
            app.state.chat_session_buffer = create_chat_session_buffer(
                chat_memory_settings, durable=app.state.pg_pool is not None
            )
            app.state.memory_metrics = MemoryOperationMetrics()
            app.state.memory_operation_sink = LoggingMemoryOperationSink(
                metrics=app.state.memory_metrics
            )
            app.state.chat_reply = UnavailableChatReply()
            app.state.chat_routing_service = None
            user_documents_settings = UserDocumentsSettings.from_env()
            if user_documents_settings.enabled and app.state.pg_pool is None:
                raise ValueError("DATABASE_URL is required when user documents are enabled")
            app.state.user_documents_settings = user_documents_settings
            app.state.ready_document_catalog = RepositoryReadyDocumentCatalog(
                app.state.project_document_repository
            )
            app.state.project_document_store = None
            app.state.project_document_vectors = None
            app.state.document_ingestion_dispatcher = None
            app.state.project_document_qdrant_client = None
            app.state.document_retention_manager = None
            app.state.chat_principal_resolver = _resolve_chat_principal

            app.state.chat_controller_factory = _chat_controller_factory(app)
            app.state.run_repository = run_repository
            app.state.create_run = CreateDigestRun(run_repository)
            app.state.get_result = GetDigestResult(
                run_repository, result_repository, task_repository
            )
            app.state.result_repository = result_repository
            app.state.task_repository = task_repository
            app.state.redis_client = None
            app.state.run_queue = None

            if user_documents_settings.enabled:
                qdrant_settings = QdrantSettings.from_env()
                if not qdrant_settings.enabled:
                    raise ValueError(
                        "QDRANT_URL and QDRANT_ENABLED=true are required for user documents"
                    )
                from qdrant_client import AsyncQdrantClient

                document_gemini_settings = GeminiSettings.from_env()
                ingestion_settings = KnowledgeIngestionSettings.from_env()
                document_store = EncryptedDocumentStore(
                    user_documents_settings.store_path,
                    user_documents_settings.encryption_key,
                )
                document_qdrant = AsyncQdrantClient(
                    url=qdrant_settings.url,
                    api_key=qdrant_settings.api_key or None,
                )
                document_vectors = QdrantProjectDocumentStore(
                    client=document_qdrant,
                    collection_name=user_documents_settings.collection_name,
                    embedder=GeminiEmbeddingAdapter(
                        document_gemini_settings,
                        model=user_documents_settings.embedding_model,
                    ),
                    projects=app.state.chat_project_repository,
                    documents=app.state.project_document_repository,
                )
                ingestion = ProjectDocumentIngestionService(
                    documents=app.state.project_document_repository,
                    store=document_store,
                    vectors=document_vectors,
                    ocr=MistralOcrClient(
                        api_key=ingestion_settings.api_key,
                        model=ingestion_settings.model,
                        timeout_seconds=ingestion_settings.timeout_seconds,
                        max_attempts=ingestion_settings.max_attempts,
                    ),
                    max_pages=user_documents_settings.max_pages,
                )

                async def process_document(document: ProjectDocument) -> object:
                    return await ingestion.process(document, worker_id=f"api-{os.getpid()}")

                dispatcher = DocumentIngestionDispatcher(
                    documents=app.state.project_document_repository,
                    process=process_document,
                    stream_name=user_documents_settings.ingestion_stream,
                    redis=None,
                )
                app.state.project_document_store = document_store
                app.state.project_document_vectors = document_vectors
                app.state.document_ingestion_dispatcher = dispatcher
                app.state.project_document_qdrant_client = document_qdrant
                retention = DocumentRetentionManager(
                    documents=app.state.project_document_repository,
                    store=document_store,
                    vectors=document_vectors,
                )
                app.state.document_retention_manager = retention
                await dispatcher.recover()
                await retention.run_once()
                retention.start()
            app.state.project_document_queue = None
            try:
                provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
                provider_label = {
                    "gemini": "Gemini",
                    "groq": "Groq",
                    "faucet": "Faucet",
                }.get(provider, "LLM provider")
                classifier: RouteClassifierPort
                generator: ActionPlanGeneratorPort
                intent_classifier: IntentClassifierPort
                if provider == "gemini":
                    gemini_settings = GeminiSettings.from_env()
                    intent_settings = ChatIntentSettings.from_env(
                        default_model=gemini_settings.model
                    )
                    intent_classifier = GeminiIntentClassifier.from_settings(
                        gemini_settings, intent_settings
                    )
                    classifier = GeminiRouteClassifier(gemini_settings)
                    generator = GeminiActionPlanGenerator(gemini_settings)
                    semantic_memory = await build_semantic_memory(
                        JinaEmbeddingSettings.from_env()
                    )
                    app.state.chat_reply = GeminiChatReply.from_settings(gemini_settings)
                elif provider == "groq":
                    groq_settings = GroqSettings.from_env()
                    intent_settings = ChatIntentSettings.from_env(default_model=groq_settings.model)
                    intent_classifier = GroqIntentClassifier.from_settings(
                        groq_settings, intent_settings
                    )
                    classifier = GroqRouteClassifier(groq_settings)
                    generator = GroqActionPlanGenerator(groq_settings)
                    semantic_memory = NullSemanticMemory()
                    app.state.chat_reply = GroqChatReply.from_settings(groq_settings)
                elif provider == "faucet":
                    faucet_settings = FaucetSettings.from_env()
                    intent_settings = ChatIntentSettings.from_env(
                        default_model=faucet_settings.model
                    )
                    intent_classifier = FaucetIntentClassifier.from_settings(
                        faucet_settings, intent_settings
                    )
                    classifier = FaucetRouteClassifier(faucet_settings)
                    generator = FaucetActionPlanGenerator(faucet_settings)
                    semantic_memory = NullSemanticMemory()
                    app.state.chat_reply = FaucetChatReply.from_settings(faucet_settings)
                else:
                    raise ValueError("LLM_PROVIDER must be 'gemini', 'groq', or 'faucet'")
                app.state.chat_intent_settings = intent_settings
                app.state.chat_routing_service = (
                    ChatRoutingService(
                        classifier=intent_classifier,
                        catalog=app.state.ready_document_catalog,
                        model_id=intent_settings.model,
                        timeout_ms=intent_settings.timeout_ms,
                        max_attempts=intent_settings.max_attempts,
                        tool_axis_enabled=intent_settings.tool_axis_enabled,
                        sink=LoggingIntentRoutingSink(),
                    )
                    if intent_settings.enabled and user_documents_settings.enabled
                    else None
                )
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
        document_dispatcher = getattr(app.state, "document_ingestion_dispatcher", None)
        if document_dispatcher is not None:
            await document_dispatcher.close()
        retention_manager = getattr(app.state, "document_retention_manager", None)
        if retention_manager is not None:
            await retention_manager.close()
        document_qdrant_to_close = getattr(app.state, "project_document_qdrant_client", None)
        if document_qdrant_to_close is not None:
            await document_qdrant_to_close.close()
        pg_pool = getattr(app.state, "pg_pool", None)
        if pg_pool is not None:
            await pg_pool.close()
        private_storage_client = getattr(app.state, "private_storage_client", None)
        if private_storage_client is not None:
            await private_storage_client.aclose()

    app = FastAPI(title="Module Mail", version="0.1.0", lifespan=lifespan)
    app.include_router(create_chat_router())
    app.include_router(create_project_router())

    @app.get("/health")
    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/cowork/chat/document-health", response_model=None)
    async def document_health() -> JSONResponse:
        settings = app.state.user_documents_settings
        checks: dict[str, str] = {
            "feature": "enabled" if settings.enabled else "disabled",
            "postgresql": "disabled",
            "encrypted_store": "disabled",
            "redis": "disabled",
            "redis_mode": "redis" if app.state.redis_client is not None else "local",
            "qdrant": "disabled",
            "gemini_embeddings": "disabled",
            "mistral_ocr": "disabled",
            "local_pdf_tools": "ready"
            if shutil.which("detect-pdf") and shutil.which("pdf2md")
            else "unavailable",
        }
        if not settings.enabled:
            return JSONResponse({"status": "disabled", "checks": checks})
        checks["gemini_embeddings"] = "configured"
        checks["mistral_ocr"] = "configured"
        pool = app.state.pg_pool
        if pool is not None:
            try:
                async with pool.connection() as connection:
                    await connection.execute("SELECT 1")
                checks["postgresql"] = "ready"
            except Exception:
                checks["postgresql"] = "unavailable"
        store = app.state.project_document_store
        checks["encrypted_store"] = "ready" if store and store.healthy() else "unavailable"
        redis_client = app.state.redis_client
        if redis_client is not None:
            try:
                await redis_client.ping()
                checks["redis"] = "ready"
            except Exception:
                checks["redis"] = "unavailable"
        else:
            checks["redis"] = "local_fallback"
        qdrant = app.state.project_document_qdrant_client
        if qdrant is not None:
            try:
                await qdrant.get_collections()
                checks["qdrant"] = "ready"
            except Exception:
                checks["qdrant"] = "unavailable"
        required = [
            "encrypted_store",
            "qdrant",
            "gemini_embeddings",
            "mistral_ocr",
            "local_pdf_tools",
        ]
        if pool is not None:
            required.append("postgresql")
        ready = all(checks[name] in {"ready", "configured"} for name in required)
        return JSONResponse(
            {"status": "ready" if ready else "degraded", "checks": checks},
            status_code=200 if ready else 503,
        )

    @app.get("/v1/conversations")
    async def legacy_list_conversations() -> dict[str, list[object]]:
        return {"items": []}

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
        identity_repository = getattr(request.app.state, "identity_repository", None)
        session_repository = getattr(request.app.state, "session_repository", None)
        if identity_repository is not None and session_repository is not None:
            principal = await identity_repository.resolve_or_create_principal(
                connection.email_address
            )
            token, _ = await session_repository.create(
                principal,
                now=datetime.now(UTC),
                ttl_seconds=_session_settings(request).session_ttl_seconds,
            )
            _set_session_cookie(response, _session_settings(request), token)
        return response

    @app.get("/v1/mail-todo/connections")
    async def list_connections(request: Request) -> dict[str, Any]:
        repository = cast(MailboxConnectionRepository, request.app.state.connection_repository)
        principal = await _authenticated_principal(
            request, required=getattr(request.app.state, "session_repository", None) is not None
        )
        connections = (
            await repository.list_for_user(principal.user_id)
            if principal is not None
            else await cast(Any, repository).list_all()
        )
        return {"connections": [_public_connection(item) for item in connections]}

    @app.delete("/v1/mail-todo/connections/{connection_id}")
    async def disconnect_gmail(connection_id: str, request: Request) -> dict[str, bool]:
        connection = await _owned_connection(request, connection_id, "Gmail connection not found")
        principal = await _connection_principal(request, connection)
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
        await _owned_connection(request, connection_id, "Gmail connection not found")
        try:
            page = await _gmail_mailbox(request).search_unread(connection_id, DEFAULT_QUERY, limit)
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
        connection = await _owned_connection(
            request, mailbox_connection_id, "Gmail connection not found"
        )
        principal = await _connection_principal(request, connection)
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
        connection = await _owned_connection(
            request, payload.mailbox_connection_id, "Gmail connection not found"
        )
        principal = await _connection_principal(request, connection)
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
    async def knowledge_chat(body: KnowledgeChatRequest, request: Request) -> dict[str, Any]:
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
            filters=RetrievalFilters(tenant_scope=LOCAL_TENANT_ID, document_status=("ready",)),
            limits=RetrievalLimits(top_k=body.top_k, min_score=-1.0, timeout_ms=8_000),
        )
        response = await memory.retrieve(retrieval_request)
        return response.to_dict()

    # ── Artifacts / Reports endpoints for chat-generated document display ──

    REPORTS_DIR = Path(__file__).resolve().parents[2] / "data" / "reports"
    EXTRACTED_DIR = Path(__file__).resolve().parents[2] / "data" / "extracted"

    @app.get("/api/v1/reports")
    async def list_reports() -> list[dict[str, Any]]:
        reports: list[dict[str, Any]] = []
        seen: set[str] = set()
        dirs_to_scan = [d for d in (REPORTS_DIR, EXTRACTED_DIR) if d.exists()]
        for folder in dirs_to_scan:
            for item in sorted(folder.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                if (
                    item.is_file()
                    and item.name != "ingestion-manifest.json"
                    and item.name not in seen
                ):
                    try:
                        seen.add(item.name)
                        stat = item.stat()
                        content = item.read_text(encoding="utf-8", errors="replace")
                        mtime = datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()
                        reports.append(
                            {
                                "filename": item.name,
                                "content": content,
                                "size": stat.st_size,
                                "updated_at": mtime,
                            }
                        )
                    except Exception as exc:
                        logger.warning("Failed to read report file %s: %s", item.name, exc)
        return reports

    @app.post("/api/v1/reports")
    async def save_report(body: SaveReportRequest) -> dict[str, Any]:
        safe_name = Path(body.filename).name
        if not safe_name or safe_name in (".", ".."):
            raise HTTPException(status_code=400, detail="Invalid filename")
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        target_path = REPORTS_DIR / safe_name
        target_path.write_text(body.content, encoding="utf-8")
        stat = target_path.stat()
        return {
            "filename": safe_name,
            "content": body.content,
            "size": stat.st_size,
            "updated_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
        }

    @app.delete("/api/v1/reports/{filename}")
    async def delete_report(filename: str) -> dict[str, str]:
        safe_name = Path(filename).name
        target_path = REPORTS_DIR / safe_name
        if target_path.is_file():
            target_path.unlink()
        return {"status": "success", "message": f"Deleted {safe_name}"}

    return app


def _connection_service(request: Request) -> GmailConnectionService:
    return cast(GmailConnectionService, request.app.state.gmail_connections)


async def _authenticated_principal(
    request: Request, *, required: bool = True
) -> VerifiedPrincipal | None:
    """Resolve the opaque session only in the PostgreSQL multi-user runtime."""
    sessions = getattr(request.app.state, "session_repository", None)
    if sessions is None:
        return None
    principal = await principal_from_opaque_session(
        request.cookies.get(_session_settings(request).cookie_name), sessions
    )
    if principal is None and required:
        raise HTTPException(status_code=401, detail="Authentication required")
    return principal


async def _connection_principal(
    request: Request, connection: MailboxConnection
) -> VerifiedPrincipal:
    """Use the opaque session in Postgres mode and legacy identity locally."""
    if getattr(request.app.state, "session_repository", None) is not None:
        principal = await _authenticated_principal(request)
        assert principal is not None
        return principal
    return principal_for_connection(connection)


async def _owned_connection(
    request: Request, connection_id: str, detail: str
) -> MailboxConnection:
    repository = cast(MailboxConnectionRepository, request.app.state.connection_repository)
    connection = await repository.get(connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail=detail)
    principal = await _connection_principal(request, connection)
    _require_owned_connection(principal, connection, detail=detail)
    return connection


def _set_session_cookie(response: Response, settings: SessionSettings, token: str) -> None:
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
    connection = await _owned_connection(request, run.mailbox_connection_id, detail)
    principal = await _connection_principal(request, connection)
    if principal.user_id != run.user_id:
        raise HTTPException(status_code=404, detail=detail)
    _require_owned_connection(principal, connection, detail=detail)


def _gmail_settings(request: Request) -> GmailSettings:
    return cast(GmailSettings, request.app.state.gmail_settings)


def _session_settings(request: Request) -> SessionSettings:
    return cast(SessionSettings, request.app.state.session_settings)


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
            {"code": run.error_code, "message": run.error_message_safe} if run.error_code else None
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
    load_dotenv(Path.cwd() / ".env", override=False)
    # Without a root handler the stdlib drops every INFO record, which silently
    # discards the whole trace-sink stream (§13) — the observability surface is
    # log lines, so an unconfigured logger means no observability at all.
    # .upper() because basicConfig rejects "debug" with a ValueError, which would
    # kill the process at startup over a lowercase env var.
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    loop = "auto"
    if database_url() and sys.platform == "win32":
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
