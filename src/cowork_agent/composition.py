"""Composition as a typed value (ADR-013).

Everything the application is made of is constructed inside the ``lifespan``
closure in ``app.py`` and was published as ~60 untyped ``app.state`` attributes,
so every consumer defended with ``getattr(request.app.state, "x", None)`` plus a
``cast``. This module is the seam that replaces that publication model:
``CoworkRuntime`` is a frozen dataclass whose fields *are* the composed value,
published once as ``app.state.runtime`` and read back through ``runtime(request)``.

The accessor is a plain function, deliberately not a FastAPI ``Depends``: the
SSE chat path must pay zero dependency-injection overhead per request, and a
plain call keeps the depth here — one module owns the whole
``app.state.runtime`` contract.

Slice 02-1 wired the report store, the module that first needed a composition
root. Slice 02-2 adds ``ControlPlane``, the group that owns the durable
control-plane construction: the Postgres-vs-SQLite repository decision, the
pool and its migrations, and the digest run/task/result bookkeeping.
Slice 02-3 adds ``MailboxRuntime``, the group that owns the provider mailbox
construction: the shared cipher, the Gmail and Outlook connection/mailbox
adapters with the Outlook sqlite-only gate, the routing adapter, and the
private document storage. Slice 02-4 adds ``ChatRuntime``, the group that owns
the chat construction: the memory settings, session registry and buffer, the
memory-observability sink, the ready-document catalog, the identity callables,
and the reply/intent/routing adapter slots that the LLM provider block
upgrades after boot. The ``chat_controller_factory`` reads the assembled
runtime at controller creation time (slice 02-7), so it never re-reads
``app.state`` — the frozen group never observes a mid-flight swap.
Slice 02-5 adds ``EmailRagRuntime``, the group that owns the email-RAG
construction: the project-document vector plane, the committed corpus, the
semantic store, and the digest worker. The group boots with its document plane
complete and provider-half placeholders; the LLM provider block in ``lifespan``
upgrades the provider half after it resolves, exactly like the chat group, and
its ``except ValueError`` degrade path degrades both halves through one coupled
contract. Slice 02-6 adds ``EvaluationBundle``, the group that owns the
internal evaluation control plane: the durable evaluation runtime, its job
service and supervisor, and the bearer token that gates the internal-only
routes. It is the only group absent on a normal boot — it composes only when
the evaluation settings are present and enabled — and with it every group
exists, so ``lifespan`` assembles the full ``CoworkRuntime`` at one point.
Slice 02-8 finishes the strangler removal: every legacy ``app.state.<key>``
forward that the cutover proved dead is deleted, so ``lifespan`` publishes
exactly the typed runtime plus the documented survivors named in ADR-013,
and teardown reads its handles back from the assembled value. The deletion
test ran for each forward: a forward disappears only when its last reader
moves behind ``runtime(request)``, and an always-``None`` key that nothing
reads carries no meaning, so deletion concentrates rather than relocates.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, cast

import httpx
from starlette.requests import Request
from starlette.responses import Response

from cowork_agent.config import (
    ChatIntentSettings,
    ChatMemorySettings,
    EmailRagQualitySettings,
    EvaluationSettings,
    GmailSettings,
    OutlookSettings,
    SessionSettings,
    SupabaseStorageSettings,
    UserDocumentsSettings,
)
from cowork_agent.domain.report_artifacts import ReportArtifactStore
from cowork_agent.features.ai_chat.controller import (
    ChatSessionRegistryPort,
    UnavailableChatReply,
)
from cowork_agent.features.ai_chat.intent.service import (
    CanonicalReadyDocumentCatalog,
    ChatRoutingService,
)
from cowork_agent.features.ai_chat.memory_observability import (
    LoggingMemoryOperationSink,
    MemoryOperationMetrics,
)
from cowork_agent.features.ai_chat.ports import ChatReplyPort
from cowork_agent.features.ai_chat.session_buffer import InMemoryChatSessionBuffer
from cowork_agent.features.ai_chat.tools.runner import ChatToolRunner
from cowork_agent.features.batch_evaluation.bootstrap import (
    EvaluationRuntime,
    build_evaluation_runtime,
)
from cowork_agent.features.batch_evaluation.service import EvaluationJobService
from cowork_agent.features.batch_evaluation.supervisor import EvaluationSupervisor
from cowork_agent.features.email_action_plan.observability import (
    LoggingTraceSink,
    dev_trace_sink_from_env,
)
from cowork_agent.features.email_action_plan.ports import (
    MailboxConnectionRepository,
    MailboxPort,
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
from cowork_agent.identity import LOCAL_TENANT_ID, PrincipalRepository, VerifiedPrincipal
from cowork_agent.integrations.gmail.auth import OAuthStateManager, TokenCipher
from cowork_agent.integrations.gmail.fakes import SafeTextAttachmentExtractor
from cowork_agent.integrations.gmail.provider import (
    GmailConnectionService,
    GmailMailboxAdapter,
    GmailPrincipalResolver,
)
from cowork_agent.integrations.llm.provider_factory import EmailProviderBundle
from cowork_agent.integrations.mailbox.router import ProviderRoutingMailboxAdapter
from cowork_agent.integrations.outlook import (
    OutlookConnectionService,
    OutlookMailboxAdapter,
)
from cowork_agent.integrations.rag.bootstrap import RAG_CORPUS_PATH, build_document_embedder
from cowork_agent.integrations.rag.knowledge_base import KnowledgeDocument, load_corpus
from cowork_agent.integrations.rag.null_memory import NullSemanticMemory
from cowork_agent.integrations.rag.project_documents import (
    CanonicalProjectDocumentRetriever,
    HybridProjectDocumentStore,
)
from cowork_agent.integrations.rag.project_index import (
    SnapshotStorage,
    TurbovecProjectIndexStore,
)
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
from cowork_agent.persistence.repositories.sqlite_chat import SQLiteChatRepository
from cowork_agent.persistence.repositories.sqlite_chat_identity import (
    SQLiteChatIdentityRepository,
)
from cowork_agent.persistence.repositories.sqlite_projects import (
    SQLiteProjectRepository,
)
from cowork_agent.persistence.repositories.sqlite_raw_documents import (
    SQLiteRawDocumentRepository,
)
from cowork_agent.persistence.repositories.tasks import SQLiteTaskRepository

if TYPE_CHECKING:
    # Postgres-only types stay out of the runtime import graph: the app must
    # boot without the optional postgres extra, exactly as it did when these
    # imports lived inside the ``lifespan`` branch in ``app.py``.
    from psycopg_pool import AsyncConnectionPool

    from cowork_agent.integrations.storage.local import LocalPrivateStorage
    from cowork_agent.persistence.repositories.chat_history import (
        PostgresChatHistoryRepository,
    )
    from cowork_agent.persistence.repositories.identity import (
        PostgresIdentityRepository,
        PostgresSessionRepository,
    )
    from cowork_agent.persistence.repositories.postgres import (
        PostgresChatProfileRepository,
        PostgresOutboxRepository,
        PostgresTaskEpisodeRepository,
    )
    from cowork_agent.persistence.repositories.projects import (
        PostgresProjectRepository,
    )


@dataclass(frozen=True, slots=True)
class ControlPlane:
    """The durable control-plane group: repositories, pool, and run bookkeeping.

    Every field is what ``lifespan`` used to construct inline and publish as an
    untyped ``app.state`` key; the ``| None`` fields are capabilities that are
    genuinely absent in one boot mode (``pg_pool`` and the identity/session
    repositories do not exist on the SQLite path). ``redis_client`` and
    ``run_queue`` are always ``None`` in this process — the worker owns the
    queue — yet they stay fields because the document-health and digest-run
    routes read them through the group to choose their degrade behavior.
    Field types name the real
    adapters, not ``Any``, so a missing dependency is a mypy error here at the
    composition root instead of a ``None`` found at request time.
    """

    session_settings: SessionSettings
    connection_repository: MailboxConnectionRepository
    pg_pool: AsyncConnectionPool | None
    identity_repository: PostgresIdentityRepository | None
    session_repository: PostgresSessionRepository | None
    chat_identity_repository: (
        PostgresIdentityRepository | SQLiteChatIdentityRepository | None
    )
    chat_opaque_session_repository: (
        PostgresSessionRepository | SQLiteChatIdentityRepository | None
    )
    outbox_repository: PostgresOutboxRepository | InMemoryOutbox
    chat_profile_repository: PostgresChatProfileRepository | SQLiteChatRepository
    chat_task_episode_repository: PostgresTaskEpisodeRepository | SQLiteChatRepository
    project_repository: PostgresProjectRepository | SQLiteProjectRepository
    chat_history_repository: PostgresChatHistoryRepository | SQLiteChatRepository
    chat_session_repository: ChatSessionRegistryPort
    chat_session_registry: ChatSessionRegistryPort
    run_repository: RunRepository
    task_repository: TaskRepository
    result_repository: InMemoryResultRepository
    create_run: CreateDigestRun
    get_result: GetDigestResult
    redis_client: object | None
    run_queue: object | None
    raw_document_repository: SQLiteRawDocumentRepository


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MailboxRuntime:
    """The mailbox group: provider connections, read adapters, routing, storage.

    Every field is what ``lifespan`` used to construct inline and publish as an
    untyped ``app.state`` key. ``gmail_settings`` joins the group in slice
    02-8: it seeds the whole construction (control plane included) and the
    mailbox group is the one place every Gmail request path already reads,
    so housing it here let the last settings forward die. The
    ``| None`` fields are capabilities that are
    genuinely absent in one boot mode: the Outlook adapters exist only on the
    SQLite control plane with valid Microsoft OAuth configuration, and the
    Supabase storage client exists only when Postgres plus Storage is
    configured. ``private_storage`` itself can be ``None`` when Supabase
    Storage validation fails — the degrade path the old inline code had.
    """

    gmail_settings: GmailSettings
    gmail_connections: GmailConnectionService
    gmail_mailbox: GmailMailboxAdapter
    outlook_connections: OutlookConnectionService | None
    outlook_mailbox: OutlookMailboxAdapter | None
    outlook_settings: OutlookSettings | None
    outlook_configuration_error: str | None
    provider_availability: dict[str, dict[str, bool | str | None]]
    mailbox: ProviderRoutingMailboxAdapter
    user_documents_settings: UserDocumentsSettings
    private_storage_client: httpx.AsyncClient | None
    private_storage: SupabasePrivateStorage | LocalPrivateStorage | None


def create_chat_session_buffer(
    settings: ChatMemorySettings, *, durable: bool
) -> InMemoryChatSessionBuffer:
    """Keep bounded working turns local; durability belongs to Postgres metadata."""
    del durable
    return InMemoryChatSessionBuffer(
        max_turns=settings.max_turns,
        ttl_seconds=settings.ttl_seconds,
    )


@dataclass(frozen=True, slots=True)
class ChatRuntime:
    """The chat group: memory settings, session buffer, catalog, provider slots.

    Every field is what ``lifespan`` used to construct inline and publish as an
    untyped ``app.state`` key. ``chat_reply`` boots as the
    ``UnavailableChatReply`` placeholder and ``chat_intent_settings`` /
    ``chat_routing_service`` boot as ``None``: the LLM provider block upgrades
    those three after this group is composed, and ``lifespan`` reads the
    upgraded values back into the group before assembling the full runtime.
    The controller factory reads the assembled runtime at controller-creation
    time (slice 02-7), so it always sees those final values without re-reading
    ``app.state`` — the frozen group never observes a mid-flight swap.
    ``chat_tool_runner`` boots as ``None`` for the same reason and is
    upgraded in the same ``replace``: the calendar tool needs the resolved
    chat providers to fill its arguments, so it cannot exist before the
    provider block runs. It stays ``None`` whenever the tool is unconfigured
    or its flag is off, which is every deployment until the executable-chat-
    tool ADR lands. ``ready_document_catalog``
    is ``None`` only when there is no project repository; the principal
    resolver and guest session issuer arrive as callables because they are
    bound to the HTTP adapter surface in ``app.py`` and crossing that seam the
    other way would invert the dependency direction.
    """

    chat_memory_settings: ChatMemorySettings
    chat_sessions: ChatSessionRegistryPort
    chat_session_buffer: InMemoryChatSessionBuffer
    memory_metrics: MemoryOperationMetrics
    memory_operation_sink: LoggingMemoryOperationSink
    user_documents_settings: UserDocumentsSettings
    ready_document_catalog: CanonicalReadyDocumentCatalog | None
    chat_principal_resolver: Callable[[Request], Awaitable[VerifiedPrincipal]]
    chat_guest_session_issuer: Callable[[Request, Response], Awaitable[None]]
    chat_reply: ChatReplyPort
    chat_intent_settings: ChatIntentSettings | None
    chat_routing_service: ChatRoutingService | None
    chat_tool_runner: ChatToolRunner | None


@dataclass(frozen=True, slots=True)
class EmailRagRuntime:
    """The email-RAG group: the document plane plus the digest worker.

    Every field is what ``lifespan`` used to construct inline and publish as
    an untyped ``app.state`` key. The group has two halves with different
    arrival times. The document plane — the project-document vector plane and
    its index — composes at boot inside ``build_email_rag`` with its
    swallow-and-log degrade. The provider half — the semantic store, the
    committed corpus, and the ``DigestWorker`` — boots as placeholders
    (``NullSemanticMemory``, empty corpus, no worker) and is upgraded by
    ``upgrade_email_rag_providers`` once the LLM provider block resolves,
    mirroring the chat group's placeholder-then-upgrade sequence. The
    ``| None`` fields are capabilities genuinely absent in one boot mode:
    ``digest_worker`` is ``None`` on the coupled degrade path, and the vector
    plane fields are ``None`` when user documents are disabled or their
    construction failed.
    """

    semantic_memory: SemanticMemoryPort
    knowledge_documents: tuple[KnowledgeDocument, ...]
    digest_worker: DigestWorker | None
    llm_configuration_error: str | None
    llm_provider_label: str
    document_embeddings_configured: bool
    project_document_vectors: CanonicalProjectDocumentRetriever | None
    project_document_index: TurbovecProjectIndexStore | None


@dataclass(frozen=True, slots=True)
class EvaluationBundle:
    """The evaluation group: the internal evaluation control plane.

    Every field is what ``lifespan`` used to construct inline and publish as
    an untyped ``app.state`` key: the durable ``EvaluationRuntime`` that owns
    storage, recovery, and runner lifecycle, the job service and supervisor
    lifted from it for the route surface, and the bearer token that gates the
    internal-only API (kept server-side, never in logs or responses). Unlike
    the other groups this one is genuinely absent on most boots: it composes
    only when the evaluation settings are present and enabled, so the field
    on ``CoworkRuntime`` is optional at the interface, not just for injected
    test runtimes.
    """

    runtime: EvaluationRuntime
    service: EvaluationJobService
    supervisor: EvaluationSupervisor
    api_token: str


@dataclass(frozen=True, slots=True)
class CoworkRuntime:
    """The composed value of the application: dependencies that outlive requests.

    Frozen so no code path can swap a field mid-flight, and typed so a missing
    dependency is a mypy error at the composition root instead of a ``None``
    found at request time. ``control_plane``, ``mailbox``, ``chat``, and
    ``email_rag`` are optional only for injected test runtimes that exercise a
    single group (the ASGI transport never runs ``lifespan``); a boot through
    ``lifespan`` always composes all of them. ``evaluation`` is the one group
    a real boot may legitimately omit: it exists only when the evaluation
    settings are present and enabled. Every group now exists, so ``lifespan``
    assembles the full value at one point; the cutover slices move *where* a
    consumer reads from, never *what* is composed.
    """

    reports: ReportArtifactStore
    control_plane: ControlPlane | None = None
    mailbox: MailboxRuntime | None = None
    chat: ChatRuntime | None = None
    email_rag: EmailRagRuntime | None = None
    evaluation: EvaluationBundle | None = None


def runtime(request: Request) -> CoworkRuntime:
    """The composed runtime behind a request: a typed read of ``app.state``."""
    return cast(CoworkRuntime, request.app.state.runtime)


async def build_control_plane(settings: GmailSettings, control_plane_url: str) -> ControlPlane:
    """Compose the control-plane group (ADR-013, slice 02-2).

    The body is the construction that used to live inline in ``lifespan``,
    moved verbatim — including the lazy postgres imports and the
    ``pool.open`` / ``apply_migrations`` calls, which changed only *where*
    they happen, never *what* they do. Boot order and degrade paths are
    preserved: an empty ``control_plane_url`` keeps the whole application on
    the SQLite adapters.
    """
    session_settings = SessionSettings.from_env()
    repository: MailboxConnectionRepository = SQLiteMailboxConnectionRepository(
        settings.connection_db_path
    )
    local_repository = cast(SQLiteMailboxConnectionRepository, repository)
    await local_repository.initialize()
    identity_repository: PostgresIdentityRepository | None = None
    session_repository: PostgresSessionRepository | None = None
    chat_identity_repository: (
        PostgresIdentityRepository | SQLiteChatIdentityRepository | None
    ) = None
    chat_opaque_session_repository: (
        PostgresSessionRepository | SQLiteChatIdentityRepository | None
    ) = None
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
        outbox_repository: PostgresOutboxRepository | InMemoryOutbox = (
            PostgresOutboxRepository(pool)
        )
        chat_profile_repository: PostgresChatProfileRepository | SQLiteChatRepository = (
            PostgresChatProfileRepository(pool)
        )
        chat_task_episode_repository: (
            PostgresTaskEpisodeRepository | SQLiteChatRepository
        ) = PostgresTaskEpisodeRepository(pool)
        project_repository: PostgresProjectRepository | SQLiteProjectRepository = (
            PostgresProjectRepository(pool)
        )
        chat_session_registry = PostgresChatSessionRegistry(pool)
        chat_history_repository: PostgresChatHistoryRepository | SQLiteChatRepository = (
            PostgresChatHistoryRepository(pool)
        )
        chat_session_repository: ChatSessionRegistryPort = chat_session_registry
        pg_pool: AsyncConnectionPool | None = pool
        identity_repository = PostgresIdentityRepository(pool)
        session_repository = PostgresSessionRepository(pool)
        chat_identity_repository = identity_repository
        chat_opaque_session_repository = session_repository
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
        outbox_repository = InMemoryOutbox()
        sqlite_chat_repository = SQLiteChatRepository(
            settings.connection_db_path.parent / "chat.db"
        )
        await sqlite_chat_repository.initialize()
        sqlite_chat_identity_repository = SQLiteChatIdentityRepository(
            settings.connection_db_path.parent / "chat_identity.db"
        )
        await sqlite_chat_identity_repository.initialize()
        chat_profile_repository = sqlite_chat_repository
        chat_task_episode_repository = sqlite_chat_repository
        chat_session_repository = sqlite_chat_repository
        chat_history_repository = sqlite_chat_repository
        pg_pool = None
        sqlite_project_repository = SQLiteProjectRepository(
            settings.connection_db_path.parent / "projects.db"
        )
        await sqlite_project_repository.initialize()
        project_repository = sqlite_project_repository
        chat_identity_repository = sqlite_chat_identity_repository
        chat_opaque_session_repository = sqlite_chat_identity_repository
        chat_session_registry = sqlite_chat_repository
    create_run = CreateDigestRun(run_repository)
    get_result = GetDigestResult(run_repository, result_repository, task_repository)
    raw_doc_repo = SQLiteRawDocumentRepository(
        settings.connection_db_path.parent / "raw_documents.db"
    )
    await raw_doc_repo.initialize()
    return ControlPlane(
        session_settings=session_settings,
        connection_repository=repository,
        pg_pool=pg_pool,
        identity_repository=identity_repository,
        session_repository=session_repository,
        chat_identity_repository=chat_identity_repository,
        chat_opaque_session_repository=chat_opaque_session_repository,
        outbox_repository=outbox_repository,
        chat_profile_repository=chat_profile_repository,
        chat_task_episode_repository=chat_task_episode_repository,
        project_repository=project_repository,
        chat_history_repository=chat_history_repository,
        chat_session_repository=chat_session_repository,
        chat_session_registry=chat_session_registry,
        run_repository=run_repository,
        task_repository=task_repository,
        result_repository=result_repository,
        create_run=create_run,
        get_result=get_result,
        redis_client=None,
        run_queue=None,
        raw_document_repository=raw_doc_repo,
    )


async def build_mailbox(
    settings: GmailSettings,
    outlook_settings: OutlookSettings | None,
    outlook_configuration_error: str | None,
    control_plane_url: str,
    connection_repository: MailboxConnectionRepository,
    identity_repository: PrincipalRepository | None,
    user_documents_settings: UserDocumentsSettings,
) -> MailboxRuntime:
    """Compose the mailbox group (ADR-013, slice 02-3).

    The body is the construction that used to live inline in ``lifespan``,
    moved verbatim — the shared cipher, the Gmail connection service with its
    identity-repository principal wiring, the Outlook adapters behind the
    sqlite-only gate, the routing adapter, and the private document storage.
    Only *where* it happens changed: ``outlook_settings`` and
    ``outlook_configuration_error`` arrive already computed because env
    validation ran before the control plane, and the identity repository is
    passed in explicitly because the mailbox group depends on the control
    plane. Boot order and degrade paths are preserved: Outlook stays offline
    whenever ``control_plane_url`` is set (its provider is sqlite-only), and
    a Supabase Storage validation failure degrades to no private storage
    instead of failing the boot.
    """
    mailbox_cipher = TokenCipher(settings.token_encryption_key)
    # The identity wiring the old inline code had: the bound repository method
    # is the resolver, cast to the protocol the Gmail adapter declares (same
    # value at runtime; the cast only names the seam's consumer-side type).
    principal_resolver: GmailPrincipalResolver | None = (
        cast(GmailPrincipalResolver, identity_repository.resolve_or_create_principal)
        if identity_repository is not None
        else None
    )
    gmail_connections = GmailConnectionService(
        settings,
        connection_repository,
        mailbox_cipher,
        OAuthStateManager(
            settings.oauth_state_secret,
            settings.oauth_state_ttl_seconds,
        ),
        principal_resolver=principal_resolver,
    )
    gmail_mailbox = GmailMailboxAdapter(
        settings,
        connection_repository,
        mailbox_cipher,
    )
    mailbox_adapters: dict[str, MailboxPort] = {"gmail": gmail_mailbox}
    outlook_enabled = outlook_settings is not None and not control_plane_url
    outlook_connections: OutlookConnectionService | None
    outlook_mailbox: OutlookMailboxAdapter | None
    outlook_reason: str | None
    if outlook_enabled:
        assert outlook_settings is not None
        outlook_connections = OutlookConnectionService(
            outlook_settings,
            connection_repository,
            mailbox_cipher,
            OAuthStateManager(
                outlook_settings.oauth_state_secret,
                outlook_settings.oauth_state_ttl_seconds,
            ),
        )
        outlook_mailbox = OutlookMailboxAdapter(
            outlook_settings,
            connection_repository,
            mailbox_cipher,
        )
        mailbox_adapters["outlook"] = outlook_mailbox
        outlook_reason = None
    else:
        outlook_connections = None
        outlook_mailbox = None
        outlook_reason = "sqlite_only" if outlook_settings is not None else "not_configured"
    provider_availability: dict[str, dict[str, bool | str | None]] = {
        "gmail": {"enabled": True, "reason": None},
        "outlook": {"enabled": outlook_enabled, "reason": outlook_reason},
    }
    mailbox = ProviderRoutingMailboxAdapter(connection_repository, mailbox_adapters)
    private_storage_client: httpx.AsyncClient | None
    private_storage: SupabasePrivateStorage | LocalPrivateStorage | None
    if control_plane_url and (
        user_documents_settings.enabled or os.getenv("SUPABASE_URL", "").strip()
    ):
        try:
            storage_settings = SupabaseStorageSettings.from_env()
            storage_client = httpx.AsyncClient(timeout=30.0)
            private_storage_client = storage_client
            private_storage = SupabasePrivateStorage(
                storage_settings.url,
                storage_settings.secret_key,
                storage_settings.bucket,
                storage_client,
            )
        except ValueError:
            logger.warning("Project document storage is unavailable")
            private_storage_client = None
            private_storage = None
    else:
        private_storage_client = None
        from cowork_agent.integrations.storage.local import LocalPrivateStorage

        private_storage = LocalPrivateStorage(
            settings.connection_db_path.parent / "project-documents"
        )
    return MailboxRuntime(
        gmail_settings=settings,
        gmail_connections=gmail_connections,
        gmail_mailbox=gmail_mailbox,
        outlook_connections=outlook_connections,
        outlook_mailbox=outlook_mailbox,
        outlook_settings=outlook_settings,
        outlook_configuration_error=outlook_configuration_error,
        provider_availability=provider_availability,
        mailbox=mailbox,
        user_documents_settings=user_documents_settings,
        private_storage_client=private_storage_client,
        private_storage=private_storage,
    )


async def build_chat(
    *,
    pg_pool: AsyncConnectionPool | None,
    project_repository: PostgresProjectRepository | SQLiteProjectRepository | None,
    chat_session_registry: ChatSessionRegistryPort,
    user_documents_settings: UserDocumentsSettings,
    principal_resolver: Callable[[Request], Awaitable[VerifiedPrincipal]],
    guest_session_issuer: Callable[[Request, Response], Awaitable[None]],
) -> ChatRuntime:
    """Compose the chat group (ADR-013, slice 02-4).

    The body is the construction that used to live inline in ``lifespan``,
    moved verbatim: the memory settings, the session buffer whose ``durable``
    flag derives from the control-plane pool, the memory-observability
    metrics/sink pair, the ready-document catalog built from the project
    repository, and the identity callables. Only *where* it happens changed.
    The reply/intent/routing slots are published here as the placeholder
    ``UnavailableChatReply`` and ``None`` because the real adapters arrive
    only after the LLM provider block resolves; ``lifespan`` republishes the
    group with those upgrades, preserving the placeholder-then-upgrade
    sequence the lazily-reading controller factory depends on. The provider
    dependencies arrive as explicit inputs because the chat group leans on
    the control plane (pool, repositories) and the mailbox group
    (``user_documents_settings``) without owning either.
    """
    chat_memory_settings = ChatMemorySettings.from_env()
    memory_metrics = MemoryOperationMetrics()
    return ChatRuntime(
        chat_memory_settings=chat_memory_settings,
        chat_sessions=chat_session_registry,
        chat_session_buffer=create_chat_session_buffer(
            chat_memory_settings, durable=pg_pool is not None
        ),
        memory_metrics=memory_metrics,
        memory_operation_sink=LoggingMemoryOperationSink(metrics=memory_metrics),
        user_documents_settings=user_documents_settings,
        ready_document_catalog=(
            CanonicalReadyDocumentCatalog(project_repository)
            if project_repository is not None
            else None
        ),
        chat_principal_resolver=principal_resolver,
        chat_guest_session_issuer=guest_session_issuer,
        chat_reply=UnavailableChatReply(),
        chat_intent_settings=None,
        chat_routing_service=None,
        chat_tool_runner=None,
    )


async def build_email_rag(
    *,
    settings: GmailSettings,
    control_plane_url: str,
    project_repository: PostgresProjectRepository | SQLiteProjectRepository | None,
    pg_pool: AsyncConnectionPool | None,
    private_storage: SupabasePrivateStorage | LocalPrivateStorage | None,
    user_documents_settings: UserDocumentsSettings,
) -> EmailRagRuntime:
    """Compose the email-RAG group's document plane (ADR-013, slice 02-5).

    The body is the project-document vector-plane construction that used to
    live inline in ``lifespan``, moved verbatim — the embedder resolution, the
    Postgres-vs-sqlite chunk repository decision, the ``.tvim`` index store,
    and the canonical retriever, gated on user documents being enabled with a
    project repository present and wrapped in the same swallow-and-log
    degrade. Only *where* it happens changed. The provider half cannot
    compose here: it depends on the LLM provider block, whose ``ValueError``
    degrade contract is coupled with the chat half, so it boots as
    placeholders and arrives through ``upgrade_email_rag_providers`` after
    that block resolves. The group's inputs arrive explicitly because the
    email-RAG group leans on the control plane (pool, project repository) and
    the mailbox group (private storage, user-document settings) without
    owning either.
    """
    document_embeddings_configured = False
    project_document_vectors: CanonicalProjectDocumentRetriever | None = None
    project_document_index: TurbovecProjectIndexStore | None = None
    if user_documents_settings.enabled and project_repository is not None:
        try:
            embedder, vector_size = build_document_embedder()
            document_embeddings_configured = True
            if control_plane_url:
                # The API only reads .tvim snapshots; mail-todo-worker owns
                # writing them. Both sides exchange them through Supabase
                # Storage, so a missing local file is pulled on demand. The
                # cast names the seam's consumer-side type: on this branch the
                # mailbox group only ever composes the Supabase adapter (or
                # ``None``), but its field carries the wider union.
                assert pg_pool is not None
                index_store = TurbovecProjectIndexStore(
                    user_documents_settings.index_root,
                    storage=cast(SnapshotStorage | None, private_storage),
                    vector_size=vector_size,
                )
                vector_store = HybridProjectDocumentStore(
                    PostgresProjectDocumentChunkRepository(pg_pool),
                    index_store,
                    embedder,
                    vector_size=vector_size,
                )
                project_document_index = index_store
                project_document_vectors = CanonicalProjectDocumentRetriever(
                    project_repository,
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
                project_document_index = local_index
                project_document_vectors = CanonicalProjectDocumentRetriever(
                    project_repository,
                    local_vectors,
                    top_k=user_documents_settings.top_k,
                    min_score=user_documents_settings.min_score,
                    timeout_ms=user_documents_settings.retrieval_timeout_ms,
                )
        except Exception:
            logger.exception(
                "Project document vector store is unavailable; API remains online"
            )
    return EmailRagRuntime(
        semantic_memory=NullSemanticMemory(),
        knowledge_documents=(),
        digest_worker=None,
        llm_configuration_error=None,
        llm_provider_label="LLM provider",
        document_embeddings_configured=document_embeddings_configured,
        project_document_vectors=project_document_vectors,
        project_document_index=project_document_index,
    )


def upgrade_email_rag_providers(
    email_rag: EmailRagRuntime,
    *,
    email_providers: EmailProviderBundle,
    provider_label: str,
    settings: GmailSettings,
    run_repository: RunRepository,
    result_repository: InMemoryResultRepository,
    task_repository: TaskRepository,
    mailbox: MailboxPort,
    outbox_repository: PostgresOutboxRepository | InMemoryOutbox,
) -> EmailRagRuntime:
    """Upgrade the group's provider half once the LLM block resolves.

    The body is the email half of the old mixed LLM provider block in
    ``lifespan``, moved verbatim and in the same statement order: the semantic
    store from the resolved bundle, the committed-corpus load with its
    swallow-to-empty fallback, and the ``DigestWorker`` construct-only. The
    call sits inside ``lifespan``'s provider try-block, so a ``ValueError``
    here (for example an invalid generation concurrency from the provider or
    the settings) propagates to the shared except and degrades both halves
    through the coupled contract — the same behavior as the old single block.
    """
    semantic_memory = email_providers.semantic_memory
    try:
        knowledge_documents = load_corpus(RAG_CORPUS_PATH, tenant_id=LOCAL_TENANT_ID)
    except Exception:
        knowledge_documents = ()
    digest_worker = DigestWorker(
        run_repository,
        result_repository,
        mailbox,
        SafeTextAttachmentExtractor(),
        email_providers.classifier,
        email_providers.generator,
        ShortTermStore(),
        task_repository,
        semantic_memory=semantic_memory,
        query_rewriter=email_providers.query_rewriter,
        quality_settings=EmailRagQualitySettings.from_env(),
        trace_sink=LoggingTraceSink(),
        dev_trace=dev_trace_sink_from_env(
            settings.connection_db_path.parent, settings.token_encryption_key
        ),
        completion_outbox=outbox_repository,
        mailbox_fetch_concurrency=settings.fetch_concurrency,
        generation_concurrency=email_providers.generation_concurrency,
    )
    return replace(
        email_rag,
        semantic_memory=semantic_memory,
        knowledge_documents=knowledge_documents,
        digest_worker=digest_worker,
        llm_configuration_error=None,
        llm_provider_label=provider_label,
    )


def degrade_email_rag(
    email_rag: EmailRagRuntime, *, configuration_error: str, provider_label: str
) -> EmailRagRuntime:
    """The coupled degrade outcome for the provider half (ADR-013, slice 02-5).

    The ``except ValueError`` path of the LLM provider block: null memory,
    empty corpus, no worker, and the configuration diagnostic kept
    server-side. The document plane survives untouched — a degraded provider
    half never takes the project-document vector plane offline, exactly as
    before the group existed.
    """
    return replace(
        email_rag,
        semantic_memory=NullSemanticMemory(),
        knowledge_documents=(),
        digest_worker=None,
        llm_configuration_error=configuration_error,
        llm_provider_label=provider_label,
    )


async def build_evaluation(
    evaluation_settings: EvaluationSettings | None,
) -> EvaluationBundle | None:
    """Compose the evaluation group (ADR-013, slice 02-6).

    The body is the construction that used to live inline in ``lifespan``,
    moved verbatim: ``build_evaluation_runtime`` from the settings' runtime
    config and the process environment, then ``initialize()`` before
    ``recover()``, with the except that closes the runtime before re-raising
    — never abandon an initialized runtime if recovery fails. Only *where*
    it happens changed, plus one round-trip deleted: the settings arrive as
    an explicit parameter captured from ``create_app`` instead of being read
    back off ``app.state`` across the phase boundary. The group composes
    only when settings are present and enabled — the same gate the old
    ``app.state.evaluation_settings`` write provided, now owned by this seam.
    """
    if evaluation_settings is None or not evaluation_settings.enabled:
        return None
    evaluation_runtime = build_evaluation_runtime(
        evaluation_settings.to_runtime_config(), os.environ
    )
    try:
        await evaluation_runtime.initialize()
        await evaluation_runtime.recover()
    except Exception:
        # Never abandon an initialized runtime if recovery fails.
        await evaluation_runtime.close()
        raise
    return EvaluationBundle(
        runtime=evaluation_runtime,
        service=evaluation_runtime.service,
        supervisor=evaluation_runtime.supervisor,
        api_token=evaluation_settings.api_token,
    )


__all__ = [
    "ChatRuntime",
    "ControlPlane",
    "CoworkRuntime",
    "EmailRagRuntime",
    "EvaluationBundle",
    "MailboxRuntime",
    "build_chat",
    "build_control_plane",
    "build_email_rag",
    "build_evaluation",
    "build_mailbox",
    "create_chat_session_buffer",
    "degrade_email_rag",
    "runtime",
    "upgrade_email_rag_providers",
]
