"""Encrypted persistence for per-user Google Calendar grants.

Both backends in one module because the two are twenty lines each and reading
them side by side is what keeps them agreeing. The refresh token arrives already
encrypted by `TokenCipher`; neither backend ever sees a plaintext credential.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

from cowork_agent.domain import CalendarConnection

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

_COLUMNS = (
    "id, user_id, provider, external_account_id, calendar_id, "
    "encrypted_refresh_token, scopes, timezone, status, created_at, updated_at"
)


class SQLiteCalendarConnectionRepository:
    def __init__(self, path: Path) -> None:
        self._path = path

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as database:
            database.execute(
                """
                CREATE TABLE IF NOT EXISTS calendar_connections (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL UNIQUE,
                    provider TEXT NOT NULL,
                    external_account_id TEXT NOT NULL,
                    calendar_id TEXT NOT NULL,
                    encrypted_refresh_token TEXT NOT NULL,
                    scopes TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    async def upsert(self, connection: CalendarConnection) -> CalendarConnection:
        return await asyncio.to_thread(self._upsert_sync, connection)

    def _upsert_sync(self, connection: CalendarConnection) -> CalendarConnection:
        with self._connect() as database:
            existing = database.execute(
                "SELECT id, created_at FROM calendar_connections WHERE user_id = ?",
                (connection.user_id,),
            ).fetchone()
            stored = connection
            if existing:
                # Reconnecting keeps the record's identity and its original
                # creation time, so a re-grant reads as the same connection
                # rather than a new one.
                stored = _replace_identity(
                    connection,
                    connection_id=str(existing["id"]),
                    created_at=datetime.fromisoformat(str(existing["created_at"])),
                )
            database.execute(
                f"""
                INSERT INTO calendar_connections ({_COLUMNS})
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    provider=excluded.provider,
                    external_account_id=excluded.external_account_id,
                    calendar_id=excluded.calendar_id,
                    encrypted_refresh_token=excluded.encrypted_refresh_token,
                    scopes=excluded.scopes,
                    timezone=excluded.timezone,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                _to_row(stored),
            )
            return stored

    async def get_for_user(self, user_id: str) -> CalendarConnection | None:
        return await asyncio.to_thread(self._get_sync, user_id)

    def _get_sync(self, user_id: str) -> CalendarConnection | None:
        with self._connect() as database:
            row = database.execute(
                "SELECT * FROM calendar_connections WHERE user_id = ?", (user_id,)
            ).fetchone()
        return _from_sqlite_row(row) if row else None

    async def delete_for_user(self, user_id: str) -> bool:
        return await asyncio.to_thread(self._delete_sync, user_id)

    def _delete_sync(self, user_id: str) -> bool:
        with self._connect() as database:
            cursor = database.execute(
                "DELETE FROM calendar_connections WHERE user_id = ?", (user_id,)
            )
            return cursor.rowcount > 0

    def _connect(self) -> sqlite3.Connection:
        database = sqlite3.connect(self._path)
        database.row_factory = sqlite3.Row
        return database


class PostgresCalendarConnectionRepository:
    """The same three operations against the Supabase control plane."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def upsert(self, connection: CalendarConnection) -> CalendarConnection:
        async with self._pool.connection() as database:
            cursor = await database.execute(
                f"""
                INSERT INTO calendar_connections ({_COLUMNS})
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    provider = EXCLUDED.provider,
                    external_account_id = EXCLUDED.external_account_id,
                    calendar_id = EXCLUDED.calendar_id,
                    encrypted_refresh_token = EXCLUDED.encrypted_refresh_token,
                    scopes = EXCLUDED.scopes,
                    timezone = EXCLUDED.timezone,
                    status = EXCLUDED.status,
                    updated_at = EXCLUDED.updated_at
                RETURNING {_COLUMNS}
                """,
                (
                    connection.id,
                    connection.user_id,
                    connection.provider,
                    connection.external_account_id.lower(),
                    connection.calendar_id,
                    connection.encrypted_refresh_token,
                    list(connection.scopes),
                    connection.timezone,
                    connection.status,
                    connection.created_at,
                    connection.updated_at,
                ),
            )
            row = await cursor.fetchone()
        assert row is not None
        return _from_postgres_row(row)

    async def get_for_user(self, user_id: str) -> CalendarConnection | None:
        async with self._pool.connection() as database:
            cursor = await database.execute(
                f"SELECT {_COLUMNS} FROM calendar_connections WHERE user_id = %s",
                (user_id,),
            )
            row = await cursor.fetchone()
        return None if row is None else _from_postgres_row(row)

    async def delete_for_user(self, user_id: str) -> bool:
        async with self._pool.connection() as database:
            cursor = await database.execute(
                "DELETE FROM calendar_connections WHERE user_id = %s", (user_id,)
            )
        return cursor.rowcount == 1


def _replace_identity(
    connection: CalendarConnection, *, connection_id: str, created_at: datetime
) -> CalendarConnection:
    return CalendarConnection(
        id=connection_id,
        user_id=connection.user_id,
        provider=connection.provider,
        external_account_id=connection.external_account_id,
        calendar_id=connection.calendar_id,
        encrypted_refresh_token=connection.encrypted_refresh_token,
        scopes=connection.scopes,
        timezone=connection.timezone,
        status=connection.status,
        created_at=created_at,
        updated_at=connection.updated_at,
    )


def _to_row(connection: CalendarConnection) -> tuple[str, ...]:
    return (
        connection.id,
        connection.user_id,
        connection.provider,
        connection.external_account_id,
        connection.calendar_id,
        connection.encrypted_refresh_token,
        " ".join(connection.scopes),
        connection.timezone,
        connection.status,
        connection.created_at.isoformat(),
        connection.updated_at.isoformat(),
    )


def _from_sqlite_row(row: sqlite3.Row) -> CalendarConnection:
    return CalendarConnection(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        provider=str(row["provider"]),
        external_account_id=str(row["external_account_id"]),
        calendar_id=str(row["calendar_id"]),
        encrypted_refresh_token=str(row["encrypted_refresh_token"]),
        scopes=tuple(str(row["scopes"]).split()),
        timezone=str(row["timezone"]),
        status=str(row["status"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )


def _from_postgres_row(row: tuple[object, ...]) -> CalendarConnection:
    return CalendarConnection(
        id=str(row[0]),
        user_id=str(row[1]),
        provider=str(row[2]),
        external_account_id=str(row[3]),
        calendar_id=str(row[4]),
        encrypted_refresh_token=str(row[5]),
        scopes=tuple(cast("list[str]", row[6])),
        timezone=str(row[7]),
        status=str(row[8]),
        created_at=cast("datetime", row[9]),
        updated_at=cast("datetime", row[10]),
    )
