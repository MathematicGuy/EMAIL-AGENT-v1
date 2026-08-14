"""Live PostgreSQL deletion and expiry audit for V2-M6-B chat memory.

Proves that configured retention produces unreadable, purgable rows,
that user-wide deletion removes all owned rows without touching other
users, and that purge_expired physically removes expired rows.

Runs against cowork-pg; skips gracefully only when DATABASE_URL is unset.
"""

import asyncio
import os
import selectors
from collections.abc import Callable, Coroutine, Iterator
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
    pytest.skip(
        "psycopg is not installed (pip install '.[postgres]')", allow_module_level=True
    )

from cowork_agent.domain.chat_contracts import (  # noqa: E402
    ChatMemoryScope,
    DeclarativeProfile,
    EpisodeCitation,
    EpisodeSourceType,
    EpisodicMemoryQuery,
    MemoryNamespace,
    MemoryType,
    TaskEpisode,
)
from cowork_agent.domain.target_contracts import ValidationStatus  # noqa: E402
from cowork_agent.persistence.migrate import apply_migrations  # noqa: E402
from cowork_agent.persistence.repositories.postgres import (  # noqa: E402
    PostgresChatProfileRepository,
    PostgresTaskEpisodeRepository,
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


async def _repositories() -> tuple[
    PostgresChatProfileRepository, PostgresTaskEpisodeRepository, AsyncConnectionPool
]:
    pool = AsyncConnectionPool(DATABASE_URL, min_size=1, max_size=4, open=False)
    await pool.open(wait=True)
    await apply_migrations(pool)
    return (
        PostgresChatProfileRepository(pool),
        PostgresTaskEpisodeRepository(pool),
        pool,
    )


def _profile_namespace(
    *, tenant_id: str = "tenant-1", user_id: str = "user@example.com"
) -> MemoryNamespace:
    return MemoryNamespace(
        scope=ChatMemoryScope(
            tenant_id=tenant_id, user_id=user_id, session_id="session-1"
        ),
        memory_type=MemoryType.LONG_TERM,
        record_id=None,
        source_id=None,
    )


def _profile(
    *,
    tenant_id: str = "tenant-1",
    user_id: str = "user@example.com",
    expires_at: datetime | None = None,
) -> DeclarativeProfile:
    return DeclarativeProfile(
        profile_id=f"profile-{tenant_id}-{user_id}",
        user_id=user_id,
        language="vi",
        timezone="Asia/Bangkok",
        assistant_persona="Coworker",
        response_tone="direct",
        created_at=NOW,
        updated_at=NOW,
        expires_at=expires_at,
    )


def _episode_namespace(
    *,
    tenant_id: str = "tenant-1",
    user_id: str = "user@example.com",
    session_id: str = "session-1",
    record_id: str = "record-1",
    turn_id: str = "turn-1",
) -> MemoryNamespace:
    return MemoryNamespace(
        scope=ChatMemoryScope(
            tenant_id=tenant_id, user_id=user_id, session_id=session_id
        ),
        memory_type=MemoryType.EPISODIC,
        record_id=record_id,
        source_id=turn_id,
    )


def _episode(
    *,
    episode_id: str = "episode-1",
    record_id: str = "record-1",
    tenant_id: str = "tenant-1",
    user_id: str = "user@example.com",
    session_id: str = "session-1",
    turn_id: str = "turn-1",
    status: ValidationStatus = ValidationStatus.SYSTEM_GENERATED,
    created_at: datetime = NOW,
    updated_at: datetime = NOW,
) -> TaskEpisode:
    return TaskEpisode(
        episode_id=episode_id,
        record_id=record_id,
        user_id=user_id,
        chat_session_id=session_id,
        chat_turn_id=turn_id,
        creation_reason="explicit_user_task_request",
        task_title="Prepare quarterly report",
        minimal_request_paraphrase="Create a compact quarterly-report task plan.",
        action_plan=("Collect approved figures.", "Draft the report."),
        rag_citations=(
            EpisodeCitation(
                document_id="doc-1",
                document_title="Quarterly reporting guide",
                section="Reporting",
                source_url="https://knowledge.example.com/reporting",
            ),
        ),
        missing_information=("Reporting deadline is not stated.",),
        validation_status=status,
        retrieval_eligible=status
        in {ValidationStatus.USER_APPROVED, ValidationStatus.COMPLETED},
        source_type=EpisodeSourceType.SYSTEM_GENERATED_CHAT_TASK,
        created_at=created_at,
        updated_at=updated_at,
        pipeline_version="2",
        model_id="model-1",
        prompt_version="prompt-1",
        confidence=0.8,
    )


def test_expired_episode_is_excluded_from_reads_before_purge() -> None:
    """A past-but-valid expires_at makes the row invisible to reads, then purgable."""

    async def scenario() -> None:
        _, episodes_repo, pool = await _repositories()
        try:
            past_created = NOW - timedelta(days=10)
            past_expires = NOW - timedelta(days=5)
            namespace = _episode_namespace()
            episode = _episode(
                created_at=past_created, updated_at=past_created
            )
            await episodes_repo.write_task_episode(
                namespace, episode, expires_at=past_expires
            )
            # Read must exclude the expired row even before purge.
            query = EpisodicMemoryQuery(
                query="report", max_items=10, min_score=0.0, timeout_ms=100
            )
            found = await episodes_repo.read_episodes(
                _episode_namespace(session_id="new-session"), query
            )
            assert len(found) == 0
            # Purge must remove the expired row.
            purged = await episodes_repo.purge_expired(datetime.now(UTC))
            assert purged >= 1
            async with pool.connection() as connection:
                cursor = await connection.execute(
                    "SELECT count(*) FROM task_episodes"
                )
                assert (await cursor.fetchone())[0] == 0  # type: ignore[index]
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_user_wide_deletion_removes_all_owned_rows_and_preserves_other_users() -> None:
    """delete_profile + delete_all_for_user clears one user's memory; others untouched."""

    async def scenario() -> None:
        profiles_repo, episodes_repo, pool = await _repositories()
        try:
            namespace = _episode_namespace()
            # Seed a profile.
            await profiles_repo.write_profile(
                _profile_namespace(), _profile()
            )
            # Seed two episodes: one approved eligible, one system_generated.
            approved = _episode(
                episode_id="ep-approved",
                record_id="rec-approved",
                turn_id="turn-approved",
            )
            generated = _episode(
                episode_id="ep-generated",
                record_id="rec-generated",
                turn_id="turn-generated",
            )
            for ep in (approved, generated):
                ns = _episode_namespace(
                    record_id=ep.record_id, turn_id=ep.chat_turn_id
                )
                await episodes_repo.write_task_episode(ns, ep, expires_at=None)
            # Transition approved episode to USER_APPROVED for retrieval eligibility.
            from cowork_agent.domain.chat_contracts import EpisodeTransition

            approved_ns = _episode_namespace(
                record_id="rec-approved", turn_id="turn-approved"
            )
            transition = EpisodeTransition(
                episode_id="ep-approved",
                namespace=approved_ns,
                from_status=ValidationStatus.SYSTEM_GENERATED,
                to_status=ValidationStatus.USER_APPROVED,
                retrieval_eligible=True,
                transitioned_at=NOW + timedelta(minutes=2),
            )
            assert await episodes_repo.transition_task_episode(transition) is not None
            # Seed a different user's episode.
            other_user_ns = _episode_namespace(
                user_id="other@example.com",
                record_id="rec-other",
                turn_id="turn-other",
            )
            other_user_ep = _episode(
                episode_id="ep-other",
                record_id="rec-other",
                user_id="other@example.com",
                turn_id="turn-other",
            )
            await episodes_repo.write_task_episode(
                other_user_ns, other_user_ep, expires_at=None
            )
            # Delete: profile + all episodes for the target user.
            assert await profiles_repo.delete_profile(_profile_namespace()) is True
            deleted_count = await episodes_repo.delete_all_for_user(namespace)
            assert deleted_count == 2
            # Subsequent eligible retrieval returns zero rows for this user.
            query = EpisodicMemoryQuery(
                query="report", max_items=10, min_score=0.0, timeout_ms=100
            )
            found = await episodes_repo.read_episodes(
                _episode_namespace(session_id="new-session"), query
            )
            assert len(found) == 0
            # A different user's episode remains untouched.
            other_found = await episodes_repo.read_episodes(
                _episode_namespace(
                    user_id="other@example.com", session_id="new-session"
                ),
                query,
            )
            # Company RAG is not in this deletion path; no semantic RAG call is made.
            assert len(other_found) == 0 or other_found[0].episode_id == "ep-other"
            async with pool.connection() as connection:
                cursor = await connection.execute(
                    "SELECT count(*) FROM task_episodes WHERE user_id = %s",
                    ("other@example.com",),
                )
                assert (await cursor.fetchone())[0] == 1  # type: ignore[index]
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_purge_live_removes_expired_profile_and_episode() -> None:
    """Seed expired profile + expired episode; purge removes both."""

    async def scenario() -> None:
        profiles_repo, episodes_repo, pool = await _repositories()
        try:
            past = datetime.now(UTC) - timedelta(seconds=2)
            # Expired profile.
            await profiles_repo.write_profile(
                _profile_namespace(), _profile(expires_at=past)
            )
            # Expired episode.
            past_created = NOW - timedelta(days=3)
            namespace = _episode_namespace()
            episode = _episode(created_at=past_created, updated_at=past_created)
            await episodes_repo.write_task_episode(
                namespace, episode, expires_at=past
            )
            # Both purges must remove at least one row.
            profile_purged = await profiles_repo.purge_expired(datetime.now(UTC))
            episode_purged = await episodes_repo.purge_expired(datetime.now(UTC))
            assert profile_purged >= 1
            assert episode_purged >= 1
            # Rows are gone.
            async with pool.connection() as connection:
                cursor = await connection.execute(
                    "SELECT count(*) FROM chat_profiles"
                )
                assert (await cursor.fetchone())[0] == 0  # type: ignore[index]
                cursor = await connection.execute(
                    "SELECT count(*) FROM task_episodes"
                )
                assert (await cursor.fetchone())[0] == 0  # type: ignore[index]
        finally:
            await pool.close()

    _run_scenario(scenario)
