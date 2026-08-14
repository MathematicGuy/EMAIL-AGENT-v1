"""Supabase/PostgreSQL identity and opaque-session repository tests."""

import asyncio
import os
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta

import pytest

try:
    import psycopg
    from psycopg_pool import AsyncConnectionPool
except ImportError:  # pragma: no cover - environment-dependent
    pytest.skip("psycopg is not installed (pip install '.[postgres]')", allow_module_level=True)

from cowork_agent.domain import MailboxConnection
from cowork_agent.persistence.migrate import apply_migrations
from cowork_agent.persistence.repositories.identity import (
    PostgresIdentityRepository,
    PostgresMailboxConnectionRepository,
    PostgresSessionRepository,
)
from tests.integration.persistence.pg_probe import server_available

DATABASE_URL = os.getenv("PG_TEST_URL", "")
NOW = datetime(2026, 8, 12, 9, tzinfo=UTC)


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


def _run(scenario: Callable[[], Awaitable[None]]) -> None:
    asyncio.run(scenario())


async def _pool() -> AsyncConnectionPool:
    pool = AsyncConnectionPool(DATABASE_URL, min_size=1, max_size=2, open=False)
    await pool.open(wait=True)
    return pool


def test_identity_creates_a_default_workspace_and_session_is_workspace_bound() -> None:
    async def scenario() -> None:
        pool = await _pool()
        try:
            await apply_migrations(pool)
            identities = PostgresIdentityRepository(pool)
            sessions = PostgresSessionRepository(pool)
            principal = await identities.resolve_or_create_principal("Owner@Example.com")
            assert principal.user_id != "owner@example.com"
            assert principal.workspace_id
            assert await identities.resolve_or_create_principal("owner@example.com") == principal

            token, expires_at = await sessions.create(principal, now=NOW, ttl_seconds=3600)
            assert expires_at == NOW + timedelta(hours=1)
            assert await sessions.resolve(token, now=NOW) == principal
            assert await sessions.revoke(token, now=NOW)
            assert await sessions.resolve(token, now=NOW) is None
        finally:
            await pool.close()

    _run(scenario)


def test_mailbox_connections_are_listed_only_for_the_internal_owner() -> None:
    async def scenario() -> None:
        pool = await _pool()
        try:
            await apply_migrations(pool)
            identities = PostgresIdentityRepository(pool)
            mailboxes = PostgresMailboxConnectionRepository(pool)
            owner = await identities.resolve_or_create_principal("owner@example.com")
            other = await identities.resolve_or_create_principal("other@example.com")
            connection = MailboxConnection(
                id="mbx-owner",
                user_id=owner.user_id,
                provider="gmail",
                external_account_id="owner@example.com",
                email_address="owner@example.com",
                encrypted_refresh_token="encrypted",
                scopes=("https://www.googleapis.com/auth/gmail.readonly",),
                status="active",
                created_at=NOW,
                updated_at=NOW,
            )
            await mailboxes.upsert_for_workspace(connection, workspace_id=owner.workspace_id)

            assert await mailboxes.list_for_user(owner.user_id) == (connection,)
            assert await mailboxes.list_for_user(other.user_id) == ()
        finally:
            await pool.close()

    _run(scenario)
