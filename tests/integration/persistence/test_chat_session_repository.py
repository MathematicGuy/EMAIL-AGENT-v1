"""PostgreSQL durable chat-session ownership integration tests."""

import asyncio
import os
import selectors
import sys
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

try:
    import psycopg
    from psycopg import sql
    from psycopg_pool import AsyncConnectionPool
except ImportError:  # pragma: no cover - environment-dependent
    pytest.skip("psycopg is not installed (pip install '.[postgres]')", allow_module_level=True)

from cowork_agent.domain.chat_contracts import (
    ChatActivity,
    ChatActivityCode,
    ChatActivityOutcome,
    ChatActivityStatus,
    ChatTurn,
    ChatTurnStatus,
    MailScanSummary,
)
from cowork_agent.features.ai_chat.controller import ChatSessionAccessDenied
from cowork_agent.persistence.migrate import apply_migrations
from cowork_agent.persistence.repositories.chat_history import PostgresChatHistoryRepository
from cowork_agent.persistence.repositories.chat_sessions import PostgresChatSessionRegistry
from cowork_agent.persistence.repositories.identity import PostgresIdentityRepository
from cowork_agent.persistence.repositories.projects import PostgresProjectRepository
from tests.integration.persistence.pg_probe import server_available

DATABASE_URL = os.getenv("PG_TEST_URL", "")


def _server_available() -> bool:
    return server_available(DATABASE_URL)


if not _server_available():
    pytest.skip("PG_TEST_URL is not configured or reachable", allow_module_level=True)


@pytest.fixture
def isolated_schema() -> Iterator[str]:
    """Give migration-destructive tests a schema no other xdist worker can see."""
    schema = f"chat_lifecycle_{uuid4().hex}"
    with psycopg.connect(DATABASE_URL, connect_timeout=3) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        yield schema
    finally:
        with psycopg.connect(DATABASE_URL, connect_timeout=3) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
            )


@pytest.fixture(autouse=True)
def fresh_schema(request: pytest.FixtureRequest) -> Iterator[None]:
    if "isolated_schema" in request.fixturenames:
        yield
        return
    with psycopg.connect(DATABASE_URL, connect_timeout=3) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    yield


async def _pool(*, schema: str | None = None) -> AsyncConnectionPool:
    connection_kwargs = {"options": f"-c search_path={schema}"} if schema else None
    pool = AsyncConnectionPool(
        DATABASE_URL,
        min_size=1,
        max_size=2,
        open=False,
        kwargs=connection_kwargs,
    )
    await pool.open(wait=True)
    return pool


def _run(scenario: Callable[[], Awaitable[None]]) -> None:
    if sys.platform == "win32" and sys.version_info >= (3, 12):
        asyncio.run(
            scenario(),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
        return
    asyncio.run(scenario())


def test_chat_session_is_visible_only_to_its_workspace_member_owner() -> None:
    async def scenario() -> None:
        pool = await _pool()
        try:
            await apply_migrations(pool)
            identities = PostgresIdentityRepository(pool)
            sessions = PostgresChatSessionRegistry(pool, new_id=lambda: "session-1")
            owner = await identities.resolve_or_create_principal("owner@example.com")
            other = await identities.resolve_or_create_principal("other@example.com")
            await PostgresProjectRepository(pool).default_project(owner)

            scope = await sessions.create(tenant_id=owner.workspace_id, user_id=owner.user_id)

            assert scope.session_id == "session-1"
            async with pool.connection() as connection:
                cursor = await connection.execute(
                    "SELECT project_id FROM chat_sessions WHERE id = %s", (scope.session_id,)
                )
                row = await cursor.fetchone()
            assert row is not None
            assert row[0] is not None
            assert (
                await sessions.require(
                    scope.session_id, tenant_id=owner.workspace_id, user_id=owner.user_id
                )
                == scope
            )
            assert await sessions.list_for(tenant_id=owner.workspace_id, user_id=owner.user_id) == (
                scope,
            )
            with pytest.raises(ChatSessionAccessDenied):
                await sessions.require(
                    scope.session_id, tenant_id=other.workspace_id, user_id=other.user_id
                )
        finally:
            await pool.close()

    _run(scenario)


def test_chat_history_begin_is_idempotent_and_completion_updates_in_place() -> None:
    async def scenario() -> None:
        pool = await _pool()
        try:
            await apply_migrations(pool)
            identities = PostgresIdentityRepository(pool)
            sessions = PostgresChatSessionRegistry(pool, new_id=lambda: "session-1")
            owner = await identities.resolve_or_create_principal("owner@example.com")
            await PostgresProjectRepository(pool).default_project(owner)
            scope = await sessions.create(tenant_id=owner.workspace_id, user_id=owner.user_id)
            repository = PostgresChatHistoryRepository(pool)
            pending = ChatTurn(
                turn_id="turn-1",
                session_id=scope.session_id,
                user_message="Keep this prompt while generating.",
                assistant_message=None,
                created_at=datetime(2026, 8, 17, tzinfo=UTC),
                status=ChatTurnStatus.GENERATING,
                idempotency_key="submission-1",
            )

            first = await repository.begin_turn(
                scope, pending, idempotency_key="submission-1", title="Keep this prompt"
            )
            replay = await repository.begin_turn(
                scope,
                ChatTurn(
                    turn_id="turn-replayed",
                    session_id=scope.session_id,
                    user_message=pending.user_message,
                    assistant_message=None,
                    created_at=datetime(2026, 8, 17, 0, 1, tzinfo=UTC),
                    status=ChatTurnStatus.GENERATING,
                    idempotency_key="submission-1",
                ),
                idempotency_key="submission-1",
                title="Ignored replay title",
            )
            with pytest.raises(ValueError, match="idempotency key"):
                await repository.begin_turn(
                    scope,
                    ChatTurn(
                        turn_id="turn-conflict",
                        session_id=scope.session_id,
                        user_message="A different prompt conflicts.",
                        assistant_message=None,
                        created_at=datetime(2026, 8, 17, 0, 2, tzinfo=UTC),
                        status=ChatTurnStatus.GENERATING,
                        idempotency_key="submission-1",
                    ),
                    idempotency_key="submission-1",
                    title="Conflict",
                )
            completed = await repository.update_turn(
                scope,
                ChatTurn(
                    turn_id=first.turn_id,
                    session_id=first.session_id,
                    user_message=first.user_message,
                    assistant_message="The reply completed.",
                    created_at=first.created_at,
                    status=ChatTurnStatus.COMPLETED,
                    idempotency_key=first.idempotency_key,
                    activities=(
                        ChatActivity(
                            code=ChatActivityCode.UNDERSTANDING_REQUEST,
                            status=ChatActivityStatus.COMPLETED,
                            outcome=ChatActivityOutcome.SUCCESS,
                            started_at=first.created_at,
                            completed_at=first.created_at,
                        ),
                    ),
                    completed_at=first.created_at,
                ),
                title="Generated title",
            )

            assert replay == first
            assert completed.status is ChatTurnStatus.COMPLETED
            assert completed.activities[0].code is ChatActivityCode.UNDERSTANDING_REQUEST
            assert completed.completed_at == first.created_at
            assert await repository.list_turns(scope) == (completed,)
            assert await repository.titles_for((scope,)) == {scope.session_id: "Generated title"}
            assert await repository.latest_turns_for((scope,)) == {scope.session_id: completed}
        finally:
            await pool.close()

    _run(scenario)


def test_chat_turn_lifecycle_migration_backfills_legacy_completed_turns(
    isolated_schema: str,
) -> None:
    async def scenario() -> None:
        pool = await _pool(schema=isolated_schema)
        try:
            await apply_migrations(pool)
            workspace_id = "00000000-0000-0000-0000-000000000001"
            user_id = "00000000-0000-0000-0000-000000000002"
            project_id = "00000000-0000-0000-0000-000000000003"
            session_id = "session-legacy"
            migrations = (
                Path(__file__).resolve().parents[3] / "src/cowork_agent/persistence/migrations"
            )
            async with pool.connection() as connection:
                current_schema = await connection.execute("SELECT current_schema()")
                assert await current_schema.fetchone() == (isolated_schema,)
                await connection.execute(
                    "INSERT INTO app_users (id, primary_email) VALUES (%s, %s)",
                    (user_id, "legacy@example.com"),
                )
                await connection.execute(
                    "INSERT INTO workspaces (id, name) VALUES (%s, %s)",
                    (workspace_id, "Legacy workspace"),
                )
                await connection.execute(
                    """
                    INSERT INTO workspace_members (workspace_id, user_id, role)
                    VALUES (%s, %s, 'owner')
                    """,
                    (workspace_id, user_id),
                )
                await connection.execute(
                    """
                    INSERT INTO projects (
                        id, workspace_id, owner_user_id, name, is_default
                    ) VALUES (%s, %s, %s, %s, TRUE)
                    """,
                    (project_id, workspace_id, user_id, "Default project"),
                )
                await connection.execute(
                    """
                    INSERT INTO chat_sessions (
                        id, workspace_id, user_id, project_id, feature
                    ) VALUES (%s, %s, %s, %s, 'ai_chat')
                    """,
                    (session_id, workspace_id, user_id, project_id),
                )
                await connection.execute(
                    (migrations / "014_chat_turn_lifecycle.down.sql").read_text(encoding="utf-8")
                )
                await connection.execute(
                    "DELETE FROM schema_migrations WHERE filename = %s",
                    ("014_chat_turn_lifecycle.sql",),
                )
                await connection.execute(
                    """
                    INSERT INTO chat_turns (
                        session_id, turn_id, user_message, assistant_message, created_at
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        session_id,
                        "legacy-turn",
                        "Legacy prompt",
                        "Legacy reply",
                        datetime(2026, 8, 16, tzinfo=UTC),
                    ),
                )

            assert await apply_migrations(pool) == ("014_chat_turn_lifecycle.sql",)
            async with pool.connection() as connection:
                cursor = await connection.execute(
                    """
                    SELECT status, idempotency_key, assistant_message
                    FROM chat_turns
                    WHERE session_id = %s AND turn_id = %s
                    """,
                    (session_id, "legacy-turn"),
                )
                row = await cursor.fetchone()

            assert row == ("completed", "legacy-turn", "Legacy reply")
        finally:
            await pool.close()

    _run(scenario)


def test_chat_history_survives_a_new_repository_instance_and_sets_its_title() -> None:
    async def scenario() -> None:
        pool = await _pool()
        try:
            await apply_migrations(pool)
            identities = PostgresIdentityRepository(pool)
            sessions = PostgresChatSessionRegistry(pool, new_id=lambda: "session-1")
            owner = await identities.resolve_or_create_principal("owner@example.com")
            await PostgresProjectRepository(pool).default_project(owner)
            scope = await sessions.create(tenant_id=owner.workspace_id, user_id=owner.user_id)
            turn = ChatTurn(
                turn_id="turn-1",
                session_id=scope.session_id,
                user_message="How should I prepare the report?",
                assistant_message="Start with the quarterly metrics.",
                created_at=datetime(2026, 8, 14, tzinfo=UTC),
                mail_scan=MailScanSummary(
                    status="succeeded",
                    emails_matched=10,
                    emails_processed=10,
                    emails_to_process=10,
                    action_items_count=3,
                ),
            )

            writer = PostgresChatHistoryRepository(pool)
            await writer.write_turn(scope, turn, title="Quarterly report plan")
            reader = PostgresChatHistoryRepository(pool)

            stored = await reader.list_turns(scope)
            assert len(stored) == 1
            assert stored[0].assistant_message == turn.assistant_message
            assert stored[0].status is ChatTurnStatus.COMPLETED
            assert stored[0].idempotency_key == turn.turn_id
            assert await reader.titles_for((scope,)) == {scope.session_id: "Quarterly report plan"}
        finally:
            await pool.close()

    _run(scenario)


def test_chat_history_list_owned_turns_is_none_for_a_non_owner() -> None:
    async def scenario() -> None:
        pool = await _pool()
        try:
            await apply_migrations(pool)
            identities = PostgresIdentityRepository(pool)
            sessions = PostgresChatSessionRegistry(pool, new_id=lambda: "session-1")
            history = PostgresChatHistoryRepository(pool)
            owner = await identities.resolve_or_create_principal("owner@example.com")
            other = await identities.resolve_or_create_principal("other@example.com")
            await PostgresProjectRepository(pool).default_project(owner)
            scope = await sessions.create(tenant_id=owner.workspace_id, user_id=owner.user_id)
            turn = ChatTurn(
                turn_id="turn-1",
                session_id=scope.session_id,
                user_message="How should I prepare the report?",
                assistant_message="Start with the quarterly metrics.",
                created_at=datetime(2026, 8, 14, tzinfo=UTC),
                idempotency_key="turn-1",
            )
            await history.write_turn(scope, turn, title="Quarterly report plan")

            assert (
                await history.list_owned_turns(
                    session_id=scope.session_id,
                    tenant_id=other.workspace_id,
                    user_id=other.user_id,
                )
                is None
            )
            owned = await history.list_owned_turns(
                session_id=scope.session_id,
                tenant_id=owner.workspace_id,
                user_id=owner.user_id,
            )
            assert owned == (scope, (turn,))
        finally:
            await pool.close()

    _run(scenario)
