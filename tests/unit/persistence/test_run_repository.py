import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cowork_agent.domain import DigestRun, RunStatus, RunTrigger
from cowork_agent.persistence.repositories.runs import SQLiteRunRepository


def _run(
    run_id: str,
    *,
    user_id: str = "user-1",
    mailbox_connection_id: str = "mailbox-1",
    idempotency_key: str | None = None,
    created_at: datetime | None = None,
) -> DigestRun:
    return DigestRun(
        id=run_id,
        user_id=user_id,
        mailbox_connection_id=mailbox_connection_id,
        trigger=RunTrigger.ON_DEMAND,
        status=RunStatus.QUEUED,
        query="is:unread",
        idempotency_key=idempotency_key or f"idem-{run_id}",
        max_emails=20,
        created_at=created_at or datetime.now(UTC),
    )


def test_sqlite_run_repository_persists_and_preserves_idempotency(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        path = tmp_path / "runs.db"
        first = SQLiteRunRepository(path)
        await first.initialize()

        created, was_created = await first.create(_run("run-1", idempotency_key="same"))
        duplicate, duplicate_created = await first.create(
            _run("run-2", idempotency_key="same")
        )
        assert was_created is True
        assert duplicate_created is False
        assert duplicate.id == created.id == "run-1"

        restarted = SQLiteRunRepository(path)
        await restarted.initialize()
        persisted = await restarted.get("run-1")
        assert persisted is not None
        assert persisted.status is RunStatus.QUEUED
        assert persisted.max_emails == 20

    asyncio.run(scenario())


def test_sqlite_run_repository_claim_is_atomic_and_save_survives_restart(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        path = tmp_path / "runs.db"
        repository = SQLiteRunRepository(path)
        await repository.initialize()
        await repository.create(_run("run-claim"))

        started_at = datetime.now(UTC)
        claims = await asyncio.gather(
            repository.claim("run-claim", started_at),
            repository.claim("run-claim", started_at),
        )
        assert sum(claim is not None for claim in claims) == 1

        claimed = next(claim for claim in claims if claim is not None)
        claimed.status = RunStatus.SUCCEEDED
        claimed.emails_matched = 4
        claimed.emails_processed = 4
        claimed.action_items_count = 2
        claimed.completed_at = datetime.now(UTC)
        await repository.save(claimed)

        restarted = SQLiteRunRepository(path)
        await restarted.initialize()
        saved = await restarted.get("run-claim")
        assert saved is not None
        assert saved.status is RunStatus.SUCCEEDED
        assert saved.emails_processed == 4
        assert saved.action_items_count == 2

    asyncio.run(scenario())


def test_sqlite_run_repository_lists_recent_runs_for_one_mailbox(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        repository = SQLiteRunRepository(tmp_path / "runs.db")
        await repository.initialize()
        now = datetime.now(UTC)
        await repository.create(_run("run-old", created_at=now - timedelta(minutes=2)))
        await repository.create(_run("run-new", created_at=now - timedelta(minutes=1)))
        await repository.create(
            _run("run-other-user", user_id="user-2", created_at=now)
        )
        await repository.create(
            _run("run-other-mailbox", mailbox_connection_id="mailbox-2", created_at=now)
        )

        recent = await repository.list_recent(
            user_id="user-1", mailbox_connection_id="mailbox-1", limit=1
        )
        assert [run.id for run in recent] == ["run-new"]

    asyncio.run(scenario())


def test_sqlite_run_repository_recovers_only_stale_running_runs(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SQLiteRunRepository(tmp_path / "runs.db")
        await repository.initialize()
        now = datetime.now(UTC)
        await repository.create(_run("stale", created_at=now - timedelta(hours=2)))
        await repository.create(_run("fresh", created_at=now))
        await repository.claim("stale", now - timedelta(hours=2))
        await repository.claim("fresh", now)

        stuck = await repository.list_stuck_runs(
            running_before=now - timedelta(hours=1),
            queued_before=now - timedelta(hours=1),
        )
        assert [run.id for run in stuck] == ["stale"]
        assert await repository.reset_stuck_run(
            "stale", started_before=now - timedelta(hours=1)
        )
        reset = await repository.get("stale")
        assert reset is not None
        assert reset.status is RunStatus.QUEUED
        assert reset.started_at is None

    asyncio.run(scenario())
