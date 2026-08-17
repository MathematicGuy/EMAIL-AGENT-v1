"""PostgreSQL durable chat-session ownership integration tests."""

import asyncio
import os
import selectors
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime

import pytest

try:
    import psycopg
    from psycopg_pool import AsyncConnectionPool
except ImportError:  # pragma: no cover - environment-dependent
    pytest.skip("psycopg is not installed (pip install '.[postgres]')", allow_module_level=True)

from cowork_agent.domain.chat_contracts import ChatTurn, MailScanSummary
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


@pytest.fixture(autouse=True)
def fresh_schema() -> Iterator[None]:
    with psycopg.connect(DATABASE_URL, connect_timeout=3) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    yield


async def _pool() -> AsyncConnectionPool:
    pool = AsyncConnectionPool(DATABASE_URL, min_size=1, max_size=2, open=False)
    await pool.open(wait=True)
    return pool


def _run(scenario: Callable[[], Awaitable[None]]) -> None:
    asyncio.run(
        scenario(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
    )


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

            scope = await sessions.create(
                tenant_id=owner.workspace_id, user_id=owner.user_id
            )

            assert scope.session_id == "session-1"
            async with pool.connection() as connection:
                cursor = await connection.execute(
                    "SELECT project_id FROM chat_sessions WHERE id = %s", (scope.session_id,)
                )
                row = await cursor.fetchone()
            assert row is not None
            assert row[0] is not None
            assert await sessions.require(
                scope.session_id, tenant_id=owner.workspace_id, user_id=owner.user_id
            ) == scope
            assert await sessions.list_for(
                tenant_id=owner.workspace_id, user_id=owner.user_id
            ) == (scope,)
            with pytest.raises(ChatSessionAccessDenied):
                await sessions.require(
                    scope.session_id, tenant_id=other.workspace_id, user_id=other.user_id
                )
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
            scope = await sessions.create(
                tenant_id=owner.workspace_id, user_id=owner.user_id
            )
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

            assert await reader.list_turns(scope) == (turn,)
            assert await reader.titles_for((scope,)) == {
                scope.session_id: "Quarterly report plan"
            }
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
            scope = await sessions.create(
                tenant_id=owner.workspace_id, user_id=owner.user_id
            )
            turn = ChatTurn(
                turn_id="turn-1",
                session_id=scope.session_id,
                user_message="How should I prepare the report?",
                assistant_message="Start with the quarterly metrics.",
                created_at=datetime(2026, 8, 14, tzinfo=UTC),
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
