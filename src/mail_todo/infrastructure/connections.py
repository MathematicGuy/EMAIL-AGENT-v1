"""Encrypted SQLite persistence for local Gmail connections."""

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path

from mail_todo.domain import MailboxConnection


class SQLiteMailboxConnectionRepository:
    def __init__(self, path: Path) -> None:
        self._path = path

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as database:
            database.execute(
                """
                CREATE TABLE IF NOT EXISTS mailbox_connections (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    external_account_id TEXT NOT NULL,
                    email_address TEXT NOT NULL,
                    encrypted_refresh_token TEXT NOT NULL,
                    scopes TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, provider, external_account_id)
                )
                """
            )

    async def upsert(self, connection: MailboxConnection) -> MailboxConnection:
        return await asyncio.to_thread(self._upsert_sync, connection)

    def _upsert_sync(self, connection: MailboxConnection) -> MailboxConnection:
        with self._connect() as database:
            existing = database.execute(
                "SELECT id, created_at FROM mailbox_connections "
                "WHERE user_id = ? AND provider = ? AND external_account_id = ?",
                (connection.user_id, connection.provider, connection.external_account_id),
            ).fetchone()
            stored = connection
            if existing:
                stored = MailboxConnection(
                    id=str(existing["id"]),
                    user_id=connection.user_id,
                    provider=connection.provider,
                    external_account_id=connection.external_account_id,
                    email_address=connection.email_address,
                    encrypted_refresh_token=connection.encrypted_refresh_token,
                    scopes=connection.scopes,
                    status=connection.status,
                    created_at=datetime.fromisoformat(str(existing["created_at"])),
                    updated_at=connection.updated_at,
                )
            database.execute(
                """
                INSERT INTO mailbox_connections VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, provider, external_account_id) DO UPDATE SET
                    email_address=excluded.email_address,
                    encrypted_refresh_token=excluded.encrypted_refresh_token,
                    scopes=excluded.scopes,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                _to_row(stored),
            )
            return stored

    async def get(self, connection_id: str) -> MailboxConnection | None:
        return await asyncio.to_thread(self._get_sync, connection_id)

    def _get_sync(self, connection_id: str) -> MailboxConnection | None:
        with self._connect() as database:
            row = database.execute(
                "SELECT * FROM mailbox_connections WHERE id = ?", (connection_id,)
            ).fetchone()
        return _from_row(row) if row else None

    async def list_for_user(self, user_id: str) -> tuple[MailboxConnection, ...]:
        return await asyncio.to_thread(self._list_sync, user_id)

    def _list_sync(self, user_id: str) -> tuple[MailboxConnection, ...]:
        with self._connect() as database:
            rows = database.execute(
                "SELECT * FROM mailbox_connections WHERE user_id = ? ORDER BY created_at",
                (user_id,),
            ).fetchall()
        return tuple(_from_row(row) for row in rows)

    async def delete(self, connection_id: str, user_id: str) -> bool:
        return await asyncio.to_thread(self._delete_sync, connection_id, user_id)

    def _delete_sync(self, connection_id: str, user_id: str) -> bool:
        with self._connect() as database:
            cursor = database.execute(
                "DELETE FROM mailbox_connections WHERE id = ? AND user_id = ?",
                (connection_id, user_id),
            )
            return cursor.rowcount > 0

    def _connect(self) -> sqlite3.Connection:
        database = sqlite3.connect(self._path)
        database.row_factory = sqlite3.Row
        return database


def _to_row(connection: MailboxConnection) -> tuple[str, ...]:
    return (
        connection.id,
        connection.user_id,
        connection.provider,
        connection.external_account_id,
        connection.email_address,
        connection.encrypted_refresh_token,
        " ".join(connection.scopes),
        connection.status,
        connection.created_at.isoformat(),
        connection.updated_at.isoformat(),
    )


def _from_row(row: sqlite3.Row) -> MailboxConnection:
    return MailboxConnection(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        provider=str(row["provider"]),
        external_account_id=str(row["external_account_id"]),
        email_address=str(row["email_address"]),
        encrypted_refresh_token=str(row["encrypted_refresh_token"]),
        scopes=tuple(str(row["scopes"]).split()),
        status=str(row["status"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )
