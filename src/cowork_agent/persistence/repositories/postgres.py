"""PostgreSQL run/task/outbox repositories (V1-H T5.1).

PostgreSQL is the production source of truth (ADR-001, master-comparison
§3.10). These adapters implement the existing feature ports over psycopg v3
so the workflow and API layers are unchanged:

- ``PostgresRunRepository`` — atomic idempotent create on
  ``(user_id, idempotency_key)`` and a compare-and-set ``claim`` that moves
  exactly one ``queued`` run to ``running`` (single-claim invariant).
- ``PostgresTaskRepository`` — the SQLite lineage shape (V1-M4): idempotent
  key ``tenant_id:user_id:gmail_message_id:pipeline_version``, body-free
  rows (invariant 1), and ``task_run_links`` preserving every producing
  run's view with save-time freshness.
- ``PostgresOutboxRepository`` — durable lifecycle-event outbox on the
  ``outbox_events`` table (metadata-only payloads; T5.3 wires publication).
- ``PostgresChatProfileRepository`` — V2-M2 declarative chat profile keyed by
  the AI Chat memory namespace; explicit-only, expiry-aware, deletable.
"""

import json
from collections.abc import Sequence
from datetime import datetime
from typing import cast

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from cowork_agent.domain import (
    ActionFreshness,
    DigestCompletedEvent,
    DigestRun,
    RunStatus,
    RunTrigger,
)
from cowork_agent.domain.chat_contracts import (
    DeclarativeProfile,
    MemoryNamespace,
    MemoryProvenanceSource,
    MemoryType,
)
from cowork_agent.domain.target_contracts import Task
from cowork_agent.features.email_action_plan.ports import PersistedTask, TaskPointer

_RUN_COLUMNS = (
    "id, user_id, mailbox_connection_id, \"trigger\", status, query,"
    " idempotency_key, max_emails, emails_matched, emails_processed,"
    " emails_actionable, action_items_count, ignored_emails_count,"
    " attachments_found, attachments_extracted, attachment_warnings_count,"
    " truncated, next_cursor, error_code, error_message_safe,"
    " started_at, completed_at, created_at"
)


class PostgresRunRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def create(self, run: DigestRun) -> tuple[DigestRun, bool]:
        # Race-free idempotent create: the no-op self-assignment upsert makes
        # the statement return exactly one row in both outcomes (a true
        # INSERT, or the conflicting row locked and re-read), and
        # ``xmax = 0`` distinguishes the fresh insert from the conflict path.
        placeholders = ", ".join(["%s"] * 22 + ["COALESCE(%s, now())"])
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                f"""
                INSERT INTO digest_runs ({_RUN_COLUMNS})
                VALUES ({placeholders})
                ON CONFLICT (user_id, idempotency_key)
                DO UPDATE SET user_id = EXCLUDED.user_id
                RETURNING {_RUN_COLUMNS}, (xmax = 0) AS created
                """,
                _run_params(run),
            )
            row = await cursor.fetchone()
        assert row is not None
        return _run_from_row(row), bool(row[-1])

    async def get(self, run_id: str) -> DigestRun | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                f"SELECT {_RUN_COLUMNS} FROM digest_runs WHERE id = %s", (run_id,)
            )
            row = await cursor.fetchone()
        return None if row is None else _run_from_row(row)

    async def claim(self, run_id: str, started_at: datetime) -> DigestRun | None:
        """Compare-and-set: only a still-``queued`` run can be claimed, so at
        most one worker ever runs a given run even across processes."""
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                f"""
                UPDATE digest_runs
                SET status = 'running', started_at = %s
                WHERE id = %s AND status = 'queued'
                RETURNING {_RUN_COLUMNS}
                """,
                (started_at, run_id),
            )
            row = await cursor.fetchone()
        return None if row is None else _run_from_row(row)

    async def save(self, run: DigestRun) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                """
                UPDATE digest_runs SET
                    mailbox_connection_id = %s, "trigger" = %s, status = %s,
                    query = %s, idempotency_key = %s, max_emails = %s,
                    emails_matched = %s, emails_processed = %s,
                    emails_actionable = %s, action_items_count = %s,
                    ignored_emails_count = %s, attachments_found = %s,
                    attachments_extracted = %s, attachment_warnings_count = %s,
                    truncated = %s, next_cursor = %s, error_code = %s,
                    error_message_safe = %s, started_at = %s, completed_at = %s
                WHERE id = %s
                """,
                (
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
                    run.truncated,
                    run.next_cursor,
                    run.error_code,
                    run.error_message_safe,
                    run.started_at,
                    run.completed_at,
                    run.id,
                ),
            )

    async def list_stuck_runs(
        self, *, running_before: datetime, queued_before: datetime
    ) -> tuple[DigestRun, ...]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                f"""
                SELECT {_RUN_COLUMNS} FROM digest_runs
                WHERE (status = 'running' AND started_at < %s)
                   OR (status = 'queued' AND created_at < %s)
                ORDER BY created_at
                """,
                (running_before, queued_before),
            )
            rows = await cursor.fetchall()
        return tuple(_run_from_row(row) for row in rows)

    async def reset_stuck_run(self, run_id: str, *, started_before: datetime) -> bool:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE digest_runs
                SET status = 'queued', started_at = NULL
                WHERE id = %s AND status = 'running' AND started_at < %s
                """,
                (run_id, started_before),
            )
            return cursor.rowcount == 1


class PostgresTaskRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save_task(
        self,
        record: PersistedTask,
        *,
        tenant_id: str,
        user_id: str,
        pipeline_version: str,
        run_id: str,
    ) -> None:
        task, pointer = record.task, record.pointer
        task_key = ":".join((tenant_id, user_id, task.gmail_message_id, pipeline_version))
        async with self._pool.connection() as connection:
            # Freshness freezes the legacy cross-run recall at save time:
            # seen when any row for this connection already carries the
            # fingerprint (the worker dedupes in-run duplicates beforehand).
            cursor = await connection.execute(
                "SELECT 1 FROM tasks WHERE mailbox_connection_id = %s AND fingerprint = %s",
                (pointer.mailbox_connection_id, record.fingerprint),
            )
            freshness = (
                ActionFreshness.SEEN if await cursor.fetchone() else ActionFreshness.NEW
            )
            await connection.execute(
                """
                INSERT INTO tasks VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (task_key) DO UPDATE SET
                    task_id = excluded.task_id,
                    mailbox_connection_id = excluded.mailbox_connection_id,
                    provider_thread_id = excluded.provider_thread_id,
                    sender_name = excluded.sender_name,
                    sender_address = excluded.sender_address,
                    email_subject = excluded.email_subject,
                    email_received_at = excluded.email_received_at,
                    fingerprint = excluded.fingerprint,
                    task_json = excluded.task_json,
                    created_at = excluded.created_at
                """,
                (
                    task_key,
                    task.task_id,
                    tenant_id,
                    user_id,
                    pipeline_version,
                    task.gmail_message_id,
                    pointer.mailbox_connection_id,
                    pointer.provider_thread_id,
                    pointer.sender_name,
                    pointer.sender_address,
                    pointer.email_subject,
                    pointer.email_received_at,
                    record.fingerprint,
                    Jsonb(task.to_dict()),
                    task.created_at,
                ),
            )
            await connection.execute(
                "INSERT INTO task_run_links (task_key, run_id, freshness)"
                " VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (task_key, run_id, freshness.value),
            )

    async def list_for_run(self, run_id: str) -> tuple[PersistedTask, ...]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT tasks.task_json, tasks.mailbox_connection_id,
                       tasks.provider_thread_id, tasks.sender_name,
                       tasks.sender_address, tasks.email_subject,
                       tasks.email_received_at, tasks.fingerprint,
                       task_run_links.freshness
                FROM task_run_links
                JOIN tasks ON tasks.task_key = task_run_links.task_key
                WHERE task_run_links.run_id = %s
                ORDER BY task_run_links.created_at, tasks.task_key
                """,
                (run_id,),
            )
            rows = await cursor.fetchall()
        return tuple(_record_from_row(row) for row in rows)


class PostgresOutboxRepository:
    """Durable lifecycle-event outbox; payloads stay metadata-only."""

    EVENT_TYPE = "digest_completed"

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def add(self, event: DigestCompletedEvent) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                """
                INSERT INTO outbox_events (aggregate_id, event_type, payload)
                VALUES (%s, %s, %s)
                ON CONFLICT (aggregate_id, event_type) DO NOTHING
                """,
                (
                    event.run_id,
                    self.EVENT_TYPE,
                    Jsonb(
                        {
                            "run_id": event.run_id,
                            "user_id": event.user_id,
                            "status": event.status.value,
                            "occurred_at": event.occurred_at.isoformat(),
                        }
                    ),
                ),
            )

    async def pending(self) -> tuple[DigestCompletedEvent, ...]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT payload FROM outbox_events"
                " WHERE published_at IS NULL ORDER BY id"
            )
            rows = await cursor.fetchall()
        return tuple(_event_from_payload(row[0]) for row in rows)

    async def mark_published(self, run_id: str) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                "UPDATE outbox_events SET published_at = now()"
                " WHERE aggregate_id = %s AND published_at IS NULL",
                (run_id,),
            )


class PostgresChatProfileRepository:
    """Explicit-only declarative chat profile store (V2-M2, PRD-v2 FR-03..FR-05).

    Isolation and retention are enforced in SQL rather than in the caller: the
    namespace supplies the primary key, and an expired row can never be read
    back (FR-16) even if a caller forgets to check.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def read_profile(self, namespace: MemoryNamespace) -> DeclarativeProfile | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                f"SELECT {_PROFILE_COLUMNS} FROM chat_profiles"
                " WHERE profile_key = %s AND (expires_at IS NULL OR expires_at > now())",
                (_profile_key(namespace),),
            )
            row = await cursor.fetchone()
        return None if row is None else _profile_from_row(row)

    async def write_profile(
        self, namespace: MemoryNamespace, profile: DeclarativeProfile
    ) -> DeclarativeProfile:
        """Idempotent upsert; ``created_at`` survives every later write."""
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                f"""
                INSERT INTO chat_profiles (profile_key, {_PROFILE_COLUMNS})
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (profile_key) DO UPDATE SET
                    profile_id = excluded.profile_id,
                    language = excluded.language,
                    timezone = excluded.timezone,
                    assistant_persona = excluded.assistant_persona,
                    response_tone = excluded.response_tone,
                    source_type = excluded.source_type,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
                RETURNING {_PROFILE_COLUMNS}
                """,
                (
                    _profile_key(namespace),
                    profile.profile_id,
                    profile.tenant_id,
                    profile.user_id,
                    namespace.feature,
                    profile.language,
                    profile.timezone,
                    profile.assistant_persona,
                    profile.response_tone,
                    profile.source_type.value,
                    profile.expires_at,
                    profile.created_at,
                    profile.updated_at,
                ),
            )
            row = await cursor.fetchone()
        assert row is not None
        return _profile_from_row(row)

    async def delete_profile(self, namespace: MemoryNamespace) -> bool:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                "DELETE FROM chat_profiles WHERE profile_key = %s",
                (_profile_key(namespace),),
            )
            return cursor.rowcount == 1

    async def purge_expired(self, now: datetime) -> int:
        """Retention purge (FR-16); reads already exclude expired rows."""
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                "DELETE FROM chat_profiles WHERE expires_at IS NOT NULL AND expires_at <= %s",
                (now,),
            )
            return cursor.rowcount


_PROFILE_COLUMNS = (
    "profile_id, tenant_id, user_id, feature, language, timezone,"
    " assistant_persona, response_tone, source_type, expires_at,"
    " created_at, updated_at"
)


def _profile_key(namespace: MemoryNamespace) -> str:
    # Session-independent: a profile outlives the session that wrote it, so
    # MemoryNamespace.logical_key() (which pins session_id) is deliberately
    # not used here.
    if namespace.memory_type is not MemoryType.LONG_TERM:
        raise ValueError("chat profiles require a long-term namespace")
    return "/".join((namespace.tenant_id, namespace.user_id, namespace.feature, "long_term"))


def _profile_from_row(row: Sequence[object]) -> DeclarativeProfile:
    def optional(index: int) -> str | None:
        return None if row[index] is None else str(row[index])

    return DeclarativeProfile(
        profile_id=str(row[0]),
        tenant_id=str(row[1]),
        user_id=str(row[2]),
        language=optional(4),
        timezone=optional(5),
        assistant_persona=optional(6),
        response_tone=optional(7),
        source_type=MemoryProvenanceSource(str(row[8])),
        expires_at=_as_datetime(row[9]),
        created_at=cast(datetime, row[10]),
        updated_at=cast(datetime, row[11]),
    )


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
        run.truncated,
        run.next_cursor,
        run.error_code,
        run.error_message_safe,
        run.started_at,
        run.completed_at,
        run.created_at,
    )


def _run_from_row(row: Sequence[object]) -> DigestRun:
    def integer(index: int) -> int:
        return cast(int, row[index])

    return DigestRun(
        id=str(row[0]),
        user_id=str(row[1]),
        mailbox_connection_id=str(row[2]),
        trigger=RunTrigger(str(row[3])),
        status=RunStatus(str(row[4])),
        query=str(row[5]),
        idempotency_key=str(row[6]),
        max_emails=integer(7),
        emails_matched=integer(8),
        emails_processed=integer(9),
        emails_actionable=integer(10),
        action_items_count=integer(11),
        ignored_emails_count=integer(12),
        attachments_found=integer(13),
        attachments_extracted=integer(14),
        attachment_warnings_count=integer(15),
        truncated=bool(row[16]),
        next_cursor=None if row[17] is None else str(row[17]),
        error_code=None if row[18] is None else str(row[18]),
        error_message_safe=None if row[19] is None else str(row[19]),
        started_at=_as_datetime(row[20]),
        completed_at=_as_datetime(row[21]),
        created_at=_as_datetime(row[22]),
    )


def _as_datetime(value: object) -> datetime | None:
    return None if value is None else cast(datetime, value)


def _record_from_row(row: Sequence[object]) -> PersistedTask:
    payload = row[0] if isinstance(row[0], dict) else json.loads(str(row[0]))
    return PersistedTask(
        task=Task.from_dict(payload),
        pointer=TaskPointer(
            mailbox_connection_id=str(row[1]),
            provider_thread_id=str(row[2]),
            sender_name=None if row[3] is None else str(row[3]),
            sender_address=str(row[4]),
            email_subject=str(row[5]),
            email_received_at=cast(datetime, row[6]),
        ),
        fingerprint=str(row[7]).strip(),
        freshness=ActionFreshness(str(row[8])),
    )


def _event_from_payload(payload: object) -> DigestCompletedEvent:
    data = payload if isinstance(payload, dict) else json.loads(str(payload))
    return DigestCompletedEvent(
        run_id=str(data["run_id"]),
        user_id=str(data["user_id"]),
        status=RunStatus(str(data["status"])),
        occurred_at=datetime.fromisoformat(str(data["occurred_at"])),
    )
