"""Explicit infrastructure purge entry point for chat memory (V2-M6-B).

Connects to PostgreSQL via ``DATABASE_URL``, runs the configured
``MemoryPurgeCoordinator``, and prints only metadata counts.

NO scheduler, NO recurring trigger — this script must be invoked explicitly.
"""

import asyncio
import os
import selectors
import sys
from datetime import UTC, datetime

from cowork_agent.features.ai_chat.memory_observability import LoggingMemoryOperationSink
from cowork_agent.features.ai_chat.retention import (
    MemoryPurgeCoordinator,
    MemoryPurgeReport,
)


async def run_purge(database_url: str) -> MemoryPurgeReport:
    """Run one purge against the PostgreSQL store and return the metadata report."""

    from psycopg_pool import AsyncConnectionPool

    from cowork_agent.persistence.repositories.postgres import (
        PostgresChatProfileRepository,
        PostgresTaskEpisodeRepository,
    )

    pool = AsyncConnectionPool(database_url, min_size=1, max_size=4, open=False)
    await pool.open(wait=True)
    try:
        profiles = PostgresChatProfileRepository(pool)
        episodes = PostgresTaskEpisodeRepository(pool)
        coordinator = MemoryPurgeCoordinator(
            profiles, episodes, sink=LoggingMemoryOperationSink()
        )
        report = await coordinator.purge_expired(datetime.now(UTC))
    finally:
        await pool.close()
    return report


def _main() -> int:
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        print("DATABASE_URL environment variable is required", file=sys.stderr)
        return 1
    try:
        report = asyncio.run(
            run_purge(database_url),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    except Exception as exc:
        print(f"purge failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(
        f"profile_count={report.profile_count} "
        f"episode_count={report.episode_count} "
        f"complete={report.complete}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(_main())
