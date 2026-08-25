"""Runnable FastAPI entry point for mailbox and Cowork workflows."""

import json
import logging
import os
import re
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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
from cowork_agent.composition import (
    ControlPlane,
    CoworkRuntime,
    build_chat,
    build_control_plane,
    build_email_rag,
    build_evaluation,
    build_mailbox,
    degrade_email_rag,
    runtime,
    upgrade_email_rag_providers,
)
from cowork_agent.config import (
    ChatIntentSettings,
    EvaluationSettings,
    GmailSettings,
    OutlookSettings,
    SessionSettings,
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
from cowork_agent.features.ai_chat.controller import ChatController
from cowork_agent.features.ai_chat.intent.observability import LoggingIntentRoutingSink
from cowork_agent.features.ai_chat.intent.service import ChatRoutingService
from cowork_agent.features.ai_chat.memory_gateway import MemoryGateway
from cowork_agent.features.ai_chat.ports import EpisodicMemoryPort
from cowork_agent.features.email_action_plan.policies import DEFAULT_QUERY
from cowork_agent.features.email_action_plan.ports import SemanticMemoryPort
from cowork_agent.features.email_action_plan.workflow import DigestWorker
from cowork_agent.identity import (
    ConnectionNotOwnedError,
    VerifiedPrincipal,
    create_guest_session,
    ensure_principal_owns_connection,
    principal_for_connection,
    principal_from_opaque_session,
)
from cowork_agent.integrations.gmail.provider import GmailConnectionService
from cowork_agent.integrations.llm.provider_factory import (
    resolve_chat_providers,
    resolve_email_providers,
)
from cowork_agent.integrations.mailbox import (
    MailboxNotConnectedError,
    MailboxPermissionDeniedError,
    MailboxReauthRequiredError,
    MailboxTemporaryError,
    ProviderRoutingMailboxAdapter,
)
from cowork_agent.integrations.outlook import OutlookConnectionService
from cowork_agent.integrations.rag.chat_memory import SemanticChatMemoryAdapter
from cowork_agent.integrations.rag.knowledge_base import KnowledgeDocument
from cowork_agent.integrations.rag.null_memory import NullSemanticMemory
from cowork_agent.persistence.report_artifacts import FileSystemReportArtifactStore
from cowork_agent.persistence.repositories.mailbox_connections import (
    SQLiteMailboxConnectionRepository,
)
from cowork_agent.runtime import (
    configure_windows_event_loop_policy,
    configure_windows_reload,
)

from .api.chat import create_chat_router
from .api.evaluation_jobs import create_evaluation_router
from .api.handlers import _jsonable
from .api.projects import create_project_router
from .api.reports import create_report_router

# ``uvicorn cowork_agent.app:create_app --factory`` bypasses ``main()``. Set
# the policy during module import as well, before Uvicorn creates its loop.
configure_windows_event_loop_policy()
configure_windows_reload()

logger = logging.getLogger(__name__)


async def _resolve_chat_principal(request: Request) -> VerifiedPrincipal:
    """Resolve chat identity from a session, with the local MVP fallback."""

    # Both identity paths read the control-plane group through the typed
    # runtime seam (ADR-013); an uncomposed group fails as loudly as the old
    # missing-key reads did.
    control_plane = runtime(request).control_plane
    if control_plane is None:
        raise RuntimeError("the control-plane group is not composed")
    if control_plane.chat_opaque_session_repository is not None:
        principal = await _authenticated_chat_principal(request)
        assert principal is not None
        return principal

    repository: SQLiteMailboxConnectionRepository = cast(
        SQLiteMailboxConnectionRepository, control_plane.connection_repository
    )
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
#: Default report folder. The store is constructed from this once in ``lifespan``
#: and injected from there; nothing downstream resolves the location again.
REPORTS_DIR = Path(__file__).resolve().parents[2] / "data" / "reports"


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


def _chat_controller_factory(
    app: FastAPI,
) -> Callable[[ChatMemoryScope], ChatController]:
    """Compose each scoped controller from the assembled typed runtime.

    One typed read of the composed ``CoworkRuntime`` per controller creation
    (ADR-013): the old version re-read a dozen untyped ``app.state`` keys and
    leaned on the placeholder-then-upgrade dance to see final values. The
    single assembly in ``lifespan`` means this read returns those same final
    values, and because the read happens at creation time — not at publish
    time — a ``dataclasses.replace`` override of the runtime stays visible.
    """

    def factory(scope: ChatMemoryScope) -> ChatController:
        composed = cast(CoworkRuntime, app.state.runtime)
        chat = composed.chat
        if chat is None:
            raise RuntimeError("the chat group is not composed")
        control_plane = composed.control_plane
        email_rag = composed.email_rag
        semantic_memory: SemanticMemoryPort = (
            email_rag.semantic_memory if email_rag is not None else NullSemanticMemory()
        )
        intent_settings = chat.chat_intent_settings
        return ChatController(
            scope=scope,
            memory=MemoryGateway(
                scope=scope,
                session_buffer=chat.chat_session_buffer,
                declarative_memory=(
                    control_plane.chat_profile_repository
                    if control_plane is not None
                    else None
                ),
                episodic_memory=cast(
                    EpisodicMemoryPort | None,
                    (
                        control_plane.chat_task_episode_repository
                        if control_plane is not None
                        else None
                    ),
                ),
                semantic_memory=SemanticChatMemoryAdapter(semantic_memory),
                project_documents=(
                    email_rag.project_document_vectors if email_rag is not None else None
                ),
                memory_operation_sink=chat.memory_operation_sink,
            ),
            reply=chat.chat_reply,
            reports=composed.reports,
            history=control_plane.chat_history_repository if control_plane is not None else None,
            routing=chat.chat_routing_service,
            company_rag_enabled=(
                intent_settings.company_rag_enabled if intent_settings is not None else True
            ),
            episode_retention_seconds=chat.chat_memory_settings.episode_retention_seconds,
        )

    return factory


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

    # The evaluation settings resolve once here and flow into ``lifespan`` as
    # an explicit closure capture (ADR-013, slice 02-6): the old code wrote
    # them onto ``app.state`` after this closure was defined and read them
    # back inside it, a cross-phase round-trip the typed assembly deletes.
    evaluation_settings = EvaluationSettings.from_env()

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

            # One report folder, resolved once. Both writers — the artifacts
            # view and the AI Chat turn that generates a report — go through
            # this store, so the filename rule cannot diverge between them.
            # Composed before any credentialed settings are read: reports need
            # no provider, so a missing Gmail key must not take them offline.
            # The store is the first field of the typed runtime (ADR-013); its
            # consumers read it through ``runtime(request).reports``.
            report_store = FileSystemReportArtifactStore(REPORTS_DIR)
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
            # Control-plane group (ADR-013, slice 02-2): the Postgres/SQLite
            # repository decision, the pool and its migrations, and the
            # run/task/result bookkeeping compose as one typed value in
            # ``composition.build_control_plane``. Every consumer reads the
            # group through ``runtime(request)`` — slice 02-8 deleted the
            # legacy ``app.state.<key>`` forwards the cutover proved dead.
            control_plane = await build_control_plane(settings, control_plane_url)
            repository = control_plane.connection_repository
            chat_session_registry = control_plane.chat_session_registry
            run_repository = control_plane.run_repository
            task_repository = control_plane.task_repository
            result_repository = control_plane.result_repository
            # Mailbox group (ADR-013, slice 02-3): the provider connection
            # and read adapters, the Outlook sqlite-only gate, the routing
            # adapter, and the private document storage compose as one typed
            # value in ``composition.build_mailbox``. The outlook settings
            # stay computed here because env validation runs before the
            # control plane; the identity repository is passed in explicitly
            # so the principal wiring moves with the group.
            mailbox_runtime = await build_mailbox(
                settings,
                outlook_settings,
                outlook_configuration_error,
                control_plane_url,
                repository,
                control_plane.identity_repository,
                UserDocumentsSettings.from_env(),
            )
            user_documents_settings = mailbox_runtime.user_documents_settings
            # Chat group (ADR-013, slice 02-4): the memory settings, session
            # buffer, observability sink, ready-document catalog, and identity
            # callables compose as one typed value in
            # ``composition.build_chat``. ``chat_reply`` /
            # ``chat_routing_service`` boot as placeholders and the LLM
            # provider block below upgrades them into the group through the
            # local upgrade sequence — no ``app.state`` round-trip.
            chat_runtime = await build_chat(
                pg_pool=control_plane.pg_pool,
                project_repository=control_plane.project_repository,
                chat_session_registry=chat_session_registry,
                user_documents_settings=user_documents_settings,
                principal_resolver=_resolve_chat_principal,
                guest_session_issuer=_issue_chat_guest_session,
            )

            # Email-RAG group (ADR-013, slice 02-5): the project-document
            # vector plane composes as one typed value in
            # ``composition.build_email_rag``, moved verbatim including its
            # swallow-and-log degrade. The provider half (semantic store,
            # corpus, digest worker) boots as placeholders here and is
            # upgraded by the LLM provider block below — the same
            # placeholder-then-upgrade sequence the chat group uses.
            email_rag_runtime = await build_email_rag(
                settings=settings,
                control_plane_url=control_plane_url,
                project_repository=control_plane.project_repository,
                pg_pool=control_plane.pg_pool,
                private_storage=mailbox_runtime.private_storage,
                user_documents_settings=user_documents_settings,
            )
            # The provider block's chat-half upgrades flow through these
            # locals into the chat group below: on success the real adapters,
            # on degrade whatever resolved before the failure — exactly the
            # contract the old ``app.state`` publication provided, minus the
            # round-trip through untyped keys.
            chat_reply = chat_runtime.chat_reply
            chat_intent_settings: ChatIntentSettings | None = None
            chat_routing_service = chat_runtime.chat_routing_service
            try:
                provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
                provider_label = {
                    "gemini": "Gemini",
                    "mistral": "Mistral",
                    "openrouter": "OpenRouter",
                    "mimo": "Mimo",
                }.get(provider, "LLM provider")
                email_providers = await resolve_email_providers(provider)
                chat_providers = resolve_chat_providers(provider)
                intent_classifier = chat_providers.intent_classifier
                intent_settings = chat_providers.intent_settings
                chat_reply = chat_providers.chat_reply
                chat_intent_settings = intent_settings
                ready_document_catalog = chat_runtime.ready_document_catalog
                chat_routing_service = (
                    ChatRoutingService(
                        classifier=intent_classifier,
                        catalog=ready_document_catalog,
                        model_id=intent_settings.model,
                        timeout_ms=intent_settings.timeout_ms,
                        max_attempts=intent_settings.max_attempts,
                        tool_axis_enabled=intent_settings.tool_axis_enabled,
                        sink=LoggingIntentRoutingSink(),
                    )
                    if (
                        intent_settings.enabled
                        and user_documents_settings.enabled
                        and ready_document_catalog is not None
                    )
                    else None
                )
                # Email half of this block (ADR-013, slice 02-5): the semantic
                # store, the corpus, and the digest worker now compose in
                # ``composition.upgrade_email_rag_providers``, called here in
                # the same statement order as before so a ``ValueError`` from
                # either half still reaches the one coupled except below.
                email_rag_runtime = upgrade_email_rag_providers(
                    email_rag_runtime,
                    email_providers=email_providers,
                    provider_label=provider_label,
                    settings=settings,
                    run_repository=run_repository,
                    result_repository=result_repository,
                    task_repository=task_repository,
                    mailbox=mailbox_runtime.mailbox,
                    outbox_repository=control_plane.outbox_repository,
                )
            except ValueError as exc:
                # The coupled degrade contract (ADR-013, slice 02-5): a
                # ``ValueError`` from either half of this block degrades the
                # email-RAG provider half; the chat half keeps whatever the
                # block resolved before the failure, exactly as before the
                # group existed. The document plane is untouched.
                email_rag_runtime = degrade_email_rag(
                    email_rag_runtime,
                    configuration_error=str(exc),
                    provider_label=provider_label,
                )
            # Chat group upgrade (ADR-013, slice 02-4): the locals above carry
            # the provider block's outcome into the typed chat group so the
            # full runtime assembled below holds the final values. On success
            # the real ``chat_reply`` / ``chat_intent_settings`` /
            # ``chat_routing_service``, on degrade whatever resolved before
            # the failure (``chat_intent_settings`` stays ``None`` unless the
            # failure came after it). The email-RAG group (slice 02-5) was
            # upgraded or degraded in the same block and joins the same
            # assembly.
            chat_runtime = replace(
                chat_runtime,
                chat_reply=chat_reply,
                chat_intent_settings=chat_intent_settings,
                chat_routing_service=chat_routing_service,
            )
            # Evaluation group (ADR-013, slice 02-6): the internal evaluation
            # control plane composes in ``composition.build_evaluation``,
            # moved verbatim including the close-before-re-raise recovery
            # contract. The settings arrive as an explicit capture from
            # ``create_app`` — no ``app.state`` round-trip — and the group
            # composes only when they are present and enabled, exactly the
            # old gate.
            evaluation_bundle = await build_evaluation(evaluation_settings)
            # Single assembly point (ADR-013): every group is built, so the
            # full ``CoworkRuntime`` publishes here once. Slice 02-8 deleted
            # the legacy ``app.state.<key>`` forwards the cutover proved
            # dead; the survivors beside this assignment are the documented
            # exceptions in ADR-013.
            app.state.runtime = CoworkRuntime(
                reports=report_store,
                control_plane=control_plane,
                mailbox=mailbox_runtime,
                chat=chat_runtime,
                email_rag=email_rag_runtime,
                evaluation=evaluation_bundle,
            )
            # The controller factory reads the composed runtime at controller
            # creation time (ADR-013, slice 02-7): publish it after the single
            # assembly, never before the upgrade sequence completes. It stays
            # on ``app.state`` because the chat router's request-time cache
            # reads it there (ADR-013's documented survivor).
            app.state.chat_controller_factory = _chat_controller_factory(app)
        except ValueError as exc:
            raise RuntimeError(f"Invalid application configuration: {exc}") from exc
        yield
        # Teardown reads its handles back from the assembled typed runtime —
        # the forwards it used to read are gone (ADR-013, slice 02-8). The
        # order is the old order exactly: evaluation first, then the
        # control-plane pool, then the storage client.
        state_runtime = cast(
            CoworkRuntime | None, getattr(app.state, "runtime", None)
        )
        if state_runtime is not None:
            evaluation = state_runtime.evaluation
            if evaluation is not None:
                await evaluation.runtime.close()
            teardown_control_plane = state_runtime.control_plane
            # The project index holds no network handle of its own: it reads
            # local .tvim files and borrows private_storage, closed below.
            if teardown_control_plane is not None and teardown_control_plane.pg_pool is not None:
                await teardown_control_plane.pg_pool.close()
            teardown_mailbox = state_runtime.mailbox
            if (
                teardown_mailbox is not None
                and teardown_mailbox.private_storage_client is not None
            ):
                await teardown_mailbox.private_storage_client.aclose()

    app = FastAPI(title="Module Mail", version="0.1.0", lifespan=lifespan)
    app.include_router(create_chat_router())
    app.include_router(create_project_router())
    app.include_router(create_report_router())
    # The evaluation API is internal-only and disabled by default; its routes
    # are mounted exclusively when explicitly enabled with a bearer token.
    if evaluation_settings.enabled:
        app.include_router(create_evaluation_router())

    @app.get("/health")
    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/cowork/chat/document-health", response_model=None)
    async def document_health(request: Request) -> JSONResponse:
        # Every check reads one composed group through the runtime seam
        # (ADR-013); an absent group degrades to the disabled/unavailable
        # states the old missing-key reads produced.
        state_runtime = runtime(request)
        chat = state_runtime.chat
        mailbox = state_runtime.mailbox
        email_rag = state_runtime.email_rag
        control_plane = state_runtime.control_plane
        settings = chat.user_documents_settings if chat is not None else None
        redis_client = control_plane.redis_client if control_plane is not None else None
        checks: dict[str, str] = {
            "feature": "enabled" if settings is not None and settings.enabled else "disabled",
            "postgresql": "disabled",
            "supabase_storage": "disabled",
            "redis": "disabled",
            "redis_mode": "redis" if redis_client is not None else "local",
            "project_index": "disabled",
            "gemini_embeddings": "disabled",
            "ocr": "optional_unavailable",
            "classifier": "disabled",
            "worker_queue": "unavailable",
        }
        if settings is None or not settings.enabled:
            return JSONResponse({"status": "disabled", "checks": checks})
        if email_rag is not None and email_rag.document_embeddings_configured:
            checks["gemini_embeddings"] = "configured"
        checks["classifier"] = (
            "ready"
            if chat is not None and chat.chat_routing_service is not None
            else "unavailable"
        )
        pool = control_plane.pg_pool if control_plane is not None else None
        if pool is not None:
            try:
                async with pool.connection() as connection:
                    await connection.execute("SELECT 1")
                checks["postgresql"] = "ready"
            except Exception:
                checks["postgresql"] = "unavailable"
        checks["supabase_storage"] = (
            "configured"
            if mailbox is not None and mailbox.private_storage is not None
            else "unavailable"
        )
        if redis_client is not None:
            try:
                await redis_client.ping()  # type: ignore[attr-defined]
                checks["redis"] = "ready"
            except Exception:
                checks["redis"] = "unavailable"
        else:
            checks["redis"] = "local_fallback"
        index_store = email_rag.project_document_index if email_rag is not None else None
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
        project_repository = (
            control_plane.project_repository if control_plane is not None else None
        )
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

    @app.get("/v1/mail-todo/connections")
    async def list_connections(request: Request) -> dict[str, Any]:
        control_plane = _control_plane_required(request)
        repository = control_plane.connection_repository
        principal = await _authenticated_principal(
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

    @app.delete("/v1/mail-todo/connections/{connection_id}")
    async def disconnect_mailbox(connection_id: str, request: Request) -> dict[str, bool]:
        connection = await _owned_connection(request, connection_id, "Mailbox connection not found")
        principal = await _connection_principal(request, connection)
        repository = _control_plane_required(request).connection_repository
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
        repository = _control_plane_required(request).run_repository
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
            email_rag = runtime(request).email_rag
            label = email_rag.llm_provider_label if email_rag is not None else "the LLM provider"
            error = (
                email_rag.llm_configuration_error
                if email_rag is not None
                else "the email-RAG group is not composed"
            )
            raise HTTPException(
                status_code=503,
                detail=f"{label} is not configured: {error}",
            )
        creator = _control_plane_required(request).create_run
        run = await creator.execute(
            user_id=principal.user_id,
            mailbox_connection_id=payload.mailbox_connection_id,
            idempotency_key=idempotency_key,
            query=payload.query,
            max_emails=payload.max_emails,
        )
        control_plane = runtime(request).control_plane
        run_queue = control_plane.run_queue if control_plane is not None else None
        if run_queue is not None:
            await cast(Any, run_queue).enqueue_digest_run(run.id, user_id=principal.user_id)
        else:
            background_tasks.add_task(worker.execute, run.id)
        return {
            "id": run.id,
            "status": run.status.value,
            "statusUrl": f"/v1/mail-todo/runs/{run.id}",
        }

    @app.get("/v1/mail-todo/runs/{run_id}")
    async def get_digest_run(run_id: str, request: Request) -> dict[str, Any]:
        repository = _control_plane_required(request).run_repository
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
            results = _control_plane_required(request).result_repository
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
        control_plane = _control_plane_required(request)
        repository = control_plane.run_repository
        run = await repository.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Digest run not found")
        await _ensure_run_connection_owned(request, run, detail="Digest run not found")
        if run.status.value in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="RUN_NOT_COMPLETE")
        result_service = control_plane.get_result
        payload = cast(dict[str, Any], _jsonable(await result_service.execute(run_id)))
        if not _is_development():
            payload.pop("processedEmails", None)
        return payload

    @app.get("/v1/mail-todo/runs/{run_id}/tasks")
    async def get_digest_tasks(run_id: str, request: Request) -> dict[str, Any]:
        """Persisted §6.6 Tasks for presentation (T4.3): citations, missing
        information, and confidences that the legacy result shape drops."""
        control_plane = _control_plane_required(request)
        repository = control_plane.run_repository
        run = await repository.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Digest run not found")
        await _ensure_run_connection_owned(request, run, detail="Digest run not found")
        if run.status.value in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="RUN_NOT_COMPLETE")
        task_repository = control_plane.task_repository
        records = await task_repository.list_for_run(run_id)
        return {"tasks": [record.task.to_dict() for record in records]}

    # ── Knowledge endpoints (V1-M3): corpus inspection + ad-hoc retrieval ──

    @app.get("/v1/mail-todo/knowledge/ready")
    async def knowledge_ready(request: Request) -> dict[str, Any]:
        email_rag = runtime(request).email_rag
        documents: tuple[KnowledgeDocument, ...] = (
            email_rag.knowledge_documents if email_rag is not None else ()
        )
        memory: SemanticMemoryPort = (
            email_rag.semantic_memory if email_rag is not None else NullSemanticMemory()
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
        email_rag = runtime(request).email_rag
        documents: tuple[KnowledgeDocument, ...] = (
            email_rag.knowledge_documents if email_rag is not None else ()
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
        email_rag = runtime(request).email_rag
        memory: SemanticMemoryPort = (
            email_rag.semantic_memory if email_rag is not None else NullSemanticMemory()
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
                k: str(v["output"]) for k, v in raw.items() if isinstance(v, dict) and "output" in v
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
            filename=safe_name,
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
    async def put_raw_document(filename: str, request: Request) -> dict[str, Any]:
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
    async def delete_raw_document(filename: str, request: Request) -> dict[str, Any]:
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
    # Read through the typed control-plane seam when it is composed; the
    # ``app.state`` memo remains the fallback (and the self-heal write-back
    # target) for the no-lifespan test path, where the runtime is never
    # assembled. This memo is a documented ``app.state`` survivor (ADR-013):
    # the frozen runtime cannot absorb a lazily constructed repository.
    app_runtime = getattr(request.app.state, "runtime", None)
    control_plane = (
        cast(CoworkRuntime, app_runtime).control_plane if app_runtime is not None else None
    )
    repo = control_plane.raw_document_repository if control_plane is not None else None
    if repo is None:
        repo = getattr(request.app.state, "raw_document_repository", None)
    if repo is None:
        from cowork_agent.persistence.repositories.sqlite_raw_documents import (
            SQLiteRawDocumentRepository,
        )

        # The no-lifespan path only: without a composed control plane there
        # is no startup location to mirror, so the memo heals under the
        # process working directory's ``data`` root.
        repo = SQLiteRawDocumentRepository(Path.cwd() / "data" / "raw_documents.db")
        # Without this the table is never created and every query raises
        # "no such table: raw_document_metadata".
        await repo.initialize()
        request.app.state.raw_document_repository = repo
    return repo


def _control_plane_required(request: Request) -> ControlPlane:
    """The control-plane group, or the loud failure its old direct reads had."""
    control_plane = runtime(request).control_plane
    if control_plane is None:
        raise RuntimeError("the control-plane group is not composed")
    return control_plane


def _connection_service(request: Request) -> GmailConnectionService:
    mailbox_group = runtime(request).mailbox
    if mailbox_group is None:
        raise RuntimeError("the mailbox group is not composed")
    return mailbox_group.gmail_connections


def _outlook_connection_service(request: Request) -> OutlookConnectionService | None:
    mailbox_group = runtime(request).mailbox
    return mailbox_group.outlook_connections if mailbox_group is not None else None


async def _authenticated_principal(
    request: Request, *, required: bool = True
) -> VerifiedPrincipal | None:
    """Resolve the opaque session only in the PostgreSQL multi-user runtime."""
    control_plane = runtime(request).control_plane
    sessions = control_plane.session_repository if control_plane is not None else None
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
    control_plane = runtime(request).control_plane
    sessions = (
        (control_plane.chat_opaque_session_repository or control_plane.session_repository)
        if control_plane is not None
        else None
    )
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
    control_plane = runtime(request).control_plane
    if control_plane is not None and control_plane.session_repository is not None:
        principal = await _authenticated_principal(request)
        assert principal is not None
        return principal
    if connection.provider == "outlook":
        # Outlook is an auxiliary mailbox whose verified address may differ from
        # the Gmail identity that owns it. The persisted user_id is that binding.
        return VerifiedPrincipal(user_id=connection.user_id)
    return principal_for_connection(connection)


async def _owned_connection(request: Request, connection_id: str, detail: str) -> MailboxConnection:
    repository = _control_plane_required(request).connection_repository
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


def _session_settings(request: Request) -> SessionSettings:
    return _control_plane_required(request).session_settings


def _mailbox(request: Request) -> ProviderRoutingMailboxAdapter:
    mailbox_group = runtime(request).mailbox
    if mailbox_group is None:
        raise RuntimeError("the mailbox group is not composed")
    return mailbox_group.mailbox


def _digest_worker(request: Request) -> DigestWorker | None:
    email_rag = runtime(request).email_rag
    return email_rag.digest_worker if email_rag is not None else None


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
