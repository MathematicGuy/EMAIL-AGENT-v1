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
- ``PostgresChatSummaryEpisodeRepository`` — V2-M3 bounded system-generated
  chat summaries, retry-safe per completed chat turn and retrieval-ineligible.
"""

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Literal, cast

import psycopg
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
    MAX_EPISODIC_RETRIEVAL_ITEMS,
    MAX_RETRIEVAL_TIMEOUT_MS,
    ChatSummaryEpisode,
    DeclarativeProfile,
    EpisodeCitation,
    EpisodeSourceType,
    EpisodeTransition,
    EpisodicMemoryQuery,
    MemoryNamespace,
    MemoryProvenanceSource,
    MemoryType,
    TaskEpisode,
)
from cowork_agent.domain.target_contracts import Task, ValidationStatus
from cowork_agent.features.ai_chat.episode_policy import authorize_chat_summary_write
from cowork_agent.features.ai_chat.memory_gateway import MemorySourceUnavailableError
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

    async def list_recent(
        self, *, user_id: str, mailbox_connection_id: str, limit: int
    ) -> tuple[DigestRun, ...]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                f"""
                SELECT {_RUN_COLUMNS} FROM digest_runs
                WHERE user_id = %s AND mailbox_connection_id = %s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (user_id, mailbox_connection_id, limit),
            )
            rows = await cursor.fetchall()
        return tuple(_run_from_row(row) for row in rows)

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
        profile_key = _profile_key(namespace)
        try:
            async with self._pool.connection() as connection:
                cursor = await connection.execute(
                    f"SELECT {_PROFILE_COLUMNS} FROM chat_profiles"
                    " WHERE profile_key = %s AND (expires_at IS NULL OR expires_at > now())",
                    (profile_key,),
                )
                row = await cursor.fetchone()
        except psycopg.OperationalError as error:
            # Optional profile reads degrade through the feature gateway. Keep
            # namespace validation above the adapter-error boundary and avoid
            # exposing driver details to API/controller callers.
            raise MemorySourceUnavailableError("chat profile read unavailable") from error
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


class PostgresChatSummaryEpisodeRepository:
    """Body-free, system-only chat-summary episode store (V2-M3)."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def write_chat_summary(
        self, namespace: MemoryNamespace, episode: ChatSummaryEpisode
    ) -> ChatSummaryEpisode:
        """Upsert per chat turn without letting retries replace original identity."""

        authorize_chat_summary_write(namespace, episode)
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                f"""
                INSERT INTO chat_summary_episodes (
                    episode_key, {_CHAT_SUMMARY_COLUMNS}
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (tenant_id, user_id, feature, chat_session_id, chat_turn_id)
                DO UPDATE SET
                    summary = CASE
                        WHEN EXCLUDED.updated_at >= chat_summary_episodes.updated_at
                        THEN EXCLUDED.summary ELSE chat_summary_episodes.summary
                    END,
                    expires_at = CASE
                        WHEN EXCLUDED.updated_at >= chat_summary_episodes.updated_at
                        THEN EXCLUDED.expires_at ELSE chat_summary_episodes.expires_at
                    END,
                    pipeline_version = CASE
                        WHEN EXCLUDED.updated_at >= chat_summary_episodes.updated_at
                        THEN EXCLUDED.pipeline_version ELSE chat_summary_episodes.pipeline_version
                    END,
                    model_id = CASE
                        WHEN EXCLUDED.updated_at >= chat_summary_episodes.updated_at
                        THEN EXCLUDED.model_id ELSE chat_summary_episodes.model_id
                    END,
                    prompt_version = CASE
                        WHEN EXCLUDED.updated_at >= chat_summary_episodes.updated_at
                        THEN EXCLUDED.prompt_version ELSE chat_summary_episodes.prompt_version
                    END,
                    confidence = CASE
                        WHEN EXCLUDED.updated_at >= chat_summary_episodes.updated_at
                        THEN EXCLUDED.confidence ELSE chat_summary_episodes.confidence
                    END,
                    updated_at = CASE
                        WHEN EXCLUDED.updated_at >= chat_summary_episodes.updated_at
                        THEN EXCLUDED.updated_at ELSE chat_summary_episodes.updated_at
                    END
                RETURNING {_CHAT_SUMMARY_COLUMNS}
                """,
                (
                    _chat_summary_key(namespace),
                    episode.episode_id,
                    episode.record_id,
                    episode.tenant_id,
                    episode.user_id,
                    namespace.feature,
                    episode.chat_session_id,
                    episode.chat_turn_id,
                    episode.summary,
                    episode.validation_status.value,
                    episode.retrieval_eligible,
                    episode.source_type.value,
                    episode.created_at,
                    episode.updated_at,
                    episode.expires_at,
                    episode.pipeline_version,
                    episode.model_id,
                    episode.prompt_version,
                    episode.confidence,
                ),
            )
            row = await cursor.fetchone()
        assert row is not None
        return _chat_summary_from_row(row)

    async def delete_chat_summary(self, namespace: MemoryNamespace) -> bool:
        """Delete the row represented by the exact in-scope logical namespace key."""

        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                "DELETE FROM chat_summary_episodes WHERE episode_key = %s",
                (_chat_summary_deletion_key(namespace),),
            )
            return cursor.rowcount == 1

    async def delete_all_for_user(self, namespace: MemoryNamespace) -> int:
        """Delete only one tenant/user's AI Chat summary rows via bound values."""

        if namespace.memory_type is not MemoryType.EPISODIC:
            raise ValueError("chat summary deletion requires an episodic namespace")
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                "DELETE FROM chat_summary_episodes"
                " WHERE tenant_id = %s AND user_id = %s AND feature = %s",
                (namespace.tenant_id, namespace.user_id, namespace.feature),
            )
            return cursor.rowcount

    async def purge_expired(self, now: datetime) -> int:
        """Delete compact summaries whose retention boundary has passed."""

        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                "DELETE FROM chat_summary_episodes"
                " WHERE expires_at IS NOT NULL AND expires_at <= %s",
                (now,),
            )
            return cursor.rowcount


class PostgresTaskEpisodeRepository:
    """Durable, body-free chat task episodes with SQL-enforced lifecycle isolation."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def write_task_episode(
        self,
        namespace: MemoryNamespace,
        episode: TaskEpisode,
        *,
        expires_at: datetime | None,
    ) -> TaskEpisode:
        _validate_task_episode_write(namespace, episode, expires_at)
        # Reparse the object-owned payload so a caller cannot bypass the frozen
        # domain constructor and smuggle raw-email/tool-shaped mappings to SQL.
        trusted_episode = TaskEpisode.from_dict(episode.to_dict())
        try:
            async with self._pool.connection() as connection:
                cursor = await connection.execute(
                f"""
                INSERT INTO task_episodes ({_TASK_EPISODE_WRITE_COLUMNS})
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (tenant_id, user_id, feature, chat_session_id, record_id)
                DO UPDATE SET
                    task_title = CASE WHEN task_episodes.validation_status = 'system_generated'
                        AND excluded.updated_at >= task_episodes.updated_at
                        THEN excluded.task_title ELSE task_episodes.task_title END,
                    minimal_request_paraphrase = CASE
                    WHEN task_episodes.validation_status = 'system_generated'
                    AND excluded.updated_at >= task_episodes.updated_at
                    THEN excluded.minimal_request_paraphrase
                    ELSE task_episodes.minimal_request_paraphrase END,
                    action_plan = CASE WHEN task_episodes.validation_status = 'system_generated'
                        AND excluded.updated_at >= task_episodes.updated_at
                        THEN excluded.action_plan ELSE task_episodes.action_plan END,
                    rag_citations = CASE WHEN task_episodes.validation_status = 'system_generated'
                        AND excluded.updated_at >= task_episodes.updated_at
                        THEN excluded.rag_citations ELSE task_episodes.rag_citations END,
                    missing_information = CASE
                    WHEN task_episodes.validation_status = 'system_generated'
                    AND excluded.updated_at >= task_episodes.updated_at
                    THEN excluded.missing_information
                    ELSE task_episodes.missing_information END,
                    expires_at = CASE
                    WHEN task_episodes.validation_status = 'system_generated'
                    AND excluded.updated_at >= task_episodes.updated_at
                    THEN excluded.expires_at ELSE task_episodes.expires_at END,
                    pipeline_version = CASE
                    WHEN task_episodes.validation_status = 'system_generated'
                    AND excluded.updated_at >= task_episodes.updated_at
                    THEN excluded.pipeline_version ELSE task_episodes.pipeline_version END,
                    model_id = CASE WHEN task_episodes.validation_status = 'system_generated'
                        AND excluded.updated_at >= task_episodes.updated_at
                        THEN excluded.model_id ELSE task_episodes.model_id END,
                    prompt_version = CASE WHEN task_episodes.validation_status = 'system_generated'
                        AND excluded.updated_at >= task_episodes.updated_at
                        THEN excluded.prompt_version ELSE task_episodes.prompt_version END,
                    confidence = CASE WHEN task_episodes.validation_status = 'system_generated'
                        AND excluded.updated_at >= task_episodes.updated_at
                        THEN excluded.confidence ELSE task_episodes.confidence END,
                    updated_at = CASE WHEN task_episodes.validation_status = 'system_generated'
                        AND excluded.updated_at >= task_episodes.updated_at
                        THEN excluded.updated_at ELSE task_episodes.updated_at END
                WHERE task_episodes.episode_id = excluded.episode_id
                    AND task_episodes.chat_turn_id = excluded.chat_turn_id
                RETURNING {_TASK_EPISODE_COLUMNS}
                """,
                    _task_episode_params(namespace, trusted_episode, expires_at),
                )
                row = await cursor.fetchone()
        except (psycopg.OperationalError, psycopg.errors.QueryCanceled) as error:
            raise MemorySourceUnavailableError("task episode write unavailable") from error
        except psycopg.errors.UniqueViolation as error:
            raise ValueError("task episode immutable identity conflict") from error
        if row is None:
            raise ValueError("task episode immutable identity conflict")
        return _task_episode_from_row(row)

    async def transition_task_episode(self, transition: EpisodeTransition) -> TaskEpisode | None:
        namespace = transition.namespace
        _task_episode_mutation_key(namespace, transition.episode_id)
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                f"""
                UPDATE task_episodes
                SET validation_status = %s, updated_at = %s
                WHERE tenant_id = %s AND user_id = %s AND feature = %s
                    AND chat_session_id = %s AND record_id = %s AND chat_turn_id = %s
                    AND episode_id = %s AND validation_status = %s
                    AND updated_at <= %s
                RETURNING {_TASK_EPISODE_COLUMNS}
                """,
                (
                    transition.to_status.value,
                    transition.transitioned_at,
                    namespace.tenant_id,
                    namespace.user_id,
                    namespace.feature,
                    namespace.session_id,
                    namespace.record_id,
                    namespace.source_id,
                    transition.episode_id,
                    transition.from_status.value,
                    transition.transitioned_at,
                ),
            )
            row = await cursor.fetchone()
        return None if row is None else _task_episode_from_row(row)

    async def delete_task_episode(self, namespace: MemoryNamespace, *, episode_id: str) -> bool:
        _task_episode_mutation_key(namespace, episode_id)
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                DELETE FROM task_episodes
                WHERE tenant_id = %s AND user_id = %s AND feature = %s
                    AND chat_session_id = %s AND record_id = %s AND chat_turn_id = %s
                    AND episode_id = %s
                """,
                (
                    namespace.tenant_id,
                    namespace.user_id,
                    namespace.feature,
                    namespace.session_id,
                    namespace.record_id,
                    namespace.source_id,
                    episode_id,
                ),
            )
            return cursor.rowcount == 1

    async def read_episodes(
        self, namespace: MemoryNamespace, query: EpisodicMemoryQuery
    ) -> tuple[TaskEpisode, ...]:
        _task_episode_read_namespace(namespace)
        limit = min(query.max_items, MAX_EPISODIC_RETRIEVAL_ITEMS)
        timeout_ms = min(query.timeout_ms, MAX_RETRIEVAL_TIMEOUT_MS)
        try:
            async with self._pool.connection() as connection:
                await connection.execute(
                    "SELECT set_config('statement_timeout', %s, true)", (f"{timeout_ms}ms",)
                )
                cursor = await connection.execute(
                    f"""
                    WITH ranked AS (
                        SELECT {_TASK_EPISODE_COLUMNS},
                            ts_rank_cd(search_vector, terms.tsquery, 32) AS relevance_score
                        FROM task_episodes
                        CROSS JOIN LATERAL (
                            SELECT plainto_tsquery('simple', %s) AS tsquery
                        ) AS terms
                        WHERE tenant_id = %s AND user_id = %s AND feature = %s
                            AND validation_status IN ('user_approved', 'completed')
                            AND retrieval_eligible = true
                            AND search_vector @@ terms.tsquery
                            AND (expires_at IS NULL OR expires_at > now())
                    )
                    SELECT {_TASK_EPISODE_COLUMNS} FROM ranked
                    WHERE relevance_score > 0 AND relevance_score >= %s
                    ORDER BY relevance_score DESC, updated_at DESC, record_id DESC
                    LIMIT %s
                    """,
                    (
                        query.query,
                        namespace.tenant_id,
                        namespace.user_id,
                        namespace.feature,
                        query.min_score,
                        limit,
                    ),
                )
                rows = await cursor.fetchall()
        except (psycopg.OperationalError, psycopg.errors.QueryCanceled) as error:
            raise MemorySourceUnavailableError("task episode read unavailable") from error
        return tuple(_task_episode_from_row(row) for row in rows)

    async def list_episodes(
        self, namespace: MemoryNamespace, *, limit: int = 100
    ) -> tuple[TaskEpisode, ...]:
        """Frontend-safe listing (demo read contract): every non-expired episode
        of the owner regardless of status, newest first."""
        _task_episode_read_namespace(namespace)
        try:
            async with self._pool.connection() as connection:
                cursor = await connection.execute(
                    f"""
                    SELECT {_TASK_EPISODE_COLUMNS}
                    FROM task_episodes
                    WHERE tenant_id = %s AND user_id = %s AND feature = %s
                        AND (expires_at IS NULL OR expires_at > now())
                    ORDER BY created_at DESC, record_id DESC
                    LIMIT %s
                    """,
                    (
                        namespace.tenant_id,
                        namespace.user_id,
                        namespace.feature,
                        max(1, min(limit, MAX_EPISODIC_RETRIEVAL_ITEMS)),
                    ),
                )
                rows = await cursor.fetchall()
        except (psycopg.OperationalError, psycopg.errors.QueryCanceled) as error:
            raise MemorySourceUnavailableError("task episode read unavailable") from error
        return tuple(_task_episode_from_row(row) for row in rows)

    async def delete_all_for_user(self, namespace: MemoryNamespace) -> int:
        _task_episode_read_namespace(namespace)
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                "DELETE FROM task_episodes WHERE tenant_id = %s AND user_id = %s AND feature = %s",
                (namespace.tenant_id, namespace.user_id, namespace.feature),
            )
            return cursor.rowcount

    async def purge_expired(self, now: datetime) -> int:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                "DELETE FROM task_episodes WHERE expires_at IS NOT NULL AND expires_at <= %s",
                (now,),
            )
            return cursor.rowcount


_PROFILE_COLUMNS = (
    "profile_id, tenant_id, user_id, feature, language, timezone,"
    " assistant_persona, response_tone, source_type, expires_at,"
    " created_at, updated_at"
)

_CHAT_SUMMARY_COLUMNS = (
    "episode_id, record_id, tenant_id, user_id, feature, chat_session_id,"
    " chat_turn_id, summary, validation_status, retrieval_eligible, source_type,"
    " created_at, updated_at, expires_at, pipeline_version, model_id,"
    " prompt_version, confidence"
)
_TASK_EPISODE_COLUMNS = (
    "tenant_id, user_id, feature, chat_session_id, record_id, episode_id, chat_turn_id,"
    " creation_reason, task_title, minimal_request_paraphrase, action_plan, rag_citations,"
    " missing_information, validation_status, retrieval_eligible, source_type, created_at,"
    " updated_at, expires_at, pipeline_version, model_id, prompt_version, confidence"
)
_TASK_EPISODE_WRITE_COLUMNS = (
    "tenant_id, user_id, feature, chat_session_id, record_id, episode_id, chat_turn_id,"
    " creation_reason, task_title, minimal_request_paraphrase, action_plan, rag_citations,"
    " missing_information, validation_status, source_type, created_at, updated_at, expires_at,"
    " pipeline_version, model_id, prompt_version, confidence"
)


def _profile_key(namespace: MemoryNamespace) -> str:
    # Session-independent: a profile outlives the session that wrote it, so
    # MemoryNamespace.logical_key() (which pins session_id) is deliberately
    # not used here.
    if namespace.memory_type is not MemoryType.LONG_TERM:
        raise ValueError("chat profiles require a long-term namespace")
    return "/".join((namespace.tenant_id, namespace.user_id, namespace.feature, "long_term"))


def _chat_summary_key(namespace: MemoryNamespace) -> str:
    if namespace.memory_type is not MemoryType.EPISODIC or namespace.source_id is None:
        raise ValueError("chat summary writes require an episodic turn namespace")
    return namespace.logical_key()


def _chat_summary_deletion_key(namespace: MemoryNamespace) -> str:
    if namespace.memory_type is not MemoryType.EPISODIC or namespace.source_id is not None:
        raise ValueError("chat summary deletion requires an episodic record namespace")
    return namespace.logical_key()


def _validate_task_episode_write(
    namespace: MemoryNamespace, episode: TaskEpisode, expires_at: datetime | None
) -> None:
    _task_episode_mutation_key(namespace, episode.episode_id)
    if (
        namespace.tenant_id != episode.tenant_id
        or namespace.user_id != episode.user_id
        or namespace.session_id != episode.chat_session_id
        or namespace.record_id != episode.record_id
        or namespace.source_id != episode.chat_turn_id
    ):
        raise ValueError("task episode namespace must match the episode identity")
    if (
        episode.validation_status is not ValidationStatus.SYSTEM_GENERATED
        or episode.retrieval_eligible
    ):
        raise ValueError("task episode writes require initial system-generated ineligible episodes")
    if episode.source_type is not EpisodeSourceType.SYSTEM_GENERATED_CHAT_TASK:
        raise ValueError("task episode writes require chat-task provenance")
    if expires_at is not None:
        if not isinstance(expires_at, datetime):
            raise TypeError("expires_at must be a datetime or None")
        if expires_at <= episode.created_at:
            raise ValueError("expires_at must be later than episode.created_at")


def _task_episode_mutation_key(namespace: MemoryNamespace, episode_id: str) -> None:
    if namespace.memory_type is not MemoryType.EPISODIC:
        raise ValueError("task episodes require an episodic namespace")
    if namespace.record_id is None or namespace.source_id is None:
        raise ValueError("task episode mutations require record and source identifiers")
    if not isinstance(episode_id, str) or not episode_id:
        raise ValueError("episode_id must be a non-empty string")


def _task_episode_read_namespace(namespace: MemoryNamespace) -> None:
    if namespace.memory_type is not MemoryType.EPISODIC:
        raise ValueError("task episodes require an episodic namespace")


def _task_episode_params(
    namespace: MemoryNamespace, episode: TaskEpisode, expires_at: datetime | None
) -> tuple[object, ...]:
    return (
        namespace.tenant_id,
        namespace.user_id,
        namespace.feature,
        namespace.session_id,
        namespace.record_id,
        episode.episode_id,
        namespace.source_id,
        episode.creation_reason,
        episode.task_title,
        episode.minimal_request_paraphrase,
        Jsonb(list(episode.action_plan)),
        Jsonb([citation.to_dict() for citation in episode.rag_citations]),
        Jsonb(list(episode.missing_information)),
        episode.validation_status.value,
        episode.source_type.value,
        episode.created_at,
        episode.updated_at,
        expires_at,
        episode.pipeline_version,
        episode.model_id,
        episode.prompt_version,
        episode.confidence,
    )


def _task_episode_from_row(row: Sequence[object]) -> TaskEpisode:
    rag_citations = cast(list[object], row[11])
    return TaskEpisode(
        episode_id=str(row[5]),
        record_id=str(row[4]),
        tenant_id=str(row[0]),
        user_id=str(row[1]),
        chat_session_id=str(row[3]),
        chat_turn_id=str(row[6]),
        creation_reason=cast("Literal['explicit_user_task_request']", str(row[7])),
        task_title=str(row[8]),
        minimal_request_paraphrase=str(row[9]),
        action_plan=tuple(cast(list[str], row[10])),
        rag_citations=tuple(
            EpisodeCitation.from_dict(cast(dict[str, object], item)) for item in rag_citations
        ),
        missing_information=tuple(cast(list[str], row[12])),
        validation_status=ValidationStatus(str(row[13])),
        retrieval_eligible=bool(row[14]),
        source_type=EpisodeSourceType(str(row[15])),
        created_at=cast(datetime, row[16]),
        updated_at=cast(datetime, row[17]),
        pipeline_version=str(row[19]),
        model_id=None if row[20] is None else str(row[20]),
        prompt_version=None if row[21] is None else str(row[21]),
        confidence=None if row[22] is None else float(cast(float, row[22])),
    )


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


def _chat_summary_from_row(row: Sequence[object]) -> ChatSummaryEpisode:
    def optional(index: int) -> str | None:
        return None if row[index] is None else str(row[index])

    return ChatSummaryEpisode(
        episode_id=str(row[0]),
        record_id=str(row[1]),
        tenant_id=str(row[2]),
        user_id=str(row[3]),
        chat_session_id=str(row[5]),
        chat_turn_id=str(row[6]),
        summary=str(row[7]),
        validation_status=ValidationStatus(str(row[8])),
        retrieval_eligible=cast(bool, row[9]),
        source_type=EpisodeSourceType(str(row[10])),
        created_at=cast(datetime, row[11]),
        updated_at=cast(datetime, row[12]),
        expires_at=_as_datetime(row[13]),
        pipeline_version=str(row[14]),
        model_id=optional(15),
        prompt_version=optional(16),
        confidence=None if row[17] is None else float(cast(float, row[17])),
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
