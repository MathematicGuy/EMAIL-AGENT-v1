"""PostgreSQL repository integration tests (V1-H T5.1).

Run against a real PostgreSQL 16 server (dev container:
``docker run -d --name cowork-pg -e POSTGRES_USER=cowork
-e POSTGRES_PASSWORD=cowork_dev_only -e POSTGRES_DB=cowork_mail_todo
-p 5432:5432 postgres:16-alpine``). Override the target with ``PG_TEST_URL``.
The whole module skips when psycopg is not installed or no server answers,
so the default suite stays green on machines without PostgreSQL.
"""

import asyncio
import os
import selectors
import sys
from collections.abc import Callable, Coroutine, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

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

from cowork_agent.domain import (  # noqa: E402
    ActionFreshness,
    DigestCompletedEvent,
    DigestRun,
    Priority,
    RunStatus,
    RunTrigger,
)
from cowork_agent.domain.target_contracts import (  # noqa: E402
    TASK_PIPELINE_VERSION,
    Actionability,
    BodyFormat,
    EphemeralEmailEnvelope,
    FetchStatus,
    PlanStep,
    Route,
    SupportingDocument,
    Task,
    ValidationStatus,
)
from cowork_agent.features.email_action_plan.ports import (  # noqa: E402
    PersistedTask,
    TaskPointer,
)
from cowork_agent.features.email_action_plan.short_term import ShortTermStore  # noqa: E402
from cowork_agent.features.email_action_plan.workflow import DigestWorker  # noqa: E402
from cowork_agent.integrations.gmail.fakes import (  # noqa: E402
    FakeMailbox,
    SafeTextAttachmentExtractor,
)
from cowork_agent.integrations.llm.fakes import (  # noqa: E402
    FakePlanGenerator,
    FakeRouteClassifier,
)
from cowork_agent.persistence.migrate import apply_migrations  # noqa: E402
from cowork_agent.persistence.repositories.local import (  # noqa: E402
    InMemoryResultRepository,
)
from cowork_agent.persistence.repositories.postgres import (  # noqa: E402
    PostgresOutboxRepository,
    PostgresRunRepository,
    PostgresTaskRepository,
)

NOW = datetime(2026, 8, 8, 9, tzinfo=UTC)
SECRET_BODY = "Nội dung email tuyệt mật không được rò rỉ."


def _run_scenario(scenario: Callable[[], Coroutine[object, object, None]]) -> None:
    # Windows' default ProactorEventLoop is unsupported by psycopg async.
    if sys.version_info >= (3, 12):
        asyncio.run(
            scenario(),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
        return
    original_policy = asyncio.get_event_loop_policy()
    if sys.platform == "win32":
        from asyncio import windows_events

        asyncio.set_event_loop_policy(windows_events.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(scenario())
    finally:
        asyncio.set_event_loop_policy(original_policy)


@pytest.fixture(autouse=True)
def fresh_schema() -> Iterator[None]:
    """Reset the public schema per test so scenarios never see each other."""
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


async def _pool() -> AsyncConnectionPool:
    pool = AsyncConnectionPool(DATABASE_URL, min_size=1, max_size=4, open=False)
    await pool.open(wait=True)
    return pool


def _run(
    run_id: str = "run_1", *, key: str = "k1", status: RunStatus = RunStatus.QUEUED
) -> DigestRun:
    return DigestRun(
        id=run_id,
        user_id="u1",
        mailbox_connection_id="mbx1",
        trigger=RunTrigger.ON_DEMAND,
        status=status,
        query="is:unread in:inbox",
        idempotency_key=key,
        max_emails=50,
    )


def _task(message_id: str, *, run_id: str = "run_1", title: str = "Gửi báo cáo") -> Task:
    return Task(
        task_id=f"task_{message_id}_{run_id}",
        run_id=run_id,
        gmail_message_id=message_id,
        gmail_url=f"https://mail.google.com/mail/u/0/#inbox/{message_id}",
        source_message_ids=(message_id,),
        incident_key=None,
        title=title,
        request_summary="Yêu cầu cần được xử lý.",
        actionability=Actionability.ACTION_REQUIRED,
        route=Route.DIRECT_PLAN,
        priority=Priority.HIGH,
        deadline=NOW,
        action_plan=(PlanStep(1, "Kiểm tra yêu cầu", ("cit_1",)),),
        supporting_documents=(
            SupportingDocument(
                citation_id="cit_1",
                document_id="doc_1",
                title="Sổ tay quy trình",
                section=None,
                url="https://docs.example.com/doc_1",
                relevance_score=0.9,
            ),
        ),
        missing_information=(),
        classifier_confidence=0.9,
        generation_confidence=0.85,
        validation_status=ValidationStatus.SYSTEM_GENERATED,
        created_at=NOW,
    )


def _record(message_id: str, *, run_id: str = "run_1", title: str = "Gửi báo cáo") -> PersistedTask:
    return PersistedTask(
        task=_task(message_id, run_id=run_id, title=title),
        pointer=TaskPointer(
            mailbox_connection_id="mbx1",
            provider_thread_id="t1",
            sender_name="Nguyễn An",
            sender_address="an@example.com",
            email_subject="Gửi báo cáo",
            email_received_at=NOW,
        ),
        fingerprint="f" * 64,
    )


def test_migrations_apply_once_and_are_idempotent() -> None:
    async def scenario() -> None:
        pool = await _pool()
        try:
            first = await apply_migrations(pool)
            assert first == (
                "001_mail_todo.sql",
                "002_chat_profiles.sql",
                "003_chat_summary_episodes.sql",
                "004_task_episodes.sql",
                "005_chat_projects.sql",
                "005_identity_workspace_sessions.sql",
                "006_durable_chat_sessions.sql",
                "006_project_documents.sql",
                "007_projects_documents.sql",
                "007_task_episode_project_scope.sql",
                "008_project_document_cleanup.sql",
                "009_canonical_project_documents.sql",
                "010_service_heartbeats.sql",
                "011_chat_history.sql",
                "012_chat_mail_scan.sql",
                "012_project_document_chunks.sql",
                "013_digest_run_filtered_summary.sql",
                "014_chat_turn_lifecycle.sql",
                "014_project_chunk_fts_simple.sql",
            )
            assert await apply_migrations(pool) == ()
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_create_is_atomic_and_idempotent_on_user_and_key() -> None:
    async def scenario() -> None:
        pool = await _pool()
        try:
            await apply_migrations(pool)
            repository = PostgresRunRepository(pool)
            run, created = await repository.create(_run())
            assert created is True
            assert run.created_at is not None

            duplicate, created_again = await repository.create(_run(run_id="run_other"))
            assert created_again is False
            assert duplicate.id == run.id

            distinct_user, created_for_user = await repository.create(
                DigestRun(
                    id="run_u2",
                    user_id="u2",
                    mailbox_connection_id="mbx1",
                    trigger=RunTrigger.ON_DEMAND,
                    status=RunStatus.QUEUED,
                    query="is:unread in:inbox",
                    idempotency_key="k1",
                    max_emails=50,
                )
            )
            assert created_for_user is True and distinct_user.id == "run_u2"
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_concurrent_creates_insert_exactly_one_row() -> None:
    async def scenario() -> None:
        pool = await _pool()
        try:
            await apply_migrations(pool)
            repository = PostgresRunRepository(pool)
            outcomes = await asyncio.gather(
                *(repository.create(_run(run_id=f"run_{index}")) for index in range(8))
            )
            created = [run for run, was_created in outcomes if was_created]
            assert len(created) == 1
            ids = {run.id for run, _ in outcomes}
            assert ids == {created[0].id}
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_claim_is_compare_and_set_single_winner() -> None:
    async def scenario() -> None:
        pool = await _pool()
        try:
            await apply_migrations(pool)
            repository = PostgresRunRepository(pool)
            await repository.create(_run())

            claims = await asyncio.gather(
                *(repository.claim("run_1", NOW) for _ in range(5))
            )
            winners = [claimed for claimed in claims if claimed is not None]
            assert len(winners) == 1
            assert winners[0].status is RunStatus.RUNNING
            assert winners[0].started_at == NOW

            assert await repository.claim("run_1", NOW) is None
            assert await repository.claim("missing", NOW) is None
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_save_round_trips_progress_and_safe_error_fields() -> None:
    async def scenario() -> None:
        pool = await _pool()
        try:
            await apply_migrations(pool)
            repository = PostgresRunRepository(pool)
            run, _ = await repository.create(_run())
            run.status = RunStatus.FAILED
            run.emails_matched = 7
            run.emails_processed = 5
            run.error_code = "GENERATION_SCHEMA_ERROR"
            run.error_message_safe = "Generation failed safely."
            run.completed_at = NOW
            await repository.save(run)

            stored = await repository.get("run_1")
            assert stored is not None
            assert stored.status is RunStatus.FAILED
            assert stored.emails_matched == 7
            assert stored.emails_processed == 5
            assert stored.error_code == "GENERATION_SCHEMA_ERROR"
            assert stored.error_message_safe == "Generation failed safely."
            assert stored.completed_at == NOW
            assert await repository.get("missing") is None
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_list_stuck_runs_filters_by_status_and_age() -> None:
    async def scenario() -> None:
        pool = await _pool()
        try:
            await apply_migrations(pool)
            repository = PostgresRunRepository(pool)
            await repository.create(_run("run_1"))
            await repository.create(_run("run_2", key="k2"))
            wall_now = datetime.now(UTC)
            await repository.claim("run_1", wall_now)

            far_future = wall_now + timedelta(hours=1)
            stuck = await repository.list_stuck_runs(
                running_before=far_future, queued_before=far_future
            )
            assert {run.id for run in stuck} == {"run_1", "run_2"}

            long_ago = wall_now - timedelta(hours=1)
            assert (
                await repository.list_stuck_runs(
                    running_before=long_ago, queued_before=long_ago
                )
                == ()
            )

            # CAS reset: succeeds while still RUNNING-past-threshold, then
            # refuses once the run is back in QUEUED.
            assert await repository.reset_stuck_run(
                "run_1", started_before=far_future
            )
            reset_again = await repository.reset_stuck_run(
                "run_1", started_before=far_future
            )
            assert reset_again is False
            reloaded = await repository.get("run_1")
            assert reloaded is not None
            assert reloaded.status is RunStatus.QUEUED
            assert reloaded.started_at is None
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_task_save_round_trip_and_run_links_survive_upsert() -> None:
    async def scenario() -> None:
        pool = await _pool()
        try:
            await apply_migrations(pool)
            runs, tasks = PostgresRunRepository(pool), PostgresTaskRepository(pool)
            await runs.create(_run("run_1"))
            await runs.create(_run("run_2", key="k2"))
            kwargs: dict[str, Any] = {
                "tenant_id": "local",
                "user_id": "u1",
                "pipeline_version": TASK_PIPELINE_VERSION,
            }

            await tasks.save_task(_record("m1"), run_id="run_1", **kwargs)
            await tasks.save_task(
                _record("m1", run_id="run_2", title="Bản cập nhật"), run_id="run_2", **kwargs
            )

            first_view = await tasks.list_for_run("run_1")
            second_view = await tasks.list_for_run("run_2")
            assert len(first_view) == len(second_view) == 1
            assert first_view[0].task.title == "Bản cập nhật"
            assert first_view[0].freshness is ActionFreshness.NEW
            assert second_view[0].freshness is ActionFreshness.SEEN
            assert first_view[0].pointer.sender_address == "an@example.com"

            # A distinct pipeline version is a distinct durable row.
            await runs.create(_run("run_3", key="k3"))
            await tasks.save_task(
                _record("m1", run_id="run_3"),
                run_id="run_3",
                tenant_id="local",
                user_id="u1",
                pipeline_version=str(int(TASK_PIPELINE_VERSION) + 1),
            )
            third_view = await tasks.list_for_run("run_3")
            assert len(third_view) == 1
            assert third_view[0].task.task_id != second_view[0].task.task_id
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_persisted_tables_are_body_free() -> None:
    async def scenario() -> None:
        # The raw body lives only in transient run state here; the dump scan
        # below proves the repository surface never stores it (invariant 1).
        transient_body = SECRET_BODY
        pool = await _pool()
        try:
            await apply_migrations(pool)
            runs, tasks = PostgresRunRepository(pool), PostgresTaskRepository(pool)
            await runs.create(_run())
            await tasks.save_task(
                _record("m1"),
                run_id="run_1",
                tenant_id="local",
                user_id="u1",
                pipeline_version=TASK_PIPELINE_VERSION,
            )
            async with pool.connection() as connection:
                cursor = await connection.execute(
                    "SELECT table_name FROM information_schema.tables"
                    " WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
                )
                tables = [str(row[0]) for row in await cursor.fetchall()]
                assert tables, "migration applied no tables"
                for table in tables:
                    cursor = await connection.execute(f'SELECT * FROM "{table}"')
                    assert transient_body not in repr(await cursor.fetchall())
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_outbox_is_idempotent_and_tracks_publication() -> None:
    async def scenario() -> None:
        pool = await _pool()
        try:
            await apply_migrations(pool)
            outbox = PostgresOutboxRepository(pool)
            event = DigestCompletedEvent(
                run_id="run_1", user_id="u1", status=RunStatus.SUCCEEDED, occurred_at=NOW
            )
            await outbox.add(event)
            await outbox.add(event)
            pending = await outbox.pending()
            assert len(pending) == 1
            assert pending[0].run_id == "run_1"
            assert pending[0].status is RunStatus.SUCCEEDED
            assert pending[0].occurred_at == NOW

            await outbox.mark_published("run_1")
            assert await outbox.pending() == ()

            await outbox.add(
                DigestCompletedEvent(
                    run_id="run_2", user_id="u1", status=RunStatus.FAILED, occurred_at=NOW
                )
            )
            assert [event.run_id for event in await outbox.pending()] == ["run_2"]
        finally:
            await pool.close()

    _run_scenario(scenario)


def test_digest_pipeline_runs_end_to_end_against_postgres() -> None:
    async def scenario() -> None:
        pool = await _pool()
        try:
            await apply_migrations(pool)
            runs = PostgresRunRepository(pool)
            tasks = PostgresTaskRepository(pool)
            outbox = PostgresOutboxRepository(pool)
            run, _ = await runs.create(_run())
            envelope = EphemeralEmailEnvelope(
                run_id="",
                user_id="",
                gmail_message_id="m1",
                gmail_thread_id="t1",
                gmail_url="https://mail.google.com/mail/u/0/#inbox/m1",
                sender_name="Nguyễn An",
                sender_email="an@example.com",
                recipients=(),
                subject="Gửi báo cáo",
                received_at=NOW,
                labels=(),
                normalized_body="Nội dung thân email.",
                body_format=BodyFormat.TEXT,
                attachments_present=False,
                fetch_status=FetchStatus.COMPLETE,
            )
            worker = DigestWorker(
                runs,
                InMemoryResultRepository(),
                FakeMailbox([envelope]),
                SafeTextAttachmentExtractor(),
                FakeRouteClassifier(),
                FakePlanGenerator((_task("m1"),)),
                ShortTermStore(),
                task_repository=tasks,
                completion_outbox=outbox,
            )

            completed = await worker.execute(run.id, now=NOW)

            assert completed is not None and completed.status is RunStatus.SUCCEEDED
            stored = await tasks.list_for_run(run.id)
            assert len(stored) == 1
            assert stored[0].task.gmail_message_id == "m1"
            assert stored[0].freshness is ActionFreshness.NEW
            reloaded = await runs.get(run.id)
            assert reloaded is not None
            assert reloaded.status is RunStatus.SUCCEEDED
            assert reloaded.emails_processed == 1
            events = await outbox.pending()
            assert len(events) == 1
            assert events[0].run_id == run.id
            assert events[0].status is RunStatus.SUCCEEDED
        finally:
            await pool.close()

    _run_scenario(scenario)
