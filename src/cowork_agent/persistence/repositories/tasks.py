"""SQLite persistence for validated §6.6 Tasks (V1-M4 T4.1).

Local lineage of the target ``tasks`` table (master-comparison §6.6,
``001_mail_todo.sql`` evolution decision 2026-08-07): the idempotent key
``tenant_id:user_id:gmail_message_id:pipeline_version`` and the body-free
row shape pre-warm the V1-H PostgreSQL migration. Rows store the Task
contract as JSON only — raw email bodies never reach this table.
"""

import asyncio
import json
import sqlite3
from pathlib import Path

from cowork_agent.domain.target_contracts import Task


class SQLiteTaskRepository:
    def __init__(self, path: Path) -> None:
        self._path = path

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as database:
            database.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    pipeline_version TEXT NOT NULL,
                    gmail_message_id TEXT NOT NULL,
                    task_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (tenant_id, user_id, gmail_message_id, pipeline_version)
                )
                """
            )
            database.execute(
                "CREATE INDEX IF NOT EXISTS tasks_run_idx ON tasks (run_id, created_at)"
            )

    async def save_task(
        self, *, tenant_id: str, user_id: str, pipeline_version: str, task: Task
    ) -> None:
        await asyncio.to_thread(
            self._save_task_sync, tenant_id, user_id, pipeline_version, task
        )

    def _save_task_sync(
        self, tenant_id: str, user_id: str, pipeline_version: str, task: Task
    ) -> None:
        with self._connect() as database:
            database.execute(
                """
                INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, user_id, gmail_message_id, pipeline_version)
                DO UPDATE SET
                    task_id=excluded.task_id,
                    run_id=excluded.run_id,
                    task_json=excluded.task_json,
                    created_at=excluded.created_at
                """,
                (
                    task.task_id,
                    task.run_id,
                    tenant_id,
                    user_id,
                    pipeline_version,
                    task.gmail_message_id,
                    json.dumps(task.to_dict(), ensure_ascii=False),
                    task.created_at.isoformat(),
                ),
            )

    async def list_for_run(self, run_id: str) -> tuple[Task, ...]:
        return await asyncio.to_thread(self._list_for_run_sync, run_id)

    def _list_for_run_sync(self, run_id: str) -> tuple[Task, ...]:
        with self._connect() as database:
            rows = database.execute(
                "SELECT task_json FROM tasks WHERE run_id = ? ORDER BY created_at, task_id",
                (run_id,),
            ).fetchall()
        return tuple(Task.from_dict(json.loads(str(row["task_json"]))) for row in rows)

    def _connect(self) -> sqlite3.Connection:
        database = sqlite3.connect(self._path)
        database.row_factory = sqlite3.Row
        return database
