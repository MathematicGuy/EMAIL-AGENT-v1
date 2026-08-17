"""PostgreSQL system-generated chat-summary episode store tests (V2-M3)."""

import asyncio
import os
import selectors
from collections.abc import Callable, Coroutine, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from tests.integration.persistence.pg_probe import server_available

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
    ChatSummaryEpisode,
    EpisodeSourceType,
    MemoryNamespace,
    MemoryType,
)
from cowork_agent.domain.target_contracts import ValidationStatus  # noqa: E402
from cowork_agent.persistence.migrate import apply_migrations  # noqa: E402
from cowork_agent.persistence.repositories.postgres import (  # noqa: E402
    PostgresChatSummaryEpisodeRepository,
)

NOW = datetime(2026, 8, 10, 9, tzinfo=UTC)


def _run_scenario(scenario: Callable[[], Coroutine[object, object, None]]) -> None:
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
    return server_available(DATABASE_URL)


if not _server_available():
    pytest.skip(
        f"no PostgreSQL server at {DATABASE_URL} (set PG_TEST_URL or start cowork-pg)",
        allow_module_level=True,
    )


async def _repository() -> tuple[PostgresChatSummaryEpisodeRepository, AsyncConnectionPool]:
    pool = AsyncConnectionPool(DATABASE_URL, min_size=1, max_size=4, open=False)
    await pool.open(wait=True)
    await apply_migrations(pool)
    return PostgresChatSummaryEpisodeRepository(pool), pool


def _namespace(
    *, record_id: str = "record-1", chat_turn_id: str | None = "turn-1"
) -> MemoryNamespace:
    return MemoryNamespace(
        scope=ChatMemoryScope(
            tenant_id="tenant-1", user_id="user@example.com", session_id="session-1"
        ),
        memory_type=MemoryType.EPISODIC,
        record_id=record_id,
        source_id=chat_turn_id,
    )


def _episode(
    *,
    episode_id: str = "episode-1",
    record_id: str = "record-1",
    summary: str = "Initial compact summary.",
    updated_at: datetime = NOW,
    expires_at: datetime | None = None,
) -> ChatSummaryEpisode:
    return ChatSummaryEpisode(
        episode_id=episode_id,
        record_id=record_id,
        user_id="user@example.com",
        chat_session_id="session-1",
        chat_turn_id="turn-1",
        summary=summary,
        validation_status=ValidationStatus.SYSTEM_GENERATED,
        retrieval_eligible=False,
        source_type=EpisodeSourceType.SYSTEM_GENERATED_CHAT_SUMMARY,
        created_at=NOW,
        updated_at=updated_at,
        expires_at=expires_at,
        pipeline_version="2",
        model_id="model-1",
        prompt_version="prompt-1",
        confidence=0.8,
    )


def test_retry_safe_upsert_preserves_original_identity_and_rejects_stale_updates() -> None:
    async def scenario() -> None:
        repository, pool = await _repository()
        try:
            first = await repository.write_chat_summary(_namespace(), _episode())
            newer = await repository.write_chat_summary(
                _namespace(record_id="retry-record"),
                _episode(
                    episode_id="retry-episode",
                    record_id="retry-record",
                    summary="Newer compact summary.",
                    updated_at=NOW + timedelta(minutes=1),
                ),
            )
            stale = await repository.write_chat_summary(
                _namespace(record_id="stale-record"),
                _episode(
                    episode_id="stale-episode",
                    record_id="stale-record",
                    summary="Stale summary must not replace newer data.",
                    updated_at=NOW,
                ),
            )

            assert first.episode_id == newer.episode_id == stale.episode_id == "episode-1"
            assert first.record_id == newer.record_id == stale.record_id == "record-1"
            assert first.created_at == newer.created_at == stale.created_at == NOW
            assert newer.summary == stale.summary == "Newer compact summary."
            assert newer.updated_at == stale.updated_at == NOW + timedelta(minutes=1)
            async with pool.connection() as connection:
                cursor = await connection.execute("SELECT count(*) FROM chat_summary_episodes")
                assert (await cursor.fetchone())[0] == 1  # type: ignore[index]
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_delete_uses_the_exact_persisted_summary_namespace_key() -> None:
    async def scenario() -> None:
        repository, pool = await _repository()
        try:
            await repository.write_chat_summary(_namespace(), _episode())

            deletion_namespace = _namespace(chat_turn_id=None)
            assert await repository.delete_chat_summary(deletion_namespace) is True
            assert await repository.delete_chat_summary(deletion_namespace) is False
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_delete_all_for_user_is_exact_scope_and_retryable() -> None:
    def scoped_namespace(tenant_id: str, user_id: str, turn_id: str) -> MemoryNamespace:
        return MemoryNamespace(
            scope=ChatMemoryScope(tenant_id=tenant_id, user_id=user_id, session_id="session-1"),
            memory_type=MemoryType.EPISODIC,
            record_id=f"record-{turn_id}",
            source_id=turn_id,
        )

    async def scenario() -> None:
        repository, pool = await _repository()
        try:
            exact_namespace = scoped_namespace("tenant-1", "user@example.com", "turn-exact")
            foreign_user_namespace = scoped_namespace(
                "tenant-1", "other@example.com", "turn-user"
            )
            await repository.write_chat_summary(
                exact_namespace,
                replace(_episode(), record_id="record-turn-exact", chat_turn_id="turn-exact"),
            )
            await repository.write_chat_summary(
                foreign_user_namespace,
                replace(
                    _episode(),
                    episode_id="episode-user",
                    record_id="record-turn-user",
                    user_id="other@example.com",
                    chat_turn_id="turn-user",
                ),
            )

            deletion_namespace = replace(exact_namespace, source_id=None)
            assert await repository.delete_all_for_user(deletion_namespace) == 1
            assert await repository.delete_all_for_user(deletion_namespace) == 0
            async with pool.connection() as connection:
                cursor = await connection.execute(
                    "SELECT tenant_id, user_id FROM chat_summary_episodes"
                    " ORDER BY tenant_id, user_id"
                )
                assert await cursor.fetchall() == [("local", "other@example.com")]
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_expiry_purge_and_schema_stay_compact_and_body_free() -> None:
    async def scenario() -> None:
        repository, pool = await _repository()
        try:
            expires_at = NOW + timedelta(minutes=1)
            await repository.write_chat_summary(
                _namespace(), _episode(expires_at=expires_at)
            )

            assert await repository.purge_expired(expires_at) == 1
            assert await repository.purge_expired(expires_at) == 0
            async with pool.connection() as connection:
                cursor = await connection.execute(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = 'chat_summary_episodes'"
                )
                columns = {str(row[0]) for row in await cursor.fetchall()}
            assert not columns & {
                "body",
                "email_body",
                "raw_email",
                "normalized_body",
                "attachment_content",
                "transcript",
                "messages",
                "tool_payload",
            }
        finally:
            await pool.close()

    _run_scenario(scenario)
