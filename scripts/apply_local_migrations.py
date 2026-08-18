"""Apply control-plane migrations to the local Docker Postgres app database."""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

from psycopg_pool import AsyncConnectionPool

from cowork_agent.config import (
    LOCAL_POSTGRES_DEFAULT_URL,
    database_url,
    load_runtime_environment,
    postgres_mode,
)
from cowork_agent.persistence.migrate import apply_migrations
from cowork_agent.runtime import configure_windows_event_loop_policy


async def main() -> None:
    load_runtime_environment()
    if postgres_mode() == "cloud":
        raise SystemExit(
            "apply_local_migrations.py refuses POSTGRES_MODE=cloud; "
            "switch to local or run migrations against the cloud project separately"
        )
    url = database_url() or LOCAL_POSTGRES_DEFAULT_URL
    host = urlsplit(url).hostname or "unknown"
    print(f"target_host={host}")
    pool = AsyncConnectionPool(url, min_size=1, max_size=2, open=False)
    await pool.open(wait=True)
    try:
        applied = await apply_migrations(pool)
        async with pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT filename FROM schema_migrations ORDER BY filename"
            )
            rows = await cursor.fetchall()
    finally:
        await pool.close()
    print(f"applied_this_run={list(applied)}")
    print(f"schema_migrations={len(rows)}")
    for row in rows:
        print(row[0])


if __name__ == "__main__":
    configure_windows_event_loop_policy()
    asyncio.run(main())
