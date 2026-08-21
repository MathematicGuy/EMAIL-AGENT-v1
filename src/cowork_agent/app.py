"""Runnable FastAPI entry point for mailbox and Cowork workflows."""

import json
import logging
import os
import re
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import uvicorn
from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

import cowork_agent.integrations.llm.langfuse_bootstrap as _langfuse_bootstrap  # noqa: F401
from cowork_agent.config import (
    ChatIntentSettings,
    ChatMemorySettings,
    EmailRagQualitySettings,
    GeminiSettings,
    GmailSettings,
    GroqSettings,
    JinaEmbeddingSettings,
    MistralSettings,
    OpenRouterSettings,
    OutlookSettings,
    SessionSettings,
    SupabaseStorageSettings,
    UserDocumentsSettings,
    database_url,
    load_runtime_environment,
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
    ChatSessionRegistryPort,
    UnavailableChatReply,
)
from cowork_agent.features.ai_chat.intent.observability import LoggingIntentRoutingSink
from cowork_agent.features.ai_chat.intent.service import (
    CanonicalReadyDocumentCatalog,
    ChatRoutingService,
)
from cowork_agent.features.ai_chat.memory_gateway import MemoryGateway
from cowork_agent.features.ai_chat.memory_observability import (
    LoggingMemoryOperationSink,
    MemoryOperationMetrics,
)
from cowork_agent.features.ai_chat.ports import (
    ChatHistoryPort,
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
    create_guest_session,
    ensure_principal_owns_connection,
    principal_for_connection,
    principal_from_opaque_session,
)
from cowork_agent.integrations.gmail.auth import OAuthStateManager, TokenCipher
from cowork_agent.integrations.gmail.fakes import SafeTextAttachmentExtractor
from cowork_agent.integrations.gmail.provider import (
    GmailConnectionService,
    GmailMailboxAdapter,
)
from cowork_agent.integrations.llm.chat_intent import (
    GeminiIntentClassifier,
    GroqIntentClassifier,
    MistralIntentClassifier,
    OpenRouterIntentClassifier,
)
from cowork_agent.integrations.llm.chat_reply import (
    GeminiChatReply,
    GroqChatReply,
    MistralChatReply,
    OpenRouterChatReply,
)
from cowork_agent.integrations.llm.last_resort import load_optional_gemini_settings
from cowork_agent.integrations.llm.providers.gemini import (
    GeminiActionPlanGenerator,
    GeminiRetrievalQueryRewriter,
    GeminiRouteClassifier,
)
from cowork_agent.integrations.llm.providers.groq import (
    GroqActionPlanGenerator,
    GroqRouteClassifier,
)
from cowork_agent.integrations.llm.providers.mistral import (
    MistralActionPlanGenerator,
    MistralRouteClassifier,
)
from cowork_agent.integrations.llm.providers.openrouter import (
    OpenRouterActionPlanGenerator,
    OpenRouterRouteClassifier,
)
from cowork_agent.integrations.mailbox import (
    MailboxNotConnectedError,
    MailboxPermissionDeniedError,
    MailboxReauthRequiredError,
    MailboxTemporaryError,
    ProviderRoutingMailboxAdapter,
)
from cowork_agent.integrations.outlook import OutlookConnectionService, OutlookMailboxAdapter
from cowork_agent.integrations.rag.bootstrap import (
    RAG_CORPUS_PATH,
    build_document_embedder,
    build_semantic_memory,
)
from cowork_agent.integrations.rag.chat_memory import SemanticChatMemoryAdapter
from cowork_agent.integrations.rag.knowledge_base import KnowledgeDocument, load_corpus
from cowork_agent.integrations.rag.null_memory import NullSemanticMemory
from cowork_agent.integrations.rag.project_documents import (
    CanonicalProjectDocumentRetriever,
    HybridProjectDocumentStore,
)
from cowork_agent.integrations.rag.project_index import TurbovecProjectIndexStore
from cowork_agent.integrations.storage.supabase import SupabasePrivateStorage
from cowork_agent.orchestration.local import InMemoryOutbox
from cowork_agent.persistence.repositories.local import InMemoryResultRepository
from cowork_agent.persistence.repositories.mailbox_connections import (
    SQLiteMailboxConnectionRepository,
)
from cowork_agent.persistence.repositories.project_document_chunks import (
    PostgresProjectDocumentChunkRepository,
)
from cowork_agent.persistence.repositories.runs import SQLiteRunRepository
from cowork_agent.persistence.repositories.tasks import SQLiteTaskRepository
from cowork_agent.runtime import (
    configure_windows_event_loop_policy,
    configure_windows_reload,
)

from .api.chat import create_chat_router
from .api.handlers import _jsonable
from .api.projects import create_project_router

# ``uvicorn cowork_agent.app:create_app --factory`` bypasses ``main()``. Set
# the policy during module import as well, before Uvicorn creates its loop.
configure_windows_event_loop_policy()
configure_windows_reload()

logger = logging.getLogger(__name__)


async def _resolve_chat_principal(request: Request) -> VerifiedPrincipal:
    """Resolve chat identity from a session, with the local MVP fallback."""

    if getattr(request.app.state, "chat_opaque_session_repository", None) is not None:
        principal = await _authenticated_chat_principal(request)
        assert principal is not None
        return principal

    repository: SQLiteMailboxConnectionRepository = request.app.state.connection_repository
    candidates = tuple(
        connection
        for connection in await repository.list_all()
        if connection.status == "active" and connection.provider == "gmail"
    )
    if len(candidates) != 1:
        raise HTTPException(status_code=503, detail="Chat identity is unavailable")
    return principal_for_connection(candidates[0])


RAW_DOCS_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
EXTRACTED_DIR = Path(__file__).resolve().parents[2] / "data" / "extracted"


def _resolve_raw_document(filename: str) -> tuple[str, Path]:
    """Map a request path segment onto a real file directly inside ``RAW_DOCS_DIR``.

    ``Path(...).name`` strips any directory part, and the containment check then
    rejects a symlink inside the corpus that points somewhere else. Raises the HTTP
    error the four raw-document handlers all want, so they stay identical.
    """
    safe_name = Path(filename).name
    if not safe_name or safe_name in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid filename")
    target = RAW_DOCS_DIR / safe_name
    if not target.is_file() or not target.resolve().is_relative_to(RAW_DOCS_DIR.resolve()):
        raise HTTPException(status_code=404, detail="Raw document not found")
    return safe_name, target


class CreateRunRequest(BaseModel):
    mailbox_connection_id: str = Field(alias="mailboxConnectionId")
    query: str = DEFAULT_QUERY
    max_emails: int = Field(default=10, alias="maxEmails", ge=1, le=500)


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
            history=cast(
                ChatHistoryPort | None,
                getattr(app.state, "chat_history_repository", None),
            ),
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
    load_runtime_environment()
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

                script_path = (
                    Path(__file__).resolve().parents[2] / "scripts" / "update_corpus2skill.py"
                )
                if script_path.exists():
                    spec = importlib.util.spec_from_file_location(
                        "update_corpus2skill", script_path
                    )
                    if spec and spec.loader:
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        if hasattr(mod, "main"):
                            mod.main()
            except Exception as exc:
                logger.warning("Corpus skill tree auto-update skipped: %s", exc)

            settings = GmailSettings.from_env()
            control_plane_url = database_url()
            outlook_settings: OutlookSettings | None = None
            outlook_configuration_error: str | None = None
            microsoft_credentials_present = bool(
                os.getenv("MICROSOFT_CLIENT_ID", "").strip()
                or os.getenv("MICROSOFT_CLIENT_SECRET", "").strip()
            )
            if microsoft_credentials_present:
                try:
                    outlook_settings = OutlookSettings.from_env()
                except ValueError as exc:
                    # Outlook is optional and must never prevent the Gmail/chat
                    # application from booting. Keep only the safe validation
                    # diagnostic server-side; the public capability uses a
                    # stable reason code.
                    outlook_configuration_error = str(exc)
                    logger.warning("Outlook connector is unavailable: %s", exc)
            else:
                outlook_configuration_error = "Microsoft OAuth is not configured"
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
            app.state.chat_identity_repository = None
            app.state.chat_opaque_session_repository = None
            run_repository: RunRepository
            task_repository: TaskRepository
            chat_session_registry: ChatSessionRegistryPort
            result_repository = InMemoryResultRepository()
            if control_plane_url:
                # V1-H T5.1 durable control plane: PostgreSQL is the source
                # of truth for runs, tasks, and outbox events. Imports stay
                # lazy so the app boots without the optional postgres extra.
                from psycopg_pool import AsyncConnectionPool

                from cowork_agent.persistence.migrate import apply_migrations
                from cowork_agent.persistence.pool import control_plane_pool_kwargs
                from cowork_agent.persistence.repositories.chat_history import (
                    PostgresChatHistoryRepository,
                )
                from cowork_agent.persistence.repositories.chat_sessions import (
                    PostgresChatSessionRegistry,
                )
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
                from cowork_agent.persistence.repositories.projects import (
                    PostgresProjectRepository,
                )

                pool = AsyncConnectionPool(
                    control_plane_url,
                    **control_plane_pool_kwargs(),
                )
                await pool.open(wait=True)
                await apply_migrations(pool)
                run_repository = PostgresRunRepository(pool)
                task_repository = PostgresTaskRepository(pool)
                app.state.outbox_repository = PostgresOutboxRepository(pool)
                app.state.chat_profile_repository = PostgresChatProfileRepository(pool)
                app.state.chat_task_episode_repository = PostgresTaskEpisodeRepository(pool)
                app.state.project_repository = PostgresProjectRepository(pool)
                chat_session_registry = PostgresChatSessionRegistry(pool)
                app.state.chat_history_repository = PostgresChatHistoryRepository(pool)
                app.state.chat_session_repository = chat_session_registry
                app.state.pg_pool = pool
                app.state.identity_repository = PostgresIdentityRepository(pool)
                app.state.session_repository = PostgresSessionRepository(pool)
                app.state.chat_identity_repository = app.state.identity_repository
                app.state.chat_opaque_session_repository = app.state.session_repository
                repository = PostgresMailboxConnectionRepository(pool)
            else:
                from cowork_agent.persistence.repositories.sqlite_chat import SQLiteChatRepository
                from cowork_agent.persistence.repositories.sqlite_chat_identity import (
                    SQLiteChatIdentityRepository,
                )
                from cowork_agent.persistence.repositories.sqlite_projects import (
                    SQLiteProjectRepository,
                )

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
                sqlite_chat_repository = SQLiteChatRepository(
                    settings.connection_db_path.parent / "chat.db"
                )
                await sqlite_chat_repository.initialize()
                sqlite_chat_identity_repository = SQLiteChatIdentityRepository(
                    settings.connection_db_path.parent / "chat_identity.db"
                )
                await sqlite_chat_identity_repository.initialize()
                app.state.chat_profile_repository = sqlite_chat_repository
                app.state.chat_task_episode_repository = sqlite_chat_repository
                app.state.chat_session_repository = sqlite_chat_repository
                app.state.chat_history_repository = sqlite_chat_repository
                app.state.pg_pool = None
                sqlite_project_repository = SQLiteProjectRepository(
                    settings.connection_db_path.parent / "projects.db"
                )
                await sqlite_project_repository.initialize()
                app.state.project_repository = sqlite_project_repository
                app.state.chat_identity_repository = sqlite_chat_identity_repository
                app.state.chat_opaque_session_repository = sqlite_chat_identity_repository
                chat_session_registry = sqlite_chat_repository
            app.state.connection_repository = repository
            mailbox_cipher = TokenCipher(settings.token_encryption_key)
            app.state.gmail_connections = GmailConnectionService(
                settings,
                repository,
                mailbox_cipher,
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
                mailbox_cipher,
            )
            mailbox_adapters: dict[str, Any] = {"gmail": app.state.gmail_mailbox}
            outlook_enabled = outlook_settings is not None and not control_plane_url
            if outlook_enabled:
                assert outlook_settings is not None
                app.state.outlook_connections = OutlookConnectionService(
                    outlook_settings,
                    repository,
                    mailbox_cipher,
                    OAuthStateManager(
                        outlook_settings.oauth_state_secret,
                        outlook_settings.oauth_state_ttl_seconds,
                    ),
                )
                app.state.outlook_mailbox = OutlookMailboxAdapter(
                    outlook_settings,
                    repository,
                    mailbox_cipher,
                )
                mailbox_adapters["outlook"] = app.state.outlook_mailbox
                outlook_reason: str | None = None
            else:
                app.state.outlook_connections = None
                app.state.outlook_mailbox = None
                outlook_reason = "sqlite_only" if outlook_settings is not None else "not_configured"
            app.state.outlook_settings = outlook_settings
            app.state.outlook_configuration_error = outlook_configuration_error
            app.state.provider_availability = {
                "gmail": {"enabled": True, "reason": None},
                "outlook": {"enabled": outlook_enabled, "reason": outlook_reason},
            }
            app.state.mailbox = ProviderRoutingMailboxAdapter(repository, mailbox_adapters)
            user_documents_settings = UserDocumentsSettings.from_env()
            app.state.document_embeddings_configured = False
            if control_plane_url and (
                user_documents_settings.enabled or os.getenv("SUPABASE_URL", "").strip()
            ):
                try:
                    storage_settings = SupabaseStorageSettings.from_env()
                    storage_client = httpx.AsyncClient(timeout=30.0)
                    app.state.private_storage_client = storage_client
                    app.state.private_storage = SupabasePrivateStorage(
                        storage_settings.url,
                        storage_settings.secret_key,
                        storage_settings.bucket,
                        storage_client,
                    )
                except ValueError:
                    logger.warning("Project document storage is unavailable")
                    app.state.private_storage_client = None
                    app.state.private_storage = None
            else:
                app.state.private_storage_client = None
                from cowork_agent.integrations.storage.local import LocalPrivateStorage

                app.state.private_storage = LocalPrivateStorage(
                    settings.connection_db_path.parent / "project-documents"
                )
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
            app.state.user_documents_settings = user_documents_settings
            app.state.ready_document_catalog = (
                CanonicalReadyDocumentCatalog(app.state.project_repository)
                if app.state.project_repository is not None
                else None
            )
            app.state.project_document_store = None
            app.state.project_document_vectors = None
            app.state.project_document_index = None
            app.state.chat_principal_resolver = _resolve_chat_principal
            app.state.chat_guest_session_issuer = _issue_chat_guest_session

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

            from cowork_agent.persistence.repositories.sqlite_raw_documents import (
                SQLiteRawDocumentRepository,
            )

            raw_doc_repo = SQLiteRawDocumentRepository(
                settings.connection_db_path.parent / "raw_documents.db"
            )
            await raw_doc_repo.initialize()
            app.state.raw_document_repository = raw_doc_repo

            if user_documents_settings.enabled and app.state.project_repository is not None:
                try:
                    embedder, vector_size = build_document_embedder()
                    app.state.document_embeddings_configured = True
                    if control_plane_url:
                        # The API only reads .tvim snapshots; mail-todo-worker owns
                        # writing them. Both sides exchange them through Supabase
                        # Storage, so a missing local file is pulled on demand.
                        index_store = TurbovecProjectIndexStore(
                            user_documents_settings.index_root,
                            storage=app.state.private_storage,
                            vector_size=vector_size,
                        )
                        vector_store = HybridProjectDocumentStore(
                            PostgresProjectDocumentChunkRepository(app.state.pg_pool),
                            index_store,
                            embedder,
                            vector_size=vector_size,
                        )
                        app.state.project_document_index = index_store
                        app.state.project_document_vectors = CanonicalProjectDocumentRetriever(
                            app.state.project_repository,
                            vector_store,
                            top_k=user_documents_settings.top_k,
                            min_score=user_documents_settings.min_score,
                            timeout_ms=user_documents_settings.retrieval_timeout_ms,
                        )
                    else:
                        from cowork_agent.persistence.repositories import (
                            sqlite_project_document_chunks,
                        )

                        local_chunks = (
                            sqlite_project_document_chunks.SQLiteProjectDocumentChunkRepository(
                                settings.connection_db_path.parent / "project_chunks.db",
                                settings.connection_db_path.parent / "projects.db",
                            )
                        )
                        await local_chunks.initialize()
                        local_index = TurbovecProjectIndexStore(
                            user_documents_settings.index_root,
                            vector_size=vector_size,
                        )
                        local_vectors = HybridProjectDocumentStore(
                            local_chunks,
                            local_index,
                            embedder,
                            vector_size=vector_size,
                        )
                        app.state.project_document_index = local_index
                        app.state.project_document_vectors = CanonicalProjectDocumentRetriever(
                            app.state.project_repository,
                            local_vectors,
                            top_k=user_documents_settings.top_k,
                            min_score=user_documents_settings.min_score,
                            timeout_ms=user_documents_settings.retrieval_timeout_ms,
                        )
                except Exception:
                    logger.exception(
                        "Project document vector store is unavailable; API remains online"
                    )
            app.state.project_document_queue = None
            try:
                provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
                provider_label = {
                    "gemini": "Gemini",
                    "groq": "Groq",
                    "mistral": "Mistral",
                    "openrouter": "OpenRouter",
                }.get(provider, "LLM provider")
                classifier: RouteClassifierPort
                generator: ActionPlanGeneratorPort
                intent_classifier: IntentClassifierPort
                generation_concurrency = 1
                query_rewriter = None
                if provider == "gemini":
                    gemini_settings = GeminiSettings.from_env()
                    generation_concurrency = gemini_settings.action_plan_concurrency
                    intent_settings = ChatIntentSettings.from_env(
                        default_model=gemini_settings.model
                    )
                    intent_classifier = GeminiIntentClassifier.from_settings(
                        gemini_settings, intent_settings
                    )
                    classifier = GeminiRouteClassifier(gemini_settings)
                    generator = GeminiActionPlanGenerator(gemini_settings)
                    query_rewriter = GeminiRetrievalQueryRewriter(gemini_settings)
                    semantic_memory = await build_semantic_memory(JinaEmbeddingSettings.from_env())
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
                elif provider == "mistral":
                    mistral_settings = MistralSettings.from_env()
                    intent_settings = ChatIntentSettings.from_env(
                        default_model=mistral_settings.model
                    )
                    intent_classifier = MistralIntentClassifier.from_settings(
                        mistral_settings, intent_settings
                    )
                    classifier = MistralRouteClassifier(mistral_settings)
                    generator = MistralActionPlanGenerator(mistral_settings)
                    semantic_memory = NullSemanticMemory()
                    app.state.chat_reply = MistralChatReply.from_settings(mistral_settings)
                elif provider == "openrouter":
                    openrouter_settings = OpenRouterSettings.from_env()
                    gemini_last_resort = load_optional_gemini_settings()
                    if gemini_last_resort is None:
                        logger.info(
                            "OpenRouter Gemini last-resort is off; "
                            "no numbered GEMINI_API_KEY_* configured"
                        )
                    else:
                        logger.info(
                            "OpenRouter Gemini last-resort is on (%s)",
                            gemini_last_resort.model,
                        )
                    intent_settings = ChatIntentSettings.from_env(
                        default_model=openrouter_settings.model
                    )
                    intent_classifier = OpenRouterIntentClassifier.from_settings(
                        openrouter_settings,
                        intent_settings,
                        last_resort=gemini_last_resort,
                    )
                    classifier = OpenRouterRouteClassifier(
                        openrouter_settings, last_resort=gemini_last_resort
                    )
                    generator = OpenRouterActionPlanGenerator(
                        openrouter_settings, last_resort=gemini_last_resort
                    )
                    semantic_memory = NullSemanticMemory()
                    app.state.chat_reply = OpenRouterChatReply.from_settings(
                        openrouter_settings, last_resort=gemini_last_resort
                    )
                else:
                    raise ValueError(
                        "LLM_PROVIDER must be 'gemini', 'groq', 'mistral', or 'openrouter'"
                    )
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
                    if (
                        intent_settings.enabled
                        and user_documents_settings.enabled
                        and app.state.ready_document_catalog is not None
                    )
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
                    app.state.mailbox,
                    SafeTextAttachmentExtractor(),
                    classifier,
                    generator,
                    ShortTermStore(),
                    task_repository,
                    semantic_memory=semantic_memory,
                    query_rewriter=query_rewriter,
                    quality_settings=EmailRagQualitySettings.from_env(),
                    trace_sink=LoggingTraceSink(),
                    dev_trace=dev_trace_sink_from_env(
                        settings.connection_db_path.parent, settings.token_encryption_key
                    ),
                    completion_outbox=app.state.outbox_repository,
                    mailbox_fetch_concurrency=settings.fetch_concurrency,
                    generation_concurrency=generation_concurrency,
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
            raise RuntimeError(f"Invalid application configuration: {exc}") from exc
        yield
        # The project index holds no network handle of its own: it reads local
        # .tvim files and borrows private_storage, which is closed below.
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
            "supabase_storage": "disabled",
            "redis": "disabled",
            "redis_mode": "redis" if app.state.redis_client is not None else "local",
            "project_index": "disabled",
            "gemini_embeddings": "disabled",
            "ocr": "optional_unavailable",
            "classifier": "disabled",
            "worker_queue": "unavailable",
        }
        if not settings.enabled:
            return JSONResponse({"status": "disabled", "checks": checks})
        if app.state.document_embeddings_configured:
            checks["gemini_embeddings"] = "configured"
        checks["classifier"] = (
            "ready" if app.state.chat_routing_service is not None else "unavailable"
        )
        pool = app.state.pg_pool
        if pool is not None:
            try:
                async with pool.connection() as connection:
                    await connection.execute("SELECT 1")
                checks["postgresql"] = "ready"
            except Exception:
                checks["postgresql"] = "unavailable"
        checks["supabase_storage"] = (
            "configured" if app.state.private_storage is not None else "unavailable"
        )
        redis_client = app.state.redis_client
        if redis_client is not None:
            try:
                await redis_client.ping()
                checks["redis"] = "ready"
            except Exception:
                checks["redis"] = "unavailable"
        else:
            checks["redis"] = "local_fallback"
        index_store = app.state.project_document_index
        if index_store is not None:
            # A .tvim is pulled per project on demand, so the only thing that
            # can be checked without a project in hand is that the API can
            # actually write the root it will cache snapshots into.
            try:
                index_store.root.mkdir(parents=True, exist_ok=True)
                checks["project_index"] = (
                    "ready" if os.access(index_store.root, os.W_OK) else ("unavailable")
                )
            except OSError:
                checks["project_index"] = "unavailable"
        project_repository = app.state.project_repository
        if project_repository is not None:
            try:
                if await project_repository.worker_heartbeat_is_fresh(max_age_seconds=120):
                    checks["worker_queue"] = "ready"
            except Exception:
                checks["worker_queue"] = "unavailable"
        required = [
            "supabase_storage",
            "project_index",
            "gemini_embeddings",
            "classifier",
            "worker_queue",
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

    @app.get("/v1/mail-todo/oauth/outlook/connect")
    async def connect_outlook(
        request: Request,
        owner_connection_id: str = Query(alias="ownerConnectionId", min_length=1),
    ) -> RedirectResponse:
        service = _outlook_connection_service(request)
        if service is None:
            reason = request.app.state.provider_availability["outlook"]["reason"]
            raise HTTPException(
                status_code=503,
                detail=f"Outlook connection is unavailable: {reason}",
            )
        owner = await _owned_connection(
            request, owner_connection_id, "Gmail owner connection not found"
        )
        if owner.provider != "gmail" or owner.status != "active":
            raise HTTPException(status_code=400, detail="An active Gmail owner is required")
        return RedirectResponse(service.begin(owner), status_code=302)

    @app.get("/v1/mail-todo/oauth/outlook/callback", response_model=None)
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
                return _frontend_mail_redirect(
                    settings.frontend_url, "denied", provider="outlook"
                )
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
        return {
            "connections": [_public_connection(item) for item in connections],
            "providerAvailability": request.app.state.provider_availability,
        }

    @app.delete("/v1/mail-todo/connections/{connection_id}")
    async def disconnect_mailbox(connection_id: str, request: Request) -> dict[str, bool]:
        connection = await _owned_connection(request, connection_id, "Mailbox connection not found")
        principal = await _connection_principal(request, connection)
        repository = cast(MailboxConnectionRepository, request.app.state.connection_repository)
        deleted = await repository.delete(connection_id, principal.user_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Mailbox connection not found")
        return {"disconnected": True}

    @app.get("/v1/mail-todo/connections/{connection_id}/unread-preview")
    async def unread_preview(
        connection_id: str,
        request: Request,
        limit: int = Query(default=10, ge=1, le=20),
    ) -> dict[str, Any]:
        connection = await _owned_connection(request, connection_id, "Mailbox connection not found")
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

    @app.get("/v1/mail-todo/runs")
    async def list_digest_runs(
        request: Request,
        mailbox_connection_id: str = Query(alias="mailboxConnectionId", min_length=1),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, Any]:
        connection = await _owned_connection(
            request, mailbox_connection_id, "Mailbox connection not found"
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
            request, payload.mailbox_connection_id, "Mailbox connection not found"
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
            await run_queue.enqueue_digest_run(run.id, user_id=principal.user_id)
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
                "filteredSummary": run.filtered_summary,
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
            user_id="demo-gui",
            query=body.query,
            knowledge_gaps=(),
            filters=RetrievalFilters(document_status=("ready",)),
            limits=RetrievalLimits(top_k=body.top_k, min_score=-1.0, timeout_ms=8_000),
        )
        response = await memory.retrieve(retrieval_request)
        return response.to_dict()

    # ── Artifacts / Reports endpoints for chat-generated document display ──

    REPORTS_DIR = Path(__file__).resolve().parents[2] / "data" / "reports"
    # EXTRACTED_DIR is module-level. A local copy here silently shadowed it for every
    # closure in create_app, including the raw-document handlers further down.

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

    # ── Raw process documents (data/raw) endpoints for procedure viewer ──
    # RAW_DOCS_DIR / EXTRACTED_DIR are module-level so tests and handlers agree on
    # one location; do not shadow them with a local copy here.

    def _load_raw_manifest() -> dict[str, str]:
        manifest_file = EXTRACTED_DIR / "ingestion-manifest.json"
        if not manifest_file.is_file():
            return {}
        try:
            raw = json.loads(manifest_file.read_text(encoding="utf-8"))
            return {
                k: str(v["output"])
                for k, v in raw.items()
                if isinstance(v, dict) and "output" in v
            }
        except Exception as exc:
            logger.warning("Failed to load ingestion-manifest.json: %s", exc)
            return {}

    def _find_associated_extracted_docs(filename: str, manifest: dict[str, str]) -> list[Path]:
        """Find all extracted markdown files associated with a raw document."""
        if not EXTRACTED_DIR.exists():
            return []

        matches: list[Path] = []
        manifest_target = manifest.get(filename)
        if manifest_target:
            # The manifest is generated locally today, but its `output` values are
            # still data: a `../` or absolute entry would otherwise let /extracted
            # read -- and DELETE, via the delete endpoint -- files outside the
            # extracted corpus.
            p = (EXTRACTED_DIR / manifest_target).resolve()
            if p.is_file() and p.is_relative_to(EXTRACTED_DIR.resolve()):
                matches.append(p)

        raw_key = re.sub(r"[^a-z0-9]", "", Path(filename).stem.lower())
        if raw_key:
            for item in EXTRACTED_DIR.iterdir():
                if (
                    item.is_file()
                    and item.suffix.lower() == ".md"
                    and item.name != "ingestion-manifest.json"
                    and item not in matches
                ):
                    if re.sub(r"[^a-z0-9]", "", item.stem.lower()) == raw_key:
                        matches.append(item)
        return matches

    def _resolve_extracted_doc(filename: str, manifest: dict[str, str]) -> str | None:
        matches = _find_associated_extracted_docs(filename, manifest)
        return matches[0].name if matches else None

    @app.post("/api/v1/raw-documents/upload")
    async def upload_raw_document(
        request: Request,
        file: Annotated[UploadFile, File()],
    ) -> dict[str, Any]:
        if not file.filename:
            raise HTTPException(status_code=400, detail="Filename required")

        safe_name = Path(file.filename).name
        if not safe_name or safe_name in (".", ".."):
            raise HTTPException(status_code=400, detail="Invalid filename")

        ext = Path(safe_name).suffix.lower().lstrip(".")
        if ext not in ("pdf", "docx", "doc"):
            raise HTTPException(
                status_code=400,
                detail=f"Định dạng không được hỗ trợ: .{ext}. Chỉ chấp nhận .pdf, .docx, .doc",
            )

        target_raw = RAW_DOCS_DIR / safe_name
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Tệp rỗng")

        target_raw.write_bytes(content)
        repo = await _raw_document_repo(request)
        await repo.record_save(safe_name, status=2)
        logger.info("Uploaded raw document %s into data/raw/ (%d bytes)", safe_name, len(content))

        # Auto-extract markdown into data/extracted/
        has_extracted = False
        extracted_name: str | None = None
        if ext in ("docx", "doc"):
            try:
                from cowork_agent.integrations.knowledge_ingestion.docx_extractor import (
                    DocxExtractor,
                )

                extracted = DocxExtractor().extract(target_raw)
                stem = target_raw.stem.lower().replace("_", "-").replace(" ", "-")
                extracted_name = f"{stem}.md"
                target_extracted = EXTRACTED_DIR / extracted_name
                target_extracted.write_text(extracted.markdown, encoding="utf-8")
                has_extracted = True
                logger.info("Extracted markdown for %s -> %s", safe_name, extracted_name)
            except Exception as extract_err:
                logger.warning("Could not extract markdown for %s: %s", safe_name, extract_err)
        elif ext == "pdf":
            try:
                from cowork_agent.integrations.knowledge_ingestion.pdf_inspector import (
                    PdfInspector,
                )

                inspection = PdfInspector().inspect(target_raw)
                pdf_text = "\n\n".join(inspection.native_markdown_by_page.values()).strip()
                if pdf_text:
                    stem = target_raw.stem.lower().replace("_", "-").replace(" ", "-")
                    extracted_name = f"{stem}.md"
                    target_extracted = EXTRACTED_DIR / extracted_name
                    target_extracted.write_text(pdf_text, encoding="utf-8")
                    has_extracted = True
                    logger.info("Extracted PDF text for %s -> %s", safe_name, extracted_name)
            except Exception as extract_err:
                logger.warning("Could not extract PDF text for %s: %s", safe_name, extract_err)

        return {
            "status": "uploaded",
            "filename": safe_name,
            "size": len(content),
            "file_type": ext,
            "has_extracted_md": has_extracted,
            "extracted_md_name": extracted_name,
        }

    @app.get("/api/v1/raw-documents")
    async def list_raw_documents() -> list[dict[str, Any]]:
        if not RAW_DOCS_DIR.exists():
            return []

        manifest = _load_raw_manifest()
        documents: list[dict[str, Any]] = []
        for item in sorted(
            RAW_DOCS_DIR.iterdir(),
            key=lambda p: p.stat().st_mtime if p.is_file() else 0,
            reverse=True,
        ):
            if not item.is_file() or item.name.startswith("."):
                continue
            try:
                stat = item.stat()
                extracted_md = _resolve_extracted_doc(item.name, manifest)
                documents.append(
                    {
                        "filename": item.name,
                        "file_type": item.suffix.lower().lstrip("."),
                        "size": stat.st_size,
                        "updated_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                        "has_extracted_md": extracted_md is not None,
                        "extracted_md_name": extracted_md,
                    }
                )
            except Exception as exc:
                logger.warning("Failed to inspect raw document %s: %s", item.name, exc)
        return documents

    @app.get("/api/v1/raw-documents/{filename}")
    async def get_raw_document(filename: str) -> FileResponse:
        safe_name, target_path = _resolve_raw_document(filename)

        ext = target_path.suffix.lower()
        media_types = {
            ".pdf": "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".doc": "application/msword",
            ".txt": "text/plain",
            ".md": "text/markdown",
        }
        media_type = media_types.get(ext, "application/octet-stream")
        return FileResponse(
            path=target_path,
            media_type=media_type,
            content_disposition_type="inline",
        )

    @app.get("/api/v1/raw-documents/{filename}/extracted")
    async def get_raw_document_extracted_text(filename: str) -> dict[str, Any]:
        safe_name, _ = _resolve_raw_document(filename)

        extracted_md = _resolve_extracted_doc(safe_name, _load_raw_manifest())
        if not extracted_md:
            raise HTTPException(
                status_code=404, detail="Extracted markdown not found for this document"
            )

        content = (EXTRACTED_DIR / extracted_md).read_text(encoding="utf-8", errors="replace")
        return {
            "filename": safe_name,
            "extracted_md_name": extracted_md,
            "content": content,
        }

    @app.put("/api/v1/raw-documents/{filename}")
    async def put_raw_document(
        filename: str, request: Request
    ) -> dict[str, Any]:
        safe_name = Path(filename).name
        if not safe_name or safe_name in (".", ".."):
            raise HTTPException(status_code=400, detail="Invalid filename")

        target_raw = RAW_DOCS_DIR / safe_name
        if not target_raw.is_file():
            raise HTTPException(status_code=404, detail="Raw document not found")

        content = await request.body()
        if not content:
            raise HTTPException(status_code=400, detail="Empty document payload")

        target_raw.write_bytes(content)
        repo = await _raw_document_repo(request)
        await repo.record_save(safe_name, status=2)
        logger.info("Saved raw document %s directly (%d bytes)", safe_name, len(content))

        # Re-extract markdown if it was a docx file
        ext = target_raw.suffix.lower().lstrip(".")
        if ext in ("docx", "doc"):
            try:
                from cowork_agent.integrations.knowledge_ingestion.docx_extractor import (
                    DocxExtractor,
                )

                extracted = DocxExtractor().extract(target_raw)
                extracted_md_name = _resolve_extracted_doc(safe_name, _load_raw_manifest())
                if extracted_md_name:
                    target_extracted = EXTRACTED_DIR / extracted_md_name
                    target_extracted.write_text(extracted.markdown, encoding="utf-8")
                    logger.info("Updated extracted markdown for %s", safe_name)
            except Exception as extract_err:
                logger.warning("Could not re-extract markdown for %s: %s", safe_name, extract_err)

        return {"status": "saved", "filename": safe_name, "size": len(content)}

    @app.delete("/api/v1/raw-documents/{filename}")
    async def delete_raw_document(
        filename: str, request: Request
    ) -> dict[str, Any]:
        safe_name = Path(filename).name
        if not safe_name or safe_name in (".", ".."):
            raise HTTPException(status_code=400, detail="Invalid filename")

        target_raw = RAW_DOCS_DIR / safe_name
        if not target_raw.is_file():
            raise HTTPException(status_code=404, detail="Raw document not found")

        try:
            target_raw.unlink(missing_ok=True)
            logger.info("Deleted raw document %s", safe_name)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to delete file: {exc}") from exc

        repo = await _raw_document_repo(request)
        if hasattr(repo, "delete"):
            try:
                await repo.delete(safe_name)
            except Exception as repo_err:
                logger.warning("Could not delete metadata for %s: %s", safe_name, repo_err)

        manifest = _load_raw_manifest()
        deleted_extracted: list[str] = []
        for target_extracted in _find_associated_extracted_docs(safe_name, manifest):
            try:
                target_extracted.unlink(missing_ok=True)
                deleted_extracted.append(target_extracted.name)
                logger.info(
                    "Deleted extracted markdown %s for %s", target_extracted.name, safe_name
                )
            except Exception as exc:
                logger.warning(
                    "Could not delete extracted markdown %s: %s", target_extracted.name, exc
                )

        return {"status": "deleted", "filename": safe_name, "deleted_extracted": deleted_extracted}

    return app


async def _raw_document_repo(request: Request) -> Any:
    repo = getattr(request.app.state, "raw_document_repository", None)
    if repo is None:
        from cowork_agent.persistence.repositories.sqlite_raw_documents import (
            SQLiteRawDocumentRepository,
        )

        # Mirror the startup path's location so a request that arrives before (or
        # without) lifespan startup still reads the same version history.
        settings = getattr(request.app.state, "gmail_settings", None)
        parent = (
            settings.connection_db_path.parent
            if settings is not None
            else Path.cwd() / "data"
        )
        repo = SQLiteRawDocumentRepository(parent / "raw_documents.db")
        # Without this the table is never created and every query raises
        # "no such table: raw_document_metadata".
        await repo.initialize()
        request.app.state.raw_document_repository = repo
    return repo


def _connection_service(request: Request) -> GmailConnectionService:
    return cast(GmailConnectionService, request.app.state.gmail_connections)


def _outlook_connection_service(request: Request) -> OutlookConnectionService | None:
    return cast(OutlookConnectionService | None, request.app.state.outlook_connections)


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


async def _authenticated_chat_principal(
    request: Request, *, required: bool = True
) -> VerifiedPrincipal | None:
    """Resolve a browser's opaque chat session in either persistence mode."""
    sessions = getattr(request.app.state, "chat_opaque_session_repository", None)
    if sessions is None:
        sessions = getattr(request.app.state, "session_repository", None)
    if sessions is None:
        return None
    principal = await principal_from_opaque_session(
        request.cookies.get(_session_settings(request).cookie_name), sessions
    )
    if principal is None and required:
        raise HTTPException(status_code=401, detail="Authentication required")
    return principal


async def _issue_chat_guest_session(request: Request, response: Response) -> None:
    """Bootstrap an isolated guest workspace without replacing an existing session."""
    existing = await _authenticated_chat_principal(request, required=False)
    if existing is not None:
        return

    identities = getattr(
        request.app.state,
        "chat_identity_repository",
        getattr(request.app.state, "identity_repository", None),
    )
    sessions = getattr(
        request.app.state,
        "chat_opaque_session_repository",
        getattr(request.app.state, "session_repository", None),
    )
    if identities is None or sessions is None:
        raise HTTPException(status_code=503, detail="Guest chat is unavailable")

    settings = _session_settings(request)
    _, token = await create_guest_session(
        identities,
        sessions,
        ttl_seconds=settings.session_ttl_seconds,
    )
    _set_session_cookie(response, settings, token)


async def _connection_principal(
    request: Request, connection: MailboxConnection
) -> VerifiedPrincipal:
    """Use the opaque session in Postgres mode and legacy identity locally."""
    if getattr(request.app.state, "session_repository", None) is not None:
        principal = await _authenticated_principal(request)
        assert principal is not None
        return principal
    if connection.provider == "outlook":
        # Outlook is an auxiliary mailbox whose verified address may differ from
        # the Gmail identity that owns it. The persisted user_id is that binding.
        return VerifiedPrincipal(user_id=connection.user_id)
    return principal_for_connection(connection)


async def _owned_connection(request: Request, connection_id: str, detail: str) -> MailboxConnection:
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


def _outlook_settings(request: Request) -> OutlookSettings | None:
    return cast(OutlookSettings | None, request.app.state.outlook_settings)


def _session_settings(request: Request) -> SessionSettings:
    return cast(SessionSettings, request.app.state.session_settings)


def _mailbox(request: Request) -> ProviderRoutingMailboxAdapter:
    return cast(ProviderRoutingMailboxAdapter, request.app.state.mailbox)


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


def main() -> None:
    load_runtime_environment()
    # Without a root handler the stdlib drops every INFO record, which silently
    # discards the whole trace-sink stream (§13) — the observability surface is
    # log lines, so an unconfigured logger means no observability at all.
    # .upper() because basicConfig rejects "debug" with a ValueError, which would
    # kill the process at startup over a lowercase env var.
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    reload = "--reload" in sys.argv or os.getenv("APP_RELOAD", "false").strip().lower() in {
        "true",
        "1",
        "yes",
    }
    api_workers = 1 if reload else int(os.getenv("APP_API_WORKERS", "1"))
    if api_workers < 1:
        raise ValueError("APP_API_WORKERS must be positive")
    loop = "auto"
    if database_url() and sys.platform == "win32":
        # psycopg async cannot run on Windows' ProactorEventLoop. uvicorn builds
        # the loop from its own loop_factory, so setting the event loop *policy*
        # here has no effect — the loop has to be named in the config instead.
        loop = "asyncio:SelectorEventLoop"
    if sys.platform == "win32":
        configure_windows_reload()

    src_dir = Path(__file__).resolve().parent.parent
    reload_dirs = [str(src_dir)] if reload else None

    uvicorn.run(
        "cowork_agent.app:create_app",
        factory=True,
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "8000")),
        loop=loop,
        workers=api_workers,
        reload=reload,
        reload_dirs=reload_dirs,
    )


if __name__ == "__main__":
    main()
