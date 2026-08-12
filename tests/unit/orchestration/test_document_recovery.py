import asyncio
from datetime import UTC, datetime, timedelta


class Repository:
    def __init__(self) -> None:
        self.before: datetime | None = None

    async def reset_stale_jobs(self, *, claimed_before: datetime) -> int:
        self.before = claimed_before
        return 2


def test_document_recovery_requeues_expired_worker_leases() -> None:
    async def scenario() -> None:
        from cowork_agent.orchestration.document_recovery import recover_stale_document_jobs

        repository = Repository()
        now = datetime(2026, 8, 12, tzinfo=UTC)

        assert await recover_stale_document_jobs(repository, now=now) == 2
        assert repository.before == now - timedelta(minutes=15)

    asyncio.run(scenario())
