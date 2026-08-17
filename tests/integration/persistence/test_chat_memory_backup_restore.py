"""Live PostgreSQL backup/restore operational proof for V2-M6-B chat memory.

Index-propagation N/A evidence (FR-16): user memory (chat_profiles,
chat_summary_episodes, task_episodes) lives exclusively in PostgreSQL.
Company knowledge is a Turbovec snapshot; there is no derived user-memory
search index anywhere in integrations/ or scripts/. Therefore FR-16
index propagation is N/A by design, and semantic RAG content is never
affected by user-memory deletion, purge, or table-level backup/restore.

Runs against cowork-pg; skips gracefully when DATABASE_URL is unset or
neither host pg_dump nor docker-exec-with-cowork-pg is reachable.
"""

import asyncio
import os
import selectors
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Coroutine, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
        "psycopg is not installed (pip install '.[postgres]')",
        allow_module_level=True,
    )

from cowork_agent.domain.chat_contracts import (  # noqa: E402
    ChatMemoryScope,
    ChatSummaryEpisode,
    DeclarativeProfile,
    EpisodeCitation,
    EpisodeSourceType,
    EpisodeTransition,
    EpisodicMemoryQuery,
    MemoryNamespace,
    MemoryType,
    TaskEpisode,
)
from cowork_agent.domain.target_contracts import ValidationStatus  # noqa: E402
from cowork_agent.persistence.migrate import apply_migrations  # noqa: E402
from cowork_agent.persistence.repositories.postgres import (  # noqa: E402
    PostgresChatProfileRepository,
    PostgresChatSummaryEpisodeRepository,
    PostgresTaskEpisodeRepository,
)

# ---------------------------------------------------------------------------
# Skip guards
# ---------------------------------------------------------------------------

NOW = datetime(2026, 8, 10, 9, tzinfo=UTC)
DOCKER_CONTAINER = "cowork-pg"


def _server_available() -> bool:
    return server_available(DATABASE_URL)


def _resolve_pg_dump_mode() -> str | None:
    """Return ``'host'`` if pg_dump is on PATH, ``'docker'`` if container has it, else ``None``."""
    if shutil.which("pg_dump") is not None:
        return "host"
    try:
        result = subprocess.run(
            ["docker", "exec", DOCKER_CONTAINER, "pg_dump", "--version"],
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0:
            return "docker"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


if not _server_available():
    pytest.skip(
        f"no PostgreSQL server at {DATABASE_URL} (set PG_TEST_URL or start cowork-pg)",
        allow_module_level=True,
    )

_PG_DUMP_MODE = _resolve_pg_dump_mode()
if _PG_DUMP_MODE is None:
    pytest.skip(
        "pg_dump not on PATH and docker exec cowork-pg pg_dump unavailable",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


async def _repositories() -> tuple[
    PostgresChatProfileRepository,
    PostgresChatSummaryEpisodeRepository,
    PostgresTaskEpisodeRepository,
    AsyncConnectionPool,
]:
    pool = AsyncConnectionPool(DATABASE_URL, min_size=1, max_size=4, open=False)
    await pool.open(wait=True)
    await apply_migrations(pool)
    return (
        PostgresChatProfileRepository(pool),
        PostgresChatSummaryEpisodeRepository(pool),
        PostgresTaskEpisodeRepository(pool),
        pool,
    )


def _docker_kw() -> dict[str, str | None]:
    """Return the docker_container kwarg dict for the backup/restore functions."""
    if _PG_DUMP_MODE == "docker":
        return {"docker_container": DOCKER_CONTAINER}
    return {"docker_container": None}


def _profile_namespace(
    *, tenant_id: str = "tenant-bak", user_id: str = "bak-user@example.com"
) -> MemoryNamespace:
    return MemoryNamespace(
        scope=ChatMemoryScope(
            tenant_id=tenant_id, user_id=user_id, session_id="session-bak"
        ),
        memory_type=MemoryType.LONG_TERM,
        record_id=None,
        source_id=None,
    )


def _profile(*, expires_at: datetime | None = None) -> DeclarativeProfile:
    return DeclarativeProfile(
        profile_id="profile-bak",
        user_id="bak-user@example.com",
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
    record_id: str = "rec-bak-1",
    turn_id: str = "turn-bak-1",
) -> MemoryNamespace:
    return MemoryNamespace(
        scope=ChatMemoryScope(
            tenant_id="tenant-bak",
            user_id="bak-user@example.com",
            session_id="session-bak",
        ),
        memory_type=MemoryType.EPISODIC,
        record_id=record_id,
        source_id=turn_id,
    )


def _task_episode(
    *,
    episode_id: str = "ep-bak-1",
    record_id: str = "rec-bak-1",
    turn_id: str = "turn-bak-1",
) -> TaskEpisode:
    return TaskEpisode(
        episode_id=episode_id,
        record_id=record_id,
        user_id="bak-user@example.com",
        chat_session_id="session-bak",
        chat_turn_id=turn_id,
        creation_reason="explicit_user_task_request",
        task_title="Prepare quarterly report",
        minimal_request_paraphrase="Create a compact quarterly-report task plan.",
        action_plan=("Collect approved figures.", "Draft the report."),
        rag_citations=(
            EpisodeCitation(
                document_id="doc-bak",
                document_title="Quarterly reporting guide",
                section="Reporting",
                source_url="https://knowledge.example.com/reporting",
            ),
        ),
        missing_information=("Reporting deadline is not stated.",),
        validation_status=ValidationStatus.SYSTEM_GENERATED,
        retrieval_eligible=False,
        source_type=EpisodeSourceType.SYSTEM_GENERATED_CHAT_TASK,
        created_at=NOW,
        updated_at=NOW,
        pipeline_version="2",
        model_id="model-bak",
        prompt_version="prompt-bak",
        confidence=0.9,
    )


def _chat_summary_episode() -> ChatSummaryEpisode:
    return ChatSummaryEpisode(
        episode_id="ep-summary-bak",
        record_id="rec-summary-bak",
        user_id="bak-user@example.com",
        chat_session_id="session-bak",
        chat_turn_id="turn-summary-bak",
        summary="Compact backup-test summary.",
        validation_status=ValidationStatus.SYSTEM_GENERATED,
        retrieval_eligible=False,
        source_type=EpisodeSourceType.SYSTEM_GENERATED_CHAT_SUMMARY,
        created_at=NOW,
        updated_at=NOW,
        expires_at=None,
        pipeline_version="2",
        model_id="model-bak",
        prompt_version="prompt-bak",
        confidence=0.85,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_backup_restore_preserves_chat_memory_metadata() -> None:
    """Seed, backup, delete, restore, and assert metadata fidelity."""
    from scripts.backup_restore_chat_memory import backup_tables, restore_tables

    future_expiry = NOW + timedelta(days=30)
    tmp_dir = Path(tempfile.mkdtemp(prefix="chat_bak_"))

    async def scenario() -> None:
        profiles_repo, summaries_repo, episodes_repo, pool = await _repositories()
        try:
            # ---- Seed ----
            # 1. Profile with expires_at set (future, so readable after restore).
            await profiles_repo.write_profile(
                _profile_namespace(), _profile(expires_at=future_expiry)
            )

            # 2. Approved eligible task episode.
            approved_ns = _episode_namespace(
                record_id="rec-bak-approved", turn_id="turn-bak-approved"
            )
            approved_ep = _task_episode(
                episode_id="ep-bak-approved",
                record_id="rec-bak-approved",
                turn_id="turn-bak-approved",
            )
            await episodes_repo.write_task_episode(
                approved_ns, approved_ep, expires_at=None
            )
            # Transition to USER_APPROVED for retrieval eligibility.
            transition = EpisodeTransition(
                episode_id="ep-bak-approved",
                namespace=approved_ns,
                from_status=ValidationStatus.SYSTEM_GENERATED,
                to_status=ValidationStatus.USER_APPROVED,
                retrieval_eligible=True,
                transitioned_at=NOW + timedelta(minutes=2),
            )
            assert await episodes_repo.transition_task_episode(transition) is not None

            # 3. System-generated ineligible task episode.
            ineligible_ns = _episode_namespace(
                record_id="rec-bak-ineligible", turn_id="turn-bak-ineligible"
            )
            ineligible_ep = _task_episode(
                episode_id="ep-bak-ineligible",
                record_id="rec-bak-ineligible",
                turn_id="turn-bak-ineligible",
            )
            await episodes_repo.write_task_episode(
                ineligible_ns, ineligible_ep, expires_at=None
            )

            # 4. Chat summary episode.
            summary_ns = MemoryNamespace(
                scope=ChatMemoryScope(
                    tenant_id="tenant-bak",
                    user_id="bak-user@example.com",
                    session_id="session-bak",
                ),
                memory_type=MemoryType.EPISODIC,
                record_id="rec-summary-bak",
                source_id="turn-summary-bak",
            )
            await summaries_repo.write_chat_summary(
                summary_ns, _chat_summary_episode()
            )

            # ---- Backup ----
            archive_path = tmp_dir / "chat_memory_backup.dump"
            await backup_tables(DATABASE_URL, archive_path, **_docker_kw())
            assert archive_path.exists()
            assert archive_path.stat().st_size > 0

            # ---- Delete all seeded rows (simulating loss) ----
            async with pool.connection() as connection:
                await connection.execute(
                    "DELETE FROM chat_profiles WHERE tenant_id = %s",
                    ("local",),
                )
                await connection.execute(
                    "DELETE FROM task_episodes WHERE tenant_id = %s",
                    ("local",),
                )
                await connection.execute(
                    "DELETE FROM chat_summary_episodes WHERE tenant_id = %s",
                    ("local",),
                )

            # Verify deletion.
            async with pool.connection() as connection:
                for table in ("chat_profiles", "task_episodes", "chat_summary_episodes"):
                    cursor = await connection.execute(
                        f"SELECT count(*) FROM {table} WHERE tenant_id = %s",
                        ("local",),
                    )
                    assert (await cursor.fetchone())[0] == 0  # type: ignore[index]

            # ---- Restore ----
            await restore_tables(DATABASE_URL, archive_path, **_docker_kw())

            # ---- Assert: profile preserved ----
            restored_profile = await profiles_repo.read_profile(_profile_namespace())
            assert restored_profile is not None
            assert restored_profile.user_id == "bak-user@example.com"
            assert restored_profile.response_tone == "direct"
            assert restored_profile.expires_at is not None
            # Compare timestamps to second precision (PG microsecond rounding).
            assert abs(
                (restored_profile.expires_at - future_expiry).total_seconds()
            ) < 2
            assert restored_profile.created_at is not None
            assert restored_profile.updated_at is not None

            # ---- Assert: eligible task episode returned by retrieval ----
            query = EpisodicMemoryQuery(
                query="report", max_items=10, min_score=0.0, timeout_ms=500
            )
            read_ns = _episode_namespace(
                record_id="rec-bak-any", turn_id="turn-bak-any"
            )
            found = await episodes_repo.read_episodes(read_ns, query)
            found_ids = {ep.episode_id for ep in found}
            assert "ep-bak-approved" in found_ids

            # ---- Assert: ineligible episode NOT returned by eligible retrieval ----
            assert "ep-bak-ineligible" not in found_ids
            # But it still exists in the table (raw SQL check).
            async with pool.connection() as connection:
                cursor = await connection.execute(
                    "SELECT validation_status, retrieval_eligible"
                    " FROM task_episodes"
                    " WHERE tenant_id = %s AND episode_id = %s",
                    ("local", "ep-bak-ineligible"),
                )
                row = await cursor.fetchone()
                assert row is not None
                assert row[0] == "system_generated"
                assert row[1] is False

            # ---- Assert: eligible episode metadata preserved ----
            async with pool.connection() as connection:
                cursor = await connection.execute(
                    "SELECT validation_status, retrieval_eligible, created_at,"
                    " updated_at, expires_at, episode_id"
                    " FROM task_episodes"
                    " WHERE tenant_id = %s AND episode_id = %s",
                    ("local", "ep-bak-approved"),
                )
                row = await cursor.fetchone()
                assert row is not None
                assert row[0] == "user_approved"
                assert row[1] is True
                assert row[4] is None  # expires_at was None

            # ---- Assert: chat summary preserved ----
            async with pool.connection() as connection:
                cursor = await connection.execute(
                    "SELECT tenant_id, user_id, feature, validation_status,"
                    " retrieval_eligible, source_type, created_at, updated_at,"
                    " expires_at"
                    " FROM chat_summary_episodes"
                    " WHERE tenant_id = %s AND episode_id = %s",
                    ("local", "ep-summary-bak"),
                )
                row = await cursor.fetchone()
                assert row is not None
                assert row[0] == "local"
                assert row[1] == "bak-user@example.com"
                assert row[2] == "ai_chat"
                assert row[3] == "system_generated"
                assert row[4] is False
                assert row[5] == "system_generated_chat_summary"
                assert row[8] is None  # expires_at was None

        finally:
            await pool.close()

    _run_scenario(scenario)
    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_backup_restore_preserves_expired_row_exclusion() -> None:
    """An expired profile remains excluded from reads after backup/restore."""
    from scripts.backup_restore_chat_memory import backup_tables, restore_tables

    past_expiry = NOW - timedelta(seconds=5)
    tmp_dir = Path(tempfile.mkdtemp(prefix="chat_exp_"))

    async def scenario() -> None:
        profiles_repo, _, _, pool = await _repositories()
        try:
            # Seed an expired profile.
            await profiles_repo.write_profile(
                _profile_namespace(), _profile(expires_at=past_expiry)
            )
            # Confirm it is excluded before backup.
            assert await profiles_repo.read_profile(_profile_namespace()) is None

            # Backup.
            archive_path = tmp_dir / "chat_memory_expired.dump"
            await backup_tables(DATABASE_URL, archive_path, **_docker_kw())

            # Delete.
            async with pool.connection() as connection:
                await connection.execute(
                    "DELETE FROM chat_profiles WHERE tenant_id = %s",
                    ("local",),
                )

            # Restore.
            await restore_tables(DATABASE_URL, archive_path, **_docker_kw())

            # The expired row must still be excluded by the expiry-aware read.
            assert await profiles_repo.read_profile(_profile_namespace()) is None

            # But the row physically exists in the table.
            async with pool.connection() as connection:
                cursor = await connection.execute(
                    "SELECT count(*) FROM chat_profiles"
                    " WHERE tenant_id = %s",
                    ("local",),
                )
                row = await cursor.fetchone()
                assert row is not None
                assert row[0] == 1  # type: ignore[index]
                cursor = await connection.execute(
                    "SELECT expires_at FROM chat_profiles"
                    " WHERE tenant_id = %s",
                    ("local",),
                )
                row = await cursor.fetchone()
                assert row is not None
                assert row[0] is not None  # expires_at is set
        finally:
            await pool.close()

    _run_scenario(scenario)
    shutil.rmtree(tmp_dir, ignore_errors=True)
