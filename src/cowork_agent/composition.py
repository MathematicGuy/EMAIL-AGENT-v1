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
Later slices grow the remaining groups — ``mailbox``, ``chat``, ``email_rag``,
``evaluation`` — without changing this interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from starlette.requests import Request

from cowork_agent.config import GmailSettings, SessionSettings
from cowork_agent.domain.report_artifacts import ReportArtifactStore
from cowork_agent.features.ai_chat.controller import ChatSessionRegistryPort
from cowork_agent.features.email_action_plan.ports import (
    MailboxConnectionRepository,
    RunRepository,
    TaskRepository,
)
from cowork_agent.features.email_action_plan.workflow import (
    CreateDigestRun,
    GetDigestResult,
)
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


@dataclass(frozen=True, slots=True)
class CoworkRuntime:
    """The composed value of the application: dependencies that outlive requests.

    Frozen so no code path can swap a field mid-flight, and typed so a missing
    dependency is a mypy error at the composition root instead of a ``None``
    found at request time. ``control_plane`` is optional only for injected
    test runtimes that exercise a single group (the ASGI transport never runs
    ``lifespan``); a boot through ``lifespan`` always composes it. Later slices
    add the remaining groups (``mailbox``, ``chat``, ``email_rag``,
    ``evaluation``); each migration moves *where* a consumer reads from, never
    *what* is composed.
    """

    reports: ReportArtifactStore
    control_plane: ControlPlane | None = None


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


__all__ = ["ControlPlane", "CoworkRuntime", "build_control_plane", "runtime"]
