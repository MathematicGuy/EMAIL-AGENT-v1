"""Declarative chat profile store integration tests (V2-M2).

Same contract as ``test_postgres_repositories``: run against a real
PostgreSQL 16 server (``PG_TEST_URL`` overrides the default dev container),
and skip the whole module when no server answers so the default suite stays
green without PostgreSQL.
"""

import asyncio
import os
import selectors
from collections.abc import Callable, Coroutine, Iterator
from datetime import UTC, datetime, timedelta

import pytest

DATABASE_URL = os.getenv(
    "PG_TEST_URL",
    "postgresql://cowork:cowork_dev_only@127.0.0.1:5432/cowork_mail_todo",
)

try:
    import psycopg
    from psycopg_pool import AsyncConnectionPool
except ImportError:  # pragma: no cover - environment-dependent
    pytest.skip("psycopg is not installed (pip install '.[postgres]')", allow_module_level=True)

from cowork_agent.domain.chat_contracts import (  # noqa: E402
    ChatMemoryScope,
    DeclarativeProfile,
    MemoryNamespace,
    MemoryType,
)
from cowork_agent.persistence.migrate import apply_migrations  # noqa: E402
from cowork_agent.persistence.repositories.postgres import (  # noqa: E402
    PostgresChatProfileRepository,
)

NOW = datetime(2026, 8, 10, 9, tzinfo=UTC)


def _run_scenario(scenario: Callable[[], Coroutine[object, object, None]]) -> None:
    # Windows' default ProactorEventLoop is unsupported by psycopg async.
    asyncio.run(
        scenario(),
        loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
    )


@pytest.fixture(autouse=True)
def fresh_schema() -> Iterator[None]:
    with psycopg.connect(DATABASE_URL, connect_timeout=3) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    yield


def _server_available() -> bool:
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3):
            return True
    except psycopg.Error:
        return False


if not _server_available():
    pytest.skip(
        f"no PostgreSQL server at {DATABASE_URL} (set PG_TEST_URL or start cowork-pg)",
        allow_module_level=True,
    )


async def _repository() -> tuple[PostgresChatProfileRepository, AsyncConnectionPool]:
    pool = AsyncConnectionPool(DATABASE_URL, min_size=1, max_size=4, open=False)
    await pool.open(wait=True)
    await apply_migrations(pool)
    return PostgresChatProfileRepository(pool), pool


def _namespace(
    *, tenant_id: str = "tenant-1", user_id: str = "user@example.com"
) -> MemoryNamespace:
    return MemoryNamespace(
        scope=ChatMemoryScope(tenant_id=tenant_id, user_id=user_id, session_id="session-1"),
        memory_type=MemoryType.LONG_TERM,
        record_id=None,
        source_id=None,
    )


def _profile(
    *,
    tenant_id: str = "tenant-1",
    user_id: str = "user@example.com",
    response_tone: str = "direct",
    expires_at: datetime | None = None,
) -> DeclarativeProfile:
    return DeclarativeProfile(
        profile_id=f"profile-{tenant_id}-{user_id}",
        tenant_id=tenant_id,
        user_id=user_id,
        language="vi",
        timezone="Asia/Bangkok",
        assistant_persona="Coworker",
        response_tone=response_tone,
        created_at=NOW,
        updated_at=NOW,
        expires_at=expires_at,
    )


def test_profile_round_trips_and_repeated_writes_stay_idempotent() -> None:
    async def scenario() -> None:
        repository, pool = await _repository()
        try:
            first = await repository.write_profile(_namespace(), _profile())
            assert await repository.read_profile(_namespace()) == first

            updated = await repository.write_profile(
                _namespace(), _profile(response_tone="concise")
            )

            assert updated.response_tone == "concise"
            assert updated.created_at == NOW
            stored = await repository.read_profile(_namespace())
            assert stored == updated
            async with pool.connection() as connection:
                cursor = await connection.execute("SELECT count(*) FROM chat_profiles")
                assert (await cursor.fetchone())[0] == 1  # type: ignore[index]
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_profiles_are_isolated_per_tenant_and_user() -> None:
    async def scenario() -> None:
        repository, pool = await _repository()
        try:
            await repository.write_profile(_namespace(), _profile())
            await repository.write_profile(
                _namespace(tenant_id="tenant-2"),
                _profile(tenant_id="tenant-2", response_tone="formal"),
            )

            assert (await repository.read_profile(_namespace())).response_tone == "direct"  # type: ignore[union-attr]
            other_tenant = await repository.read_profile(_namespace(tenant_id="tenant-2"))
            assert other_tenant is not None
            assert other_tenant.response_tone == "formal"
            assert await repository.read_profile(_namespace(user_id="other@example.com")) is None
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_expired_profile_is_never_returned_and_purges() -> None:
    async def scenario() -> None:
        repository, pool = await _repository()
        try:
            expired = datetime.now(UTC) - timedelta(seconds=1)
            await repository.write_profile(_namespace(), _profile(expires_at=expired))

            assert await repository.read_profile(_namespace()) is None
            assert await repository.purge_expired(datetime.now(UTC)) == 1
            assert await repository.purge_expired(datetime.now(UTC)) == 0
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_deletion_prevents_later_retrieval() -> None:
    async def scenario() -> None:
        repository, pool = await _repository()
        try:
            await repository.write_profile(_namespace(), _profile())

            assert await repository.delete_profile(_namespace()) is True
            assert await repository.read_profile(_namespace()) is None
            assert await repository.delete_profile(_namespace()) is False
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_schema_carries_no_email_body_or_chat_transcript_column() -> None:
    async def scenario() -> None:
        _, pool = await _repository()
        try:
            async with pool.connection() as connection:
                cursor = await connection.execute(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = 'chat_profiles'"
                )
                columns = {str(row[0]) for row in await cursor.fetchall()}
            assert not columns & {
                "body",
                "email_body",
                "raw_email",
                "normalized_body",
                "attachment_content",
                "message",
                "transcript",
            }
        finally:
            await pool.close()

    _run_scenario(scenario)
