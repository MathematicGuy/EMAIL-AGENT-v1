"""Runnable FastAPI entry point for mailbox and Cowork workflows."""

import logging
import os
import sys
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

import uvicorn
from fastapi import (
    FastAPI,
    HTTPException,
    Request,
)

import cowork_agent.integrations.llm.langfuse_bootstrap as _langfuse_bootstrap  # noqa: F401
from cowork_agent.composition import (
    CalendarRuntime,
    CoworkRuntime,
    build_calendar,
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
    UserDocumentsSettings,
    database_url,
    load_runtime_environment,
)
from cowork_agent.domain.chat_contracts import ChatMemoryScope
from cowork_agent.features.ai_chat.controller import ChatController
from cowork_agent.features.ai_chat.intent.observability import LoggingIntentRoutingSink
from cowork_agent.features.ai_chat.intent.service import ChatRoutingService
from cowork_agent.features.ai_chat.memory_gateway import MemoryGateway
from cowork_agent.features.ai_chat.ports import EpisodicMemoryPort
from cowork_agent.features.ai_chat.tools import (
    AGENDA_TOOL_DESCRIPTION,
    AGENDA_TOOL_NAME,
    AGENDA_TOOL_SCHEMA,
    CALENDAR_TOOL_DESCRIPTION,
    CALENDAR_TOOL_NAME,
    CALENDAR_TOOL_SCHEMA,
    Tool,
    ToolResult,
    build_agenda_tool,
    build_calendar_tool,
)
from cowork_agent.features.ai_chat.tools.runner import ChatToolRunner, ToolTurnContext
from cowork_agent.features.email_action_plan.ports import SemanticMemoryPort
from cowork_agent.identity import (
    VerifiedPrincipal,
    principal_for_connection,
)
from cowork_agent.integrations.google_calendar import (
    GoogleCalendar,
    GoogleCalendarSettings,
    calendar_settings_for,
)
from cowork_agent.integrations.llm.provider_factory import (
    ChatProviderBundle,
    resolve_chat_providers,
    resolve_email_providers,
)
from cowork_agent.integrations.rag.chat_memory import SemanticChatMemoryAdapter
from cowork_agent.integrations.rag.null_memory import NullSemanticMemory
from cowork_agent.integrations.report_pdf import Fpdf2ReportPdfRenderer
from cowork_agent.persistence.report_artifacts import FileSystemReportArtifactStore
from cowork_agent.persistence.repositories.mailbox_connections import (
    SQLiteMailboxConnectionRepository,
)
from cowork_agent.runtime import (
    configure_windows_event_loop_policy,
    configure_windows_reload,
)

from .api.calendars import create_calendar_router
from .api.chat import create_chat_router
from .api.dependencies import (
    authenticated_chat_principal,
    issue_chat_guest_session,
)
from .api.digest_runs import create_digest_router
from .api.evaluation_jobs import create_evaluation_router
from .api.knowledge import create_knowledge_router
from .api.mailboxes import create_mailbox_router
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
        principal = await authenticated_chat_principal(request)
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


#: Default report folder. The store is constructed from this once in ``lifespan``
#: and injected from there; nothing downstream resolves the location again.
REPORTS_DIR = Path(__file__).resolve().parents[2] / "data" / "reports"


#: Shown when the turn belongs to a user with no calendar grant. A refusal the
#: user can act on beats a successful write to somebody else's calendar, which
#: is what any fallback credential would produce (ADR-019 §2).
CALENDAR_NOT_CONNECTED = (
    "Your Google Calendar is not connected yet, so I could not create the event. "
    "Connect it from the dashboard and ask me again."
)
#: The same refusal for a turn that only wanted to read. Saying "I could not
#: create the event" to someone who asked what was on Friday is a wrong answer
#: about what the system tried to do.
CALENDAR_NOT_CONNECTED_READ = (
    "Your Google Calendar is not connected yet, so I could not look anything up. "
    "Connect it from the dashboard and ask me again."
)


def _not_connected_tool(
    name: str,
    description: str,
    parameters: Mapping[str, object],
    message: str,
) -> Tool:
    """A tool that refuses instead of reaching the calendar.

    Returned rather than binding no tool at all, so the turn degrades into a
    reply that says what did not happen -- the same shape every other tool
    failure takes -- instead of the router and the runner disagreeing about
    whether the tool exists.

    Parameterized once the second tool arrived. It had been written around the
    one tool that existed, so a `list_calendar_events` turn with no grant would
    have been refused under the writing tool's name and schema (PROGRESS.md F9).
    """

    async def handler(arguments: Mapping[str, object]) -> ToolResult:
        del arguments
        return ToolResult(ok=False, text=message)

    return Tool(name=name, description=description, parameters=parameters, handler=handler)


def _calendar_not_connected() -> Tool:
    return _not_connected_tool(
        CALENDAR_TOOL_NAME,
        CALENDAR_TOOL_DESCRIPTION,
        CALENDAR_TOOL_SCHEMA,
        CALENDAR_NOT_CONNECTED,
    )


def _agenda_not_connected() -> Tool:
    return _not_connected_tool(
        AGENDA_TOOL_NAME,
        AGENDA_TOOL_DESCRIPTION,
        AGENDA_TOOL_SCHEMA,
        CALENDAR_NOT_CONNECTED_READ,
    )


def _calendar_classifier_tools(
    settings: GoogleCalendarSettings | None,
) -> tuple[Tool, ...]:
    """Tool descriptions the intent classifier may select at this boot.

    The handler is never dispatched from this tuple; `ChatToolRunner` binds a
    fresh tool to the real turn. Keeping the same `Tool` value for both paths
    makes the classifier name, description, and schema impossible to drift
    from the executable definition.

    The `GoogleCalendar` built here from environment settings is inert: only
    the name, description, and schema are read off the value, and the handler
    is dropped. ADR-019's per-user rule governs dispatch, which happens in
    `_chat_tool_runner`, and is not weakened by a description built once.
    """

    if settings is None or not settings.enabled:
        return ()
    timezone = ZoneInfo(settings.timezone)
    calendar = GoogleCalendar(settings)
    return (
        build_calendar_tool(
            calendar,
            idempotency_key="classifier-tool-spec",
            timezone=settings.timezone,
            now=datetime.now(timezone),
            # No turn, so no message and nothing for the ambiguous-hour guard
            # to read. Harmless here: this value's handler is never called.
            user_message="",
        ),
        build_agenda_tool(calendar, timezone=settings.timezone),
    )


def _chat_tool_runner(
    chat_providers: ChatProviderBundle,
    settings: GoogleCalendarSettings | None,
    calendar_plane: CalendarRuntime | None = None,
) -> ChatToolRunner | None:
    """Compose the calendar tool, or ``None`` when unconfigured or not enabled.

    Both gates matter: absent credentials is the usual case, and
    ``GOOGLE_CALENDAR_ENABLED`` is what keeps a developer's working
    credentials from turning the tool on everywhere else. The settings arrive
    as an explicit capture resolved once in ``create_app`` (ADR-013) rather
    than a per-turn ``from_env()`` re-read of the process environment.

    The credential does *not* arrive that way, and cannot: ADR-019 requires the
    grant to belong to the turn's own user, so the binder resolves it per turn
    from ``calendar_plane``. ``settings`` keeps only what is genuinely
    process-wide -- the flag, and the local-development fallback token used when
    there is no principal at all.
    """

    if settings is None or not settings.enabled:
        return None

    async def resolve(context: ToolTurnContext) -> GoogleCalendarSettings | None:
        """The grant this turn may use, or None when there is none.

        Extracted when the second tool arrived: both binders need the same
        answer, and the two must never disagree about whose calendar a turn
        touches. Returning `None` rather than raising keeps each binder free to
        pick its own refusal, which reads differently for a read and a write.
        """

        if context.user_id and calendar_plane is not None:
            connection = await calendar_plane.repository.get_for_user(context.user_id)
            if connection is None:
                # No silent fallback to `settings`. A signed-in user without a
                # grant is told so; substituting the environment token here is
                # how one person's event lands on another person's calendar.
                return None
            return calendar_settings_for(
                connection, calendar_plane.oauth_settings, calendar_plane.cipher
            )
        if context.user_id and calendar_plane is None:
            return None
        return settings

    async def bind_create(context: ToolTurnContext) -> Tool:
        resolved = await resolve(context)
        if resolved is None:
            return _calendar_not_connected()
        return build_calendar_tool(
            GoogleCalendar(resolved),
            idempotency_key=context.idempotency_key,
            timezone=resolved.timezone,
            now=context.now.astimezone(ZoneInfo(resolved.timezone)),
            user_message=context.user_message,
        )

    async def bind_agenda(context: ToolTurnContext) -> Tool:
        resolved = await resolve(context)
        if resolved is None:
            return _agenda_not_connected()
        # Reads nothing else off the context. The agenda tool is bound to a
        # grant, not to a turn -- see `build_agenda_tool`.
        return build_agenda_tool(GoogleCalendar(resolved), timezone=resolved.timezone)

    return ChatToolRunner(
        {CALENDAR_TOOL_NAME: bind_create, AGENDA_TOOL_NAME: bind_agenda},
        complete=chat_providers.tool_arguments,
    )


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
            tools=chat.chat_tool_runner,
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
    # Same explicit-capture rule for the calendar tool: one ``.env`` read at
    # startup, carried into ``lifespan`` as a closure capture instead of a
    # per-turn re-read of the process environment.
    calendar_settings = GoogleCalendarSettings.from_env()

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
            report_pdf_renderer = Fpdf2ReportPdfRenderer()
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
            # Calendar group (SPEC-per-user-google-calendar-oauth P4/P5): the
            # per-user grant plane. Composed right after the mailbox because it
            # shares that group's cipher and OAuth state secret, and because the
            # Gmail callback chains into its connect route.
            calendar_runtime = await build_calendar(settings, control_plane.pg_pool)
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
                guest_session_issuer=issue_chat_guest_session,
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
            chat_tool_runner = chat_runtime.chat_tool_runner
            try:
                provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
                provider_label = {
                    "gemini": "Gemini",
                    "mistral": "Mistral",
                    "openrouter": "OpenRouter",
                    "mimo": "Mimo",
                }.get(provider, "LLM provider")
                email_providers = await resolve_email_providers(provider)
                chat_providers = resolve_chat_providers(
                    provider, tools=_calendar_classifier_tools(calendar_settings)
                )
                intent_classifier = chat_providers.intent_classifier
                intent_settings = chat_providers.intent_settings
                chat_reply = chat_providers.chat_reply
                chat_intent_settings = intent_settings
                ready_document_catalog = chat_runtime.ready_document_catalog
                chat_tool_runner = _chat_tool_runner(
                    chat_providers, calendar_settings, calendar_runtime
                )
                chat_routing_service = (
                    ChatRoutingService(
                        classifier=intent_classifier,
                        catalog=ready_document_catalog,
                        model_id=intent_settings.model,
                        timeout_ms=intent_settings.timeout_ms,
                        max_attempts=intent_settings.max_attempts,
                        tool_axis_enabled=intent_settings.tool_axis_enabled,
                        available_tools=(
                            chat_tool_runner.names if chat_tool_runner is not None else ()
                        ),
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
                chat_tool_runner=chat_tool_runner,
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
                report_pdf_renderer=report_pdf_renderer,
                control_plane=control_plane,
                mailbox=mailbox_runtime,
                chat=chat_runtime,
                email_rag=email_rag_runtime,
                evaluation=evaluation_bundle,
                calendar=calendar_runtime,
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
    app.include_router(create_knowledge_router())
    app.include_router(create_digest_router())
    app.include_router(create_mailbox_router())
    app.include_router(create_calendar_router())
    # The evaluation API is internal-only and disabled by default; its routes
    # are mounted exclusively when explicitly enabled with a bearer token.
    if evaluation_settings.enabled:
        app.include_router(create_evaluation_router())

    @app.get("/health")
    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


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
