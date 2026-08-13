"""SQLite persistence for validated §6.6 Tasks (V1-M4 T4.1/T4.2).

Local lineage of the target ``tasks`` table (master-comparison §6.6,
``001_mail_todo.sql`` evolution decision 2026-08-07): the idempotent key
``tenant_id:user_id:gmail_message_id:pipeline_version`` and the body-free
row shape pre-warm the V1-H PostgreSQL migration. Rows store the Task
contract as JSON plus the body-free pointer metadata the compatibility
mapper needs; ``task_run_links`` keeps every producing run attached to the
idempotent key, so upserts never erase an earlier run's result view.
"""

import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from cowork_agent.domain import ActionFreshness
from cowork_agent.domain.target_contracts import Task
from cowork_agent.features.email_action_plan.ports import PersistedTask, TaskPointer

_SCHEMA_VERSION = 2


class SQLiteTaskRepository:
    def __init__(self, path: Path) -> None:
        self._path = path

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as database:
            # Development-stage schema evolution: recreate tables written by
            # the older T4.1 shape instead of silently keeping them.
            if database.execute("PRAGMA user_version").fetchone()[0] < _SCHEMA_VERSION:
                database.execute("DROP TABLE IF EXISTS task_run_links")
                database.execute("DROP TABLE IF EXISTS tasks")
                database.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            database.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_key TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    pipeline_version TEXT NOT NULL,
                    gmail_message_id TEXT NOT NULL,
                    mailbox_connection_id TEXT NOT NULL,
                    provider_thread_id TEXT NOT NULL,
                    sender_name TEXT,
                    sender_address TEXT NOT NULL,
                    email_subject TEXT NOT NULL,
                    email_received_at TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    task_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            database.execute(
                """
                CREATE TABLE IF NOT EXISTS task_run_links (
                    task_key TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    freshness TEXT NOT NULL CHECK (freshness IN ('new', 'seen')),
                    PRIMARY KEY (task_key, run_id)
                )
                """
            )
            database.execute(
                "CREATE INDEX IF NOT EXISTS tasks_fingerprint_idx"
                " ON tasks (mailbox_connection_id, fingerprint)"
            )
            database.execute(
                "CREATE INDEX IF NOT EXISTS task_links_run_idx ON task_run_links (run_id)"
            )

    async def save_task(
        self,
        record: PersistedTask,
        *,
        tenant_id: str,
        user_id: str,
        pipeline_version: str,
        run_id: str,
    ) -> None:
        await asyncio.to_thread(
            self._save_task_sync, record, tenant_id, user_id, pipeline_version, run_id
        )

    def _save_task_sync(
        self,
        record: PersistedTask,
        tenant_id: str,
        user_id: str,
        pipeline_version: str,
        run_id: str,
    ) -> None:
        task, pointer = record.task, record.pointer
        task_key = _task_key(tenant_id, user_id, task.gmail_message_id, pipeline_version)
        with self._connect() as database:
            # Freshness freezes the legacy cross-run recall at save time:
            # seen when any row for this connection already carries the
            # fingerprint (in-run duplicates are impossible — the worker
            # dedupes by fingerprint before persisting).
            seen = database.execute(
                "SELECT 1 FROM tasks WHERE mailbox_connection_id = ? AND fingerprint = ?",
                (pointer.mailbox_connection_id, record.fingerprint),
            ).fetchone()
            freshness = ActionFreshness.SEEN if seen else ActionFreshness.NEW
            database.execute(
                """
                INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_key) DO UPDATE SET
                    task_id=excluded.task_id,
                    run_id=excluded.run_id,
                    mailbox_connection_id=excluded.mailbox_connection_id,
                    provider_thread_id=excluded.provider_thread_id,
                    sender_name=excluded.sender_name,
                    sender_address=excluded.sender_address,
                    email_subject=excluded.email_subject,
                    email_received_at=excluded.email_received_at,
                    fingerprint=excluded.fingerprint,
                    task_json=excluded.task_json,
                    created_at=excluded.created_at
                """,
                (
                    task_key,
                    task.task_id,
                    run_id,
                    tenant_id,
                    user_id,
                    pipeline_version,
                    task.gmail_message_id,
                    pointer.mailbox_connection_id,
                    pointer.provider_thread_id,
                    pointer.sender_name,
                    pointer.sender_address,
                    pointer.email_subject,
                    pointer.email_received_at.isoformat(),
                    record.fingerprint,
                    json.dumps(task.to_dict(), ensure_ascii=False),
                    task.created_at.isoformat(),
                ),
            )
            database.execute(
                "INSERT OR IGNORE INTO task_run_links VALUES (?, ?, ?)",
                (task_key, run_id, freshness.value),
            )

    async def list_for_run(self, run_id: str) -> tuple[PersistedTask, ...]:
        return await asyncio.to_thread(self._list_for_run_sync, run_id)

    def _list_for_run_sync(self, run_id: str) -> tuple[PersistedTask, ...]:
        with self._connect() as database:
            rows = database.execute(
                """
                SELECT tasks.task_json, tasks.mailbox_connection_id,
                       tasks.provider_thread_id, tasks.sender_name,
                       tasks.sender_address, tasks.email_subject,
                       tasks.email_received_at, tasks.fingerprint,
                       task_run_links.freshness
                FROM task_run_links
                JOIN tasks ON tasks.task_key = task_run_links.task_key
                WHERE task_run_links.run_id = ?
                ORDER BY task_run_links.rowid
                """,
                (run_id,),
            ).fetchall()
        return tuple(_record_from_row(row) for row in rows)

    def _connect(self) -> sqlite3.Connection:
        database = sqlite3.connect(self._path)
        database.row_factory = sqlite3.Row
        return database


def _task_key(tenant_id: str, user_id: str, gmail_message_id: str, pipeline_version: str) -> str:
    return ":".join((tenant_id, user_id, gmail_message_id, pipeline_version))


def _record_from_row(row: sqlite3.Row) -> PersistedTask:
    return PersistedTask(
        task=Task.from_dict(json.loads(str(row["task_json"]))),
        pointer=TaskPointer(
            mailbox_connection_id=str(row["mailbox_connection_id"]),
            provider_thread_id=str(row["provider_thread_id"]),
            sender_name=None if row["sender_name"] is None else str(row["sender_name"]),
            sender_address=str(row["sender_address"]),
            email_subject=str(row["email_subject"]),
            email_received_at=datetime.fromisoformat(str(row["email_received_at"])),
        ),
        fingerprint=str(row["fingerprint"]),
        freshness=ActionFreshness(str(row["freshness"])),
    )
