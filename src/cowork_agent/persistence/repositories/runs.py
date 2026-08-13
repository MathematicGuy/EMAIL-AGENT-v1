"""Durable SQLite run repository for local execution."""

import asyncio
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from cowork_agent.domain import DigestRun, RunStatus, RunTrigger

_RUN_COLUMNS = (
    "id, user_id, mailbox_connection_id, trigger, status, query,"
    " idempotency_key, max_emails, emails_matched, emails_processed,"
    " emails_actionable, action_items_count, ignored_emails_count,"
    " attachments_found, attachments_extracted, attachment_warnings_count,"
    " truncated, next_cursor, error_code, error_message_safe,"
    " started_at, completed_at, created_at"
)


class SQLiteRunRepository:
    """Persist run control-plane metadata without storing email content."""

    def __init__(self, path: Path) -> None:
        self._path = path

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as database:
            database.execute("PRAGMA journal_mode = WAL")
            database.execute(
                """
                CREATE TABLE IF NOT EXISTS digest_runs (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    mailbox_connection_id TEXT NOT NULL,
                    trigger TEXT NOT NULL CHECK (trigger = 'on_demand'),
                    status TEXT NOT NULL CHECK (
                        status IN ('queued','running','succeeded','partial','failed')
                    ),
                    query TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    max_emails INTEGER NOT NULL CHECK (max_emails BETWEEN 1 AND 500),
                    emails_matched INTEGER NOT NULL DEFAULT 0,
                    emails_processed INTEGER NOT NULL DEFAULT 0,
                    emails_actionable INTEGER NOT NULL DEFAULT 0,
                    action_items_count INTEGER NOT NULL DEFAULT 0,
                    ignored_emails_count INTEGER NOT NULL DEFAULT 0,
                    attachments_found INTEGER NOT NULL DEFAULT 0,
                    attachments_extracted INTEGER NOT NULL DEFAULT 0,
                    attachment_warnings_count INTEGER NOT NULL DEFAULT 0,
                    truncated INTEGER NOT NULL DEFAULT 0,
                    next_cursor TEXT,
                    error_code TEXT,
                    error_message_safe TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE (user_id, idempotency_key)
                )
                """
            )
            database.execute(
                """
                CREATE INDEX IF NOT EXISTS digest_runs_mailbox_created_idx
                ON digest_runs (mailbox_connection_id, created_at DESC, id DESC)
                """
            )

    async def create(self, run: DigestRun) -> tuple[DigestRun, bool]:
        return await asyncio.to_thread(self._create_sync, run)

    def _create_sync(self, run: DigestRun) -> tuple[DigestRun, bool]:
        if run.created_at is None:
            run.created_at = datetime.now(UTC)
        with self._connect() as database:
            cursor = database.execute(
                f"INSERT OR IGNORE INTO digest_runs ({_RUN_COLUMNS})"
                " VALUES (" + ", ".join("?" for _ in range(23)) + ")",
                _run_params(run),
            )
            created = cursor.rowcount == 1
            row = database.execute(
                f"SELECT {_RUN_COLUMNS} FROM digest_runs WHERE user_id = ? AND idempotency_key = ?",
                (run.user_id, run.idempotency_key),
            ).fetchone()
        assert row is not None
        return _run_from_row(row), created

    async def get(self, run_id: str) -> DigestRun | None:
        return await asyncio.to_thread(self._get_sync, run_id)

    def _get_sync(self, run_id: str) -> DigestRun | None:
        with self._connect() as database:
            row = database.execute(
                f"SELECT {_RUN_COLUMNS} FROM digest_runs WHERE id = ?", (run_id,)
            ).fetchone()
        return None if row is None else _run_from_row(row)

    async def list_recent(
        self, *, user_id: str, mailbox_connection_id: str, limit: int
    ) -> tuple[DigestRun, ...]:
        return await asyncio.to_thread(
            self._list_recent_sync, user_id, mailbox_connection_id, limit
        )

    def _list_recent_sync(
        self, user_id: str, mailbox_connection_id: str, limit: int
    ) -> tuple[DigestRun, ...]:
        with self._connect() as database:
            rows = database.execute(
                f"SELECT {_RUN_COLUMNS} FROM digest_runs"
                " WHERE user_id = ? AND mailbox_connection_id = ?"
                " ORDER BY created_at DESC, id DESC LIMIT ?",
                (user_id, mailbox_connection_id, limit),
            ).fetchall()
        return tuple(_run_from_row(row) for row in rows)

    async def claim(self, run_id: str, started_at: datetime) -> DigestRun | None:
        return await asyncio.to_thread(self._claim_sync, run_id, started_at)

    def _claim_sync(self, run_id: str, started_at: datetime) -> DigestRun | None:
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            cursor = database.execute(
                "UPDATE digest_runs SET status = 'running', started_at = ?"
                " WHERE id = ? AND status = 'queued'",
                (_datetime_text(started_at), run_id),
            )
            if cursor.rowcount != 1:
                return None
            row = database.execute(
                f"SELECT {_RUN_COLUMNS} FROM digest_runs WHERE id = ?", (run_id,)
            ).fetchone()
        assert row is not None
        return _run_from_row(row)

    async def save(self, run: DigestRun) -> None:
        await asyncio.to_thread(self._save_sync, run)

    def _save_sync(self, run: DigestRun) -> None:
        with self._connect() as database:
            database.execute(
                """
                UPDATE digest_runs SET
                    mailbox_connection_id = ?, trigger = ?, status = ?, query = ?,
                    idempotency_key = ?, max_emails = ?, emails_matched = ?,
                    emails_processed = ?, emails_actionable = ?, action_items_count = ?,
                    ignored_emails_count = ?, attachments_found = ?,
                    attachments_extracted = ?, attachment_warnings_count = ?,
                    truncated = ?, next_cursor = ?, error_code = ?,
                    error_message_safe = ?, started_at = ?, completed_at = ?
                WHERE id = ?
                """,
                _run_params(run)[2:20] + _run_params(run)[20:22] + (run.id,),
            )

    async def list_stuck_runs(
        self, *, running_before: datetime, queued_before: datetime
    ) -> tuple[DigestRun, ...]:
        return await asyncio.to_thread(self._list_stuck_runs_sync, running_before, queued_before)

    def _list_stuck_runs_sync(
        self, running_before: datetime, queued_before: datetime
    ) -> tuple[DigestRun, ...]:
        with self._connect() as database:
            rows = database.execute(
                f"SELECT {_RUN_COLUMNS} FROM digest_runs"
                " WHERE (status = 'running' AND started_at < ?)"
                " OR (status = 'queued' AND created_at < ?)"
                " ORDER BY created_at",
                (_datetime_text(running_before), _datetime_text(queued_before)),
            ).fetchall()
        return tuple(_run_from_row(row) for row in rows)

    async def reset_stuck_run(self, run_id: str, *, started_before: datetime) -> bool:
        return await asyncio.to_thread(self._reset_stuck_run_sync, run_id, started_before)

    def _reset_stuck_run_sync(self, run_id: str, started_before: datetime) -> bool:
        with self._connect() as database:
            cursor = database.execute(
                "UPDATE digest_runs SET status = 'queued', started_at = NULL"
                " WHERE id = ? AND status = 'running' AND started_at < ?",
                (run_id, _datetime_text(started_before)),
            )
        return cursor.rowcount == 1

    def _connect(self) -> sqlite3.Connection:
        database = sqlite3.connect(self._path, timeout=30)
        database.row_factory = sqlite3.Row
        return database


def _run_params(run: DigestRun) -> tuple[object, ...]:
    return (
        run.id,
        run.user_id,
        run.mailbox_connection_id,
        run.trigger.value,
        run.status.value,
        run.query,
        run.idempotency_key,
        run.max_emails,
        run.emails_matched,
        run.emails_processed,
        run.emails_actionable,
        run.action_items_count,
        run.ignored_emails_count,
        run.attachments_found,
        run.attachments_extracted,
        run.attachment_warnings_count,
        int(run.truncated),
        run.next_cursor,
        run.error_code,
        run.error_message_safe,
        _optional_datetime_text(run.started_at),
        _optional_datetime_text(run.completed_at),
        _datetime_text(run.created_at or datetime.now(UTC)),
    )


def _run_from_row(row: Sequence[object]) -> DigestRun:
    return DigestRun(
        id=str(row[0]),
        user_id=str(row[1]),
        mailbox_connection_id=str(row[2]),
        trigger=RunTrigger(str(row[3])),
        status=RunStatus(str(row[4])),
        query=str(row[5]),
        idempotency_key=str(row[6]),
        max_emails=int(str(row[7])),
        emails_matched=int(str(row[8])),
        emails_processed=int(str(row[9])),
        emails_actionable=int(str(row[10])),
        action_items_count=int(str(row[11])),
        ignored_emails_count=int(str(row[12])),
        attachments_found=int(str(row[13])),
        attachments_extracted=int(str(row[14])),
        attachment_warnings_count=int(str(row[15])),
        truncated=bool(int(str(row[16]))),
        next_cursor=None if row[17] is None else str(row[17]),
        error_code=None if row[18] is None else str(row[18]),
        error_message_safe=None if row[19] is None else str(row[19]),
        started_at=_optional_datetime(row[20]),
        completed_at=_optional_datetime(row[21]),
        created_at=datetime.fromisoformat(str(row[22])),
    )


def _datetime_text(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _optional_datetime_text(value: datetime | None) -> str | None:
    return None if value is None else _datetime_text(value)


def _optional_datetime(value: object) -> datetime | None:
    return None if value is None else datetime.fromisoformat(str(value))
