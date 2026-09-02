"""Real-PostgreSQL TaskEpisode durability and isolation tests (V2-M3.4a)."""

import asyncio
import os
import selectors
from collections.abc import Callable, Coroutine, Iterator
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.integration.persistence.pg_probe import server_available

DATABASE_URL = os.getenv(
    "PG_TEST_URL", "postgresql://cowork:cowork_dev_only@127.0.0.1:5432/cowork_mail_todo"
)

try:
    import psycopg
    from psycopg_pool import AsyncConnectionPool
except ImportError:  # pragma: no cover - execution environment guard
    pytest.skip("psycopg not installed (pip install '.[postgres]')", allow_module_level=True)

from cowork_agent.domain.chat_contracts import (  # noqa: E402
    ChatMemoryScope,
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
    PostgresTaskEpisodeRepository,
)

NOW = datetime(2026, 8, 11, 9, tzinfo=UTC)


def _run_scenario(scenario: Callable[[], Coroutine[object, object, None]]) -> None:
    asyncio.run(
        scenario(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
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


async def _repository() -> tuple[PostgresTaskEpisodeRepository, AsyncConnectionPool]:
    pool = AsyncConnectionPool(DATABASE_URL, min_size=1, max_size=4, open=False)
    await pool.open(wait=True)
    await apply_migrations(pool)
    return PostgresTaskEpisodeRepository(pool), pool


def _namespace(
    *,
    tenant_id: str = "tenant-1",
    user_id: str = "user@example.com",
    session_id: str = "session-1",
    record_id: str = "record-1",
    turn_id: str = "turn-1",
) -> MemoryNamespace:
    return MemoryNamespace(
        scope=ChatMemoryScope(tenant_id=tenant_id, user_id=user_id, session_id=session_id),
        memory_type=MemoryType.EPISODIC,
        record_id=record_id,
        source_id=turn_id,
    )


def _episode(
    *,
    episode_id: str = "episode-1",
    record_id: str = "record-1",
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
        retrieval_eligible=status in {ValidationStatus.USER_APPROVED, ValidationStatus.COMPLETED},
        source_type=EpisodeSourceType.SYSTEM_GENERATED_CHAT_TASK,
        created_at=created_at,
        updated_at=updated_at,
        pipeline_version="2",
        model_id="model-1",
        prompt_version="prompt-1",
        confidence=0.8,
    )


def _transition(
    namespace: MemoryNamespace,
    *,
    episode_id: str = "episode-1",
    from_status: ValidationStatus = ValidationStatus.SYSTEM_GENERATED,
    to_status: ValidationStatus = ValidationStatus.USER_APPROVED,
) -> EpisodeTransition:
    return EpisodeTransition(
        episode_id=episode_id,
        namespace=namespace,
        from_status=from_status,
        to_status=to_status,
        retrieval_eligible=to_status
        in {ValidationStatus.USER_APPROVED, ValidationStatus.COMPLETED},
        transitioned_at=NOW + timedelta(minutes=2),
    )


def test_write_is_retry_safe_and_rejects_namespace_or_stale_payload_updates() -> None:
    async def scenario() -> None:
        repository, pool = await _repository()
        try:
            first = await repository.write_task_episode(_namespace(), _episode(), expires_at=None)
            newer = await repository.write_task_episode(
                _namespace(),
                replace(
                    _episode(), task_title="Updated report", updated_at=NOW + timedelta(minutes=1)
                ),
                expires_at=NOW + timedelta(days=1),
            )
            stale = await repository.write_task_episode(_namespace(), _episode(), expires_at=None)
            assert first.episode_id == newer.episode_id == stale.episode_id == "episode-1"
            assert first.record_id == newer.record_id == stale.record_id == "record-1"
            assert newer.created_at == stale.created_at == NOW
            assert stale.task_title == "Updated report"
            assert stale.validation_status is ValidationStatus.SYSTEM_GENERATED
            with pytest.raises(ValueError, match="namespace"):
                await repository.write_task_episode(
                    _namespace(user_id="other@example.com"), _episode(), expires_at=None
                )
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_transition_and_exact_deletion_require_full_scoped_identity() -> None:
    async def scenario() -> None:
        repository, pool = await _repository()
        try:
            await repository.write_task_episode(_namespace(), _episode(), expires_at=None)
            assert (
                await repository.transition_task_episode(
                    _transition(_namespace(session_id="other-session"))
                )
                is None
            )
            approved = await repository.transition_task_episode(_transition(_namespace()))
            assert approved is not None
            assert approved.validation_status is ValidationStatus.USER_APPROVED
            assert approved.retrieval_eligible is True
            assert await repository.transition_task_episode(_transition(_namespace())) is None
            assert not await repository.delete_task_episode(
                _namespace(session_id="other-session"), episode_id="episode-1"
            )
            assert await repository.delete_task_episode(_namespace(), episode_id="episode-1")
            assert not await repository.delete_task_episode(_namespace(), episode_id="episode-1")
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_retrieval_is_bounded_cross_session_and_excludes_ineligible_or_expired_rows() -> None:
    async def scenario() -> None:
        repository, pool = await _repository()
        try:
            expired_at = datetime.now(UTC) - timedelta(seconds=1)
            first = _episode(record_id="record-1", episode_id="episode-1")
            second = _episode(
                record_id="record-2",
                episode_id="episode-2",
                session_id="session-2",
                turn_id="turn-2",
                updated_at=NOW + timedelta(minutes=1),
            )
            expired = _episode(
                record_id="record-3",
                episode_id="episode-3",
                turn_id="turn-3",
                created_at=expired_at - timedelta(seconds=1),
                updated_at=expired_at - timedelta(seconds=1),
            )
            for namespace, episode, expiry in (
                (_namespace(), first, None),
                (
                    _namespace(session_id="session-2", record_id="record-2", turn_id="turn-2"),
                    second,
                    None,
                ),
                (_namespace(record_id="record-3", turn_id="turn-3"), expired, expired_at),
            ):
                await repository.write_task_episode(namespace, episode, expires_at=expiry)
                await repository.transition_task_episode(
                    _transition(
                        namespace,
                        episode_id=episode.episode_id,
                        to_status=ValidationStatus.USER_APPROVED,
                    )
                )
            query = EpisodicMemoryQuery(query="report", max_items=1, min_score=0.0, timeout_ms=100)
            found = await repository.read_episodes(_namespace(session_id="new-session"), query)
            assert [episode.episode_id for episode in found] == ["episode-2"]
            assert (
                await repository.read_episodes(
                    _namespace(user_id="other@example.com", session_id="new-session"), query
                )
                == ()
            )
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_retrieval_uses_query_relevance_and_min_score_not_recentness() -> None:
    async def scenario() -> None:
        repository, pool = await _repository()
        try:
            high_namespace = _namespace(session_id="session-high", record_id="high", turn_id="high")
            low_namespace = _namespace(record_id="low", turn_id="low")
            unrelated_namespace = _namespace(
                session_id="session-new", record_id="unrelated", turn_id="unrelated"
            )
            high = replace(
                _episode(
                    episode_id="episode-high",
                    record_id="high",
                    session_id="session-high",
                    turn_id="high",
                ),
                task_title="Nebula nebula nebula plan",
                minimal_request_paraphrase="Create the nebula delivery plan.",
                action_plan=("Prepare nebula milestones.",),
                missing_information=("Nebula owner is not stated.",),
            )
            low = replace(
                _episode(episode_id="episode-low", record_id="low", turn_id="low"),
                task_title="Delivery plan",
                minimal_request_paraphrase="Nebula.",
                action_plan=("Prepare milestones.",),
                missing_information=("Owner is not stated.",),
            )
            unrelated = replace(
                _episode(
                    episode_id="episode-unrelated",
                    record_id="unrelated",
                    session_id="session-new",
                    turn_id="unrelated",
                ),
                task_title="Holiday commute plan",
                minimal_request_paraphrase="Create a transit plan.",
                action_plan=("Check train schedules.",),
                missing_information=("Departure time is not stated.",),
            )
            for namespace, episode, transitioned_at in (
                (high_namespace, high, NOW + timedelta(minutes=2)),
                (low_namespace, low, NOW + timedelta(minutes=3)),
                (unrelated_namespace, unrelated, NOW + timedelta(minutes=4)),
            ):
                await repository.write_task_episode(namespace, episode, expires_at=None)
                transitioned = EpisodeTransition(
                    episode_id=episode.episode_id,
                    namespace=namespace,
                    from_status=ValidationStatus.SYSTEM_GENERATED,
                    to_status=ValidationStatus.USER_APPROVED,
                    retrieval_eligible=True,
                    transitioned_at=transitioned_at,
                )
                assert await repository.transition_task_episode(transitioned) is not None

            query = EpisodicMemoryQuery(query="nebula", max_items=10, min_score=0.0, timeout_ms=100)
            matches = await repository.read_episodes(_namespace(session_id="new-session"), query)
            assert [episode.episode_id for episode in matches] == ["episode-high", "episode-low"]
            assert matches[0].chat_session_id == "session-high"

            async with pool.connection() as connection:
                cursor = await connection.execute(
                    "SELECT record_id,"
                    " ts_rank_cd(search_vector, plainto_tsquery('simple', 'nebula'), 32)"
                    " FROM task_episodes WHERE record_id IN ('high', 'low')"
                )
                rows = await cursor.fetchall()
                scores = {str(record_id): float(score) for record_id, score in rows}
            assert scores["high"] > scores["low"] > 0
            threshold = (scores["high"] + scores["low"]) / 2
            filtered = await repository.read_episodes(
                _namespace(session_id="another-session"),
                EpisodicMemoryQuery(
                    query="nebula", max_items=10, min_score=threshold, timeout_ms=100
                ),
            )
            assert [episode.episode_id for episode in filtered] == ["episode-high"]
            limited = await repository.read_episodes(
                _namespace(session_id="another-session"),
                EpisodicMemoryQuery(query="nebula", max_items=1, min_score=0.0, timeout_ms=100),
            )
            assert [episode.episode_id for episode in limited] == ["episode-high"]
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_generated_eligibility_expiry_purge_and_user_deletion_preserve_foreign_rows() -> None:
    async def scenario() -> None:
        repository, pool = await _repository()
        try:
            own = _namespace()
            other_user = _namespace(
                user_id="other@example.com", record_id="other-user", turn_id="other-user"
            )
            await repository.write_task_episode(
                own, _episode(), expires_at=NOW + timedelta(seconds=1)
            )
            await repository.write_task_episode(
                other_user,
                _episode(
                    episode_id="episode-user",
                    record_id="other-user",
                    user_id="other@example.com",
                    turn_id="other-user",
                ),
                expires_at=None,
            )

            async with pool.connection() as connection:
                await connection.execute("CREATE TABLE semantic_rag_sentinel (value text NOT NULL)")
                await connection.execute("INSERT INTO semantic_rag_sentinel VALUES ('preserve')")
            assert await repository.purge_expired(NOW + timedelta(seconds=2)) == 1
            assert await repository.delete_all_for_user(own) == 0
            async with pool.connection() as connection:
                cursor = await connection.execute(
                    "SELECT tenant_id, user_id, retrieval_eligible FROM task_episodes"
                    " ORDER BY tenant_id, user_id"
                )
                assert await cursor.fetchall() == [("tenant-1", "other@example.com", False)]
                sentinel = await connection.execute("SELECT value FROM semantic_rag_sentinel")
                assert await sentinel.fetchall() == [("preserve",)]
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_malicious_payload_shape_is_rejected_before_persistence_and_identifiers_are_bound() -> None:
    async def scenario() -> None:
        repository, pool = await _repository()
        try:
            malicious_tenant = "tenant'; DELETE FROM task_episodes; --"
            source = _episode(user_id="user'; DELETE FROM task_episodes; --")
            namespace = _namespace(tenant_id=malicious_tenant, user_id=source.user_id)
            malicious = object.__new__(TaskEpisode)
            for field in fields(TaskEpisode):
                object.__setattr__(malicious, field.name, getattr(source, field.name))
            object.__setattr__(malicious, "rag_citations", ({"tool_payload": "forbidden"},))
            with pytest.raises(ValueError, match="tool payload"):
                await repository.write_task_episode(namespace, malicious, expires_at=None)
            async with pool.connection() as connection:
                cursor = await connection.execute("SELECT count(*) FROM task_episodes")
                assert (await cursor.fetchone())[0] == 0  # type: ignore[index]
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_write_rejects_all_immutable_identity_guard_conflicts_without_changing_the_row() -> None:
    async def scenario() -> None:
        repository, pool = await _repository()
        try:
            original = _episode()
            await repository.write_task_episode(_namespace(), original, expires_at=None)
            conflicts = (
                (
                    _namespace(record_id="other-record"),
                    _episode(record_id="other-record"),
                ),
                (
                    _namespace(),
                    _episode(episode_id="other-episode"),
                ),
                (
                    _namespace(turn_id="other-turn"),
                    _episode(turn_id="other-turn"),
                ),
            )
            for namespace, conflict in conflicts:
                with pytest.raises(ValueError, match="immutable identity"):
                    await repository.write_task_episode(namespace, conflict, expires_at=None)
            async with pool.connection() as connection:
                cursor = await connection.execute(
                    "SELECT record_id, episode_id, chat_turn_id FROM task_episodes"
                )
                assert await cursor.fetchall() == [("record-1", "episode-1", "turn-1")]
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_transition_never_moves_lifecycle_time_backwards_and_uses_generated_eligibility() -> None:
    async def scenario() -> None:
        repository, pool = await _repository()
        try:
            outcomes: list[tuple[ValidationStatus, bool]] = []
            for status, record_id, turn_id in (
                (ValidationStatus.USER_APPROVED, "approved", "approved"),
                (ValidationStatus.COMPLETED, "completed", "completed"),
                (ValidationStatus.REJECTED, "rejected", "rejected"),
            ):
                namespace = _namespace(record_id=record_id, turn_id=turn_id)
                episode = _episode(
                    episode_id=f"episode-{record_id}",
                    record_id=record_id,
                    turn_id=turn_id,
                )
                await repository.write_task_episode(namespace, episode, expires_at=None)
                transitioned = await repository.transition_task_episode(
                    _transition(
                        namespace,
                        episode_id=episode.episode_id,
                        to_status=status,
                    )
                )
                assert transitioned is not None
                outcomes.append((transitioned.validation_status, transitioned.retrieval_eligible))
            assert outcomes == [
                (ValidationStatus.USER_APPROVED, True),
                (ValidationStatus.COMPLETED, True),
                (ValidationStatus.REJECTED, False),
            ]
            stale = EpisodeTransition(
                episode_id="episode-approved",
                namespace=_namespace(record_id="approved", turn_id="approved"),
                from_status=ValidationStatus.USER_APPROVED,
                to_status=ValidationStatus.COMPLETED,
                retrieval_eligible=True,
                transitioned_at=NOW + timedelta(minutes=1),
            )
            assert await repository.transition_task_episode(stale) is None
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_transition_and_deletion_are_isolated_by_every_mutation_guard() -> None:
    async def scenario() -> None:
        repository, pool = await _repository()
        try:
            await repository.write_task_episode(_namespace(), _episode(), expires_at=None)
            namespaces = (
                _namespace(user_id="other@example.com"),
                _namespace(session_id="other-session"),
                _namespace(record_id="other-record"),
                _namespace(turn_id="other-turn"),
            )
            for namespace in namespaces:
                assert await repository.transition_task_episode(_transition(namespace)) is None
                assert not await repository.delete_task_episode(namespace, episode_id="episode-1")
            assert (
                await repository.transition_task_episode(
                    _transition(_namespace(), episode_id="other-episode")
                )
                is None
            )
            assert not await repository.delete_task_episode(
                _namespace(), episode_id="other-episode"
            )
            async with pool.connection() as connection:
                cursor = await connection.execute(
                    "SELECT validation_status FROM task_episodes WHERE record_id = 'record-1'"
                )
                assert await cursor.fetchall() == [("system_generated",)]
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_generated_eligibility_tampering_is_refused_and_down_migration_rolls_back() -> None:
    async def scenario() -> None:
        repository, pool = await _repository()
        try:
            await repository.write_task_episode(_namespace(), _episode(), expires_at=None)
            async with pool.connection() as connection:
                with pytest.raises(psycopg.errors.GeneratedAlways):
                    await connection.execute(
                        "INSERT INTO task_episodes (tenant_id, user_id, feature, chat_session_id,"
                        " record_id, episode_id, chat_turn_id, creation_reason, task_title,"
                        " minimal_request_paraphrase, action_plan, rag_citations,"
                        " missing_information, validation_status, retrieval_eligible,"
                        " source_type, created_at, updated_at,"
                        " pipeline_version) SELECT tenant_id, user_id, feature, chat_session_id,"
                        " 'tamper-record', 'tamper-episode', 'tamper-turn',"
                        " creation_reason, task_title, minimal_request_paraphrase,"
                        " action_plan, rag_citations, missing_information, validation_status,"
                        " true, source_type, created_at, updated_at, pipeline_version"
                        " FROM task_episodes WHERE record_id = 'record-1'"
                    )
            async with pool.connection() as connection:
                await connection.execute(
                    (
                        Path(__file__).resolve().parents[3]
                        / "src"
                        / "cowork_agent"
                        / "persistence"
                        / "migrations"
                        / "004_task_episodes.down.sql"
                    ).read_text(encoding="utf-8")
                )
                cursor = await connection.execute("SELECT to_regclass('public.task_episodes')")
                assert await cursor.fetchone() == (None,)
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_storage_rejects_extra_rag_citation_keys_and_keeps_exact_shape_rows() -> None:
    async def scenario() -> None:
        repository, pool = await _repository()
        try:
            await repository.write_task_episode(_namespace(), _episode(), expires_at=None)
            async with pool.connection() as connection:
                with pytest.raises(psycopg.errors.CheckViolation):
                    await connection.execute(
                        "INSERT INTO task_episodes (tenant_id, user_id, feature, chat_session_id,"
                        " record_id, episode_id, chat_turn_id, creation_reason, task_title,"
                        " minimal_request_paraphrase, action_plan, rag_citations,"
                        " missing_information, validation_status, source_type,"
                        " created_at, updated_at, pipeline_version)"
                        " SELECT tenant_id, user_id, feature, chat_session_id, 'extra-record',"
                        " 'extra-episode', 'extra-turn', creation_reason, task_title,"
                        " minimal_request_paraphrase, action_plan,"
                        " jsonb_set(rag_citations, '{0,tool_payload}', '\"forbidden\"'),"
                        " missing_information, validation_status, source_type,"
                        " created_at, updated_at, pipeline_version"
                        " FROM task_episodes WHERE record_id = 'record-1'"
                    )
            async with pool.connection() as connection:
                cursor = await connection.execute(
                    "SELECT record_id FROM task_episodes ORDER BY record_id"
                )
                assert await cursor.fetchall() == [("record-1",)]
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_list_episodes_returns_every_non_expired_status_newest_first() -> None:
    async def scenario() -> None:
        repository, pool = await _repository()
        try:
            await repository.write_task_episode(
                _namespace(record_id="rec-old", turn_id="turn-old"),
                _episode(episode_id="ep-old", record_id="rec-old", turn_id="turn-old"),
                expires_at=None,
            )
            await repository.write_task_episode(
                _namespace(record_id="rec-new", turn_id="turn-new"),
                _episode(
                    episode_id="ep-new",
                    record_id="rec-new",
                    turn_id="turn-new",
                    created_at=NOW + timedelta(minutes=5),
                    updated_at=NOW + timedelta(minutes=5),
                ),
                expires_at=None,
            )
            await repository.transition_task_episode(
                EpisodeTransition(
                    episode_id="ep-new",
                    namespace=_namespace(record_id="rec-new", turn_id="turn-new"),
                    from_status=ValidationStatus.SYSTEM_GENERATED,
                    to_status=ValidationStatus.USER_APPROVED,
                    retrieval_eligible=True,
                    transitioned_at=NOW + timedelta(minutes=6),
                )
            )
            await repository.write_task_episode(
                _namespace(record_id="rec-expired", turn_id="turn-expired"),
                _episode(episode_id="ep-expired", record_id="rec-expired", turn_id="turn-expired"),
                expires_at=NOW + timedelta(hours=1),
            )
            await repository.write_task_episode(
                _namespace(
                    user_id="other@example.com", record_id="rec-foreign", turn_id="turn-foreign"
                ),
                _episode(
                    episode_id="ep-foreign",
                    record_id="rec-foreign",
                    turn_id="turn-foreign",
                    user_id="other@example.com",
                ),
                expires_at=None,
            )

            listed = await repository.list_episodes(_namespace())
        finally:
            await pool.close()

        assert [episode.episode_id for episode in listed] == ["ep-new", "ep-old"]
        assert [episode.validation_status for episode in listed] == [
            ValidationStatus.USER_APPROVED,
            ValidationStatus.SYSTEM_GENERATED,
        ]

    _run_scenario(scenario)


def test_a_multi_word_search_matches_an_episode_that_holds_only_some_of_the_words() -> None:
    """The search terms are ORed, and `min_score` is what decides relevance.

    They used to be ANDed by `plainto_tsquery`, so an episode had to contain
    every term of the search text to be a candidate at all. Nothing reached the
    score, and episodic retrieval returned nothing to anyone.
    """

    async def scenario() -> None:
        repository, pool = await _repository()
        try:
            wanted_namespace = _namespace(
                session_id="session-wanted", record_id="wanted", turn_id="wanted"
            )
            wanted = replace(
                _episode(
                    episode_id="episode-wanted",
                    record_id="wanted",
                    turn_id="wanted",
                    session_id="session-wanted",
                ),
                task_title="Renew the identity card",
                minimal_request_paraphrase="Renew the identity card for the branch office.",
                action_plan=("Collect the identity documents.",),
                missing_information=("The case number is not stated.",),
            )
            other_namespace = _namespace(
                session_id="session-other", record_id="other", turn_id="other"
            )
            other = replace(
                _episode(
                    episode_id="episode-other",
                    record_id="other",
                    turn_id="other",
                    session_id="session-other",
                ),
                task_title="Book the quarterly meeting",
                minimal_request_paraphrase="Book a quarterly meeting with finance.",
                action_plan=("Check the shared calendar.",),
                missing_information=(),
            )
            for namespace, episode in ((wanted_namespace, wanted), (other_namespace, other)):
                await repository.write_task_episode(namespace, episode, expires_at=None)
                assert (
                    await repository.transition_task_episode(
                        _transition(
                            namespace,
                            episode_id=episode.episode_id,
                            to_status=ValidationStatus.USER_APPROVED,
                        )
                    )
                    is not None
                )

            # "case" and "number" are in the wanted episode; "renew" and
            # "identity" are too; "passport" is in neither. Under AND this
            # matched nothing.
            found = await repository.read_episodes(
                _namespace(session_id="new-session"),
                EpisodicMemoryQuery(
                    query="renew identity card passport case number",
                    max_items=10,
                    min_score=0.0,
                    timeout_ms=500,
                ),
            )
            assert [episode.episode_id for episode in found] == ["episode-wanted"]

            # A search text with no lexemes at all selects nothing rather than
            # everything: the expansion yields NULL and `@@ NULL` is not true.
            assert (
                await repository.read_episodes(
                    _namespace(session_id="new-session"),
                    EpisodicMemoryQuery(query="- - -", max_items=10, min_score=0.0, timeout_ms=500),
                )
                == ()
            )

            # A term that is only tsquery punctuation is data, not syntax.
            escaped = await repository.read_episodes(
                _namespace(session_id="new-session"),
                EpisodicMemoryQuery(
                    query="renew' | 'x", max_items=10, min_score=0.0, timeout_ms=500
                ),
            )
            assert [episode.episode_id for episode in escaped] == ["episode-wanted"]
        finally:
            await pool.close()

    _run_scenario(scenario)
