"""Ordered, idempotent application of the SQL migration lineage (V1-H T5.1).

Migrations are plain ``.sql`` files in ``persistence/migrations`` applied in
filename order; all pending files run in one transaction and each is
recorded in ``schema_migrations`` so re-application is a no-op.
``*.down.sql`` files are manual rollback companions and are never applied.
A session advisory lock serializes concurrent migrators (the V1-H worker
process migrates on boot as well).
"""

from pathlib import Path

from psycopg_pool import AsyncConnectionPool

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_LOCK_KEY_SQL = "SELECT pg_advisory_lock(hashtext('schema_migrations'))"


async def apply_migrations(pool: AsyncConnectionPool) -> tuple[str, ...]:
    """Apply every pending migration; returns the names applied this call."""
    applied_names: list[str] = []
    async with pool.connection() as connection:
        await connection.execute(_LOCK_KEY_SQL)
        try:
            await connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " filename text PRIMARY KEY,"
                " applied_at timestamptz NOT NULL DEFAULT now())"
            )
            cursor = await connection.execute("SELECT filename FROM schema_migrations")
            applied = {row[0] for row in await cursor.fetchall()}
            for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
                if path.name.endswith(".down.sql") or path.name in applied:
                    continue
                await connection.execute(path.read_text(encoding="utf-8"))
                await connection.execute(
                    "INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,)
                )
                applied_names.append(path.name)
        finally:
            await connection.execute("SELECT pg_advisory_unlock(hashtext('schema_migrations'))")
    return tuple(applied_names)
