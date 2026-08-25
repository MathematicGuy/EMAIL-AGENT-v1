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
pool and its migrations, and the digest run/task/result bookkeeping. The group
is built by ``build_control_plane`` and ``lifespan`` still forwards every
legacy ``app.state.<key>`` from it — deliberate temporary duplication that
later slices delete consumer by consumer (the deletion test: a forward
disappears only when its last reader moves behind ``runtime(request)``).
Slice 02-3 adds ``MailboxRuntime``, the group that owns the provider mailbox
construction: the shared cipher, the Gmail and Outlook connection/mailbox
adapters with the Outlook sqlite-only gate, the routing adapter, and the
private document storage. Later slices grow the remaining groups — ``chat``,
``email_rag``, ``evaluation`` — without changing this interface.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import httpx
from starlette.requests import Request

from cowork_agent.config import (
    GmailSettings,
    OutlookSettings,
    SessionSettings,
    SupabaseStorageSettings,
    UserDocumentsSettings,
)
from cowork_agent.domain.report_artifacts import ReportArtifactStore
from cowork_agent.features.ai_chat.controller import ChatSessionRegistryPort
from cowork_agent.features.email_action_plan.ports import (
    MailboxConnectionRepository,
    MailboxPort,
    RunRepository,
    TaskRepository,
)
from cowork_agent.features.email_action_plan.workflow import (
    CreateDigestRun,
    GetDigestResult,
)
from cowork_agent.identity import PrincipalRepository
from cowork_agent.integrations.gmail.auth import OAuthStateManager, TokenCipher
from cowork_agent.integrations.gmail.provider import (
    GmailConnectionService,
    GmailMailboxAdapter,
    GmailPrincipalResolver,
)
from cowork_agent.integrations.mailbox.router import ProviderRoutingMailboxAdapter
from cowork_agent.integrations.outlook import (
    OutlookConnectionService,
    OutlookMailboxAdapter,
)
from cowork_agent.integrations.storage.supabase import SupabasePrivateStorage
from cowork_agent.orchestration.local import InMemoryOutbox
from cowork_agent.persistence.repositories.local import InMemoryResultRepository
from cowork_agent.persistence.repositories.mailbox_connections import (
    SQLiteMailboxConnectionRepository,
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
    repositories do not exist on the SQLite path). Field types name the real
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
    untyped ``app.state`` key. The ``| None`` fields are capabilities that are
    genuinely absent in one boot mode: the Outlook adapters exist only on the
    SQLite control plane with valid Microsoft OAuth configuration, and the
    Supabase storage client exists only when Postgres plus Storage is
    configured. ``private_storage`` itself can be ``None`` when Supabase
    Storage validation fails — the degrade path the old inline code had.
    """

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


@dataclass(frozen=True, slots=True)
class CoworkRuntime:
    """The composed value of the application: dependencies that outlive requests.

    Frozen so no code path can swap a field mid-flight, and typed so a missing
    dependency is a mypy error at the composition root instead of a ``None``
    found at request time. ``control_plane`` and ``mailbox`` are optional only
    for injected test runtimes that exercise a single group (the ASGI
    transport never runs ``lifespan``); a boot through ``lifespan`` always
    composes both. Later slices add the remaining groups (``chat``,
    ``email_rag``, ``evaluation``); each migration moves *where* a consumer
    reads from, never *what* is composed.
    """

    reports: ReportArtifactStore
    control_plane: ControlPlane | None = None
    mailbox: MailboxRuntime | None = None


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


__all__ = [
    "ControlPlane",
    "CoworkRuntime",
    "MailboxRuntime",
    "build_control_plane",
    "build_mailbox",
    "runtime",
]
