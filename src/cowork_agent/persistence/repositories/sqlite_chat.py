"""SQLite-backed chat sessions, history, and memory for local runtime."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from cowork_agent.domain.chat_contracts import (
    MAX_EPISODIC_RETRIEVAL_ITEMS,
    ChatMemoryScope,
    ChatSummaryEpisode,
    ChatTurn,
    ChatTurnStatus,
    DeclarativeProfile,
    EpisodeTransition,
    EpisodicMemoryQuery,
    MemoryNamespace,
    MemoryType,
    TaskEpisode,
)
from cowork_agent.domain.target_contracts import ValidationStatus
from cowork_agent.features.ai_chat.controller import (
    ChatSessionAccessDenied,
    ChatSessionRegistryPort,
)


class SQLiteChatRepository(ChatSessionRegistryPort):
    """Local durable implementation of the chat session, history, and memory ports."""

    def __init__(self, path: Path) -> None:
        self._path = path

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def create(
        self,
        *,
        user_id: str,
        tenant_id: str = "local",
        project_id: str = "default-project",
    ) -> ChatMemoryScope:
        return await asyncio.to_thread(self._create_sync, user_id, tenant_id, project_id)

    async def require(
        self,
        session_id: str,
        *,
        user_id: str,
        tenant_id: str = "local",
        connection: object | None = None,
    ) -> ChatMemoryScope:
        del connection
        return await asyncio.to_thread(self._require_sync, session_id, user_id, tenant_id)

    async def list_for(
        self, *, user_id: str, tenant_id: str = "local", project_id: str | None = None
    ) -> tuple[ChatMemoryScope, ...]:
        return await asyncio.to_thread(self._list_for_sync, user_id, tenant_id, project_id)

    async def delete(self, session_id: str, *, user_id: str, tenant_id: str = "local") -> bool:
        return await asyncio.to_thread(self._delete_session_sync, session_id, user_id, tenant_id)

    async def delete_project(
        self, *, user_id: str, project_id: str, tenant_id: str = "local"
    ) -> tuple[str, ...]:
        return await asyncio.to_thread(self._delete_project_sync, user_id, project_id, tenant_id)

    async def begin_turn(
        self,
        scope: ChatMemoryScope,
        turn: ChatTurn,
        *,
        idempotency_key: str,
        title: str,
    ) -> ChatTurn:
        return await asyncio.to_thread(self._begin_turn_sync, scope, turn, idempotency_key, title)

    async def update_turn(
        self, scope: ChatMemoryScope, turn: ChatTurn, *, title: str | None = None
    ) -> ChatTurn:
        return await asyncio.to_thread(self._update_turn_sync, scope, turn, title)

    async def write_turn(self, scope: ChatMemoryScope, turn: ChatTurn, *, title: str) -> None:
        if turn.assistant_message is None:
            raise ValueError("only completed assistant replies may enter chat history")
        completed = replace(
            turn,
            status=ChatTurnStatus.COMPLETED,
            idempotency_key=turn.idempotency_key or turn.turn_id,
            error_code=None,
        )
        await self.begin_turn(
            scope,
            completed,
            idempotency_key=completed.idempotency_key or completed.turn_id,
            title=title,
        )

    async def list_turns(
        self, scope: ChatMemoryScope, *, connection: object | None = None
    ) -> tuple[ChatTurn, ...]:
        del connection
        return await asyncio.to_thread(self._list_turns_sync, scope)

    async def list_owned_turns(
        self, *, session_id: str, tenant_id: str, user_id: str
    ) -> tuple[ChatMemoryScope, tuple[ChatTurn, ...]] | None:
        try:
            scope = await self.require(session_id, tenant_id=tenant_id, user_id=user_id)
        except ChatSessionAccessDenied:
            return None
        return scope, await self.list_turns(scope)

    async def titles_for(self, scopes: Sequence[ChatMemoryScope]) -> Mapping[str, str]:
        return await asyncio.to_thread(self._titles_for_sync, tuple(scopes))

    async def latest_turns_for(self, scopes: Sequence[ChatMemoryScope]) -> Mapping[str, ChatTurn]:
        return await asyncio.to_thread(self._latest_turns_for_sync, tuple(scopes))

    async def read_profile(self, namespace: MemoryNamespace) -> DeclarativeProfile | None:
        return await asyncio.to_thread(self._read_profile_sync, namespace)

    async def write_profile(
        self, namespace: MemoryNamespace, profile: DeclarativeProfile
    ) -> DeclarativeProfile:
        return await asyncio.to_thread(self._write_profile_sync, namespace, profile)

    async def delete_profile(self, namespace: MemoryNamespace) -> bool:
        return await asyncio.to_thread(self._delete_profile_sync, namespace)

    async def write_task_episode(
        self,
        namespace: MemoryNamespace,
        episode: TaskEpisode,
        *,
        expires_at: datetime | None,
    ) -> TaskEpisode:
        return await asyncio.to_thread(
            self._write_task_episode_sync, namespace, episode, expires_at
        )

    async def transition_task_episode(self, transition: EpisodeTransition) -> TaskEpisode | None:
        return await asyncio.to_thread(self._transition_task_episode_sync, transition)

    async def read_task_episode(
        self, namespace: MemoryNamespace, *, episode_id: str
    ) -> TaskEpisode | None:
        return await asyncio.to_thread(self._read_task_episode_sync, namespace, episode_id)

    async def read_episodes(
        self, namespace: MemoryNamespace, query: EpisodicMemoryQuery
    ) -> tuple[TaskEpisode, ...]:
        return await asyncio.to_thread(self._read_episodes_sync, namespace, query)

    async def list_episodes(
        self, namespace: MemoryNamespace, *, limit: int = 100
    ) -> tuple[TaskEpisode, ...]:
        return await asyncio.to_thread(self._list_episodes_sync, namespace, limit)

    async def delete_task_episode(self, namespace: MemoryNamespace, *, episode_id: str) -> bool:
        return await asyncio.to_thread(self._delete_task_episode_sync, namespace, episode_id)

    async def write_chat_summary(
        self, namespace: MemoryNamespace, episode: ChatSummaryEpisode
    ) -> ChatSummaryEpisode:
        return await asyncio.to_thread(self._write_chat_summary_sync, namespace, episode)

    async def delete_chat_summary(self, namespace: MemoryNamespace) -> bool:
        return await asyncio.to_thread(self._delete_chat_summary_sync, namespace)

    async def delete_all_for_user(self, namespace: MemoryNamespace) -> int:
        return await asyncio.to_thread(self._delete_all_for_user_sync, namespace)

    async def purge_expired(self, now: datetime) -> int:
        return await asyncio.to_thread(self._purge_expired_sync, now)

    def _initialize_sync(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as database:
            database.executescript(
                """
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    session_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    feature TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    title TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chat_turns (
                    session_id TEXT NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
                    turn_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, turn_id),
                    UNIQUE (session_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS chat_profiles (
                    profile_key TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    feature TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    expires_at TEXT
                );
                CREATE TABLE IF NOT EXISTS task_episodes (
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    feature TEXT NOT NULL,
                    chat_session_id TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    episode_id TEXT NOT NULL,
                    chat_turn_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    expires_at TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, user_id, feature, chat_session_id, record_id)
                );
                CREATE TABLE IF NOT EXISTS chat_summary_episodes (
                    summary_key TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    feature TEXT NOT NULL,
                    chat_session_id TEXT NOT NULL,
                    chat_turn_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    expires_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_chat_sessions_owner
                    ON chat_sessions (tenant_id, user_id, created_at, session_id);
                CREATE INDEX IF NOT EXISTS idx_task_episodes_owner
                    ON task_episodes (tenant_id, user_id, feature, updated_at DESC);
                """
            )

    def _create_sync(self, user_id: str, tenant_id: str, project_id: str) -> ChatMemoryScope:
        with self._connect() as database:
            while True:
                session_id = str(uuid4())
                try:
                    database.execute(
                        """
                        INSERT INTO chat_sessions
                            (session_id, tenant_id, user_id, feature, project_id, created_at)
                        VALUES (?, ?, ?, 'ai_chat', ?, ?)
                        """,
                        (session_id, tenant_id, user_id, project_id, _now_text()),
                    )
                except sqlite3.IntegrityError:
                    continue
                return ChatMemoryScope(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    session_id=session_id,
                    project_id=project_id,
                )

    def _require_sync(self, session_id: str, user_id: str, tenant_id: str) -> ChatMemoryScope:
        with self._connect() as database:
            row = database.execute(
                """
                SELECT tenant_id, user_id, session_id, feature, project_id
                FROM chat_sessions
                WHERE session_id = ? AND tenant_id = ? AND user_id = ?
                """,
                (session_id, tenant_id, user_id),
            ).fetchone()
        if row is None:
            raise ChatSessionAccessDenied(session_id)
        return _scope_from_row(row)

    def _list_for_sync(
        self, user_id: str, tenant_id: str, project_id: str | None
    ) -> tuple[ChatMemoryScope, ...]:
        sql = (
            "SELECT tenant_id, user_id, session_id, feature, project_id FROM chat_sessions "
            "WHERE tenant_id = ? AND user_id = ?"
        )
        params: tuple[object, ...] = (tenant_id, user_id)
        if project_id is not None:
            sql += " AND project_id = ?"
            params = (*params, project_id)
        sql += " ORDER BY created_at, session_id"
        with self._connect() as database:
            rows = database.execute(sql, params).fetchall()
        return tuple(_scope_from_row(row) for row in rows)

    def _delete_session_sync(self, session_id: str, user_id: str, tenant_id: str) -> bool:
        with self._connect() as database:
            cursor = database.execute(
                "DELETE FROM chat_sessions WHERE session_id = ? AND tenant_id = ? AND user_id = ?",
                (session_id, tenant_id, user_id),
            )
            if cursor.rowcount != 1:
                return False
            database.execute(
                "DELETE FROM task_episodes"
                " WHERE chat_session_id = ? AND tenant_id = ? AND user_id = ?",
                (session_id, tenant_id, user_id),
            )
            database.execute(
                "DELETE FROM chat_summary_episodes"
                " WHERE chat_session_id = ? AND tenant_id = ? AND user_id = ?",
                (session_id, tenant_id, user_id),
            )
        return True

    def _delete_project_sync(
        self, user_id: str, project_id: str, tenant_id: str
    ) -> tuple[str, ...]:
        with self._connect() as database:
            rows = database.execute(
                "SELECT session_id FROM chat_sessions"
                " WHERE tenant_id = ? AND user_id = ? AND project_id = ?",
                (tenant_id, user_id, project_id),
            ).fetchall()
            session_ids = tuple(str(row[0]) for row in rows)
            for session_id in session_ids:
                self._delete_session_sync(session_id, user_id, tenant_id)
        return session_ids

    def _begin_turn_sync(
        self, scope: ChatMemoryScope, turn: ChatTurn, idempotency_key: str, title: str
    ) -> ChatTurn:
        _validate_turn_scope(scope, turn)
        if turn.idempotency_key is not None and turn.idempotency_key != idempotency_key:
            raise ValueError("chat turn idempotency key must match begin_turn")
        with self._connect() as database:
            self._require_in_database(database, scope)
            existing = database.execute(
                "SELECT payload FROM chat_turns WHERE session_id = ? AND idempotency_key = ?",
                (scope.session_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                stored = _turn_from_payload(existing[0])
                if stored.user_message != turn.user_message:
                    raise ValueError("idempotency key was already used for another message")
                return stored
            stored = replace(turn, idempotency_key=idempotency_key)
            database.execute(
                """
                INSERT INTO chat_turns (session_id, turn_id, idempotency_key, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    scope.session_id,
                    stored.turn_id,
                    idempotency_key,
                    _dump(stored.to_dict()),
                    stored.created_at.isoformat(),
                ),
            )
            database.execute(
                "UPDATE chat_sessions SET title = COALESCE(title, ?) WHERE session_id = ?",
                (title, scope.session_id),
            )
        return stored

    def _update_turn_sync(
        self, scope: ChatMemoryScope, turn: ChatTurn, title: str | None
    ) -> ChatTurn:
        _validate_turn_scope(scope, turn)
        with self._connect() as database:
            self._require_in_database(database, scope)
            row = database.execute(
                "SELECT payload FROM chat_turns WHERE session_id = ? AND turn_id = ?",
                (scope.session_id, turn.turn_id),
            ).fetchone()
            if row is None:
                raise ValueError("chat turn was not found for its history scope")
            existing = _turn_from_payload(row[0])
            stored = replace(
                existing,
                assistant_message=turn.assistant_message,
                citation_coordinates=turn.citation_coordinates,
                rag_evidence=turn.rag_evidence,
                retrieval_status=turn.retrieval_status,
                mail_scan=turn.mail_scan,
                status=turn.status,
                error_code=turn.error_code,
                activities=turn.activities,
                completed_at=turn.completed_at,
                execution_trace=turn.execution_trace,
                artifact_refs=turn.artifact_refs,
            )
            database.execute(
                "UPDATE chat_turns SET payload = ? WHERE session_id = ? AND turn_id = ?",
                (_dump(stored.to_dict()), scope.session_id, turn.turn_id),
            )
            if title is not None:
                database.execute(
                    "UPDATE chat_sessions SET title = ? WHERE session_id = ?",
                    (title, scope.session_id),
                )
        return stored

    def _list_turns_sync(self, scope: ChatMemoryScope) -> tuple[ChatTurn, ...]:
        with self._connect() as database:
            try:
                self._require_in_database(database, scope)
            except ChatSessionAccessDenied:
                return ()
            rows = database.execute(
                "SELECT payload FROM chat_turns WHERE session_id = ? ORDER BY created_at, turn_id",
                (scope.session_id,),
            ).fetchall()
        return tuple(_turn_from_payload(row[0]) for row in rows)

    def _titles_for_sync(self, scopes: tuple[ChatMemoryScope, ...]) -> Mapping[str, str]:
        result: dict[str, str] = {}
        with self._connect() as database:
            for scope in scopes:
                row = database.execute(
                    """
                    SELECT title FROM chat_sessions
                    WHERE session_id = ? AND tenant_id = ? AND user_id = ? AND title IS NOT NULL
                    """,
                    (scope.session_id, scope.tenant_id, scope.user_id),
                ).fetchone()
                if row is not None:
                    result[scope.session_id] = str(row[0])
        return result

    def _latest_turns_for_sync(self, scopes: tuple[ChatMemoryScope, ...]) -> Mapping[str, ChatTurn]:
        result: dict[str, ChatTurn] = {}
        with self._connect() as database:
            for scope in scopes:
                if not self._is_owner_in_database(database, scope):
                    continue
                row = database.execute(
                    """
                    SELECT payload FROM chat_turns WHERE session_id = ?
                    ORDER BY created_at DESC, turn_id DESC LIMIT 1
                    """,
                    (scope.session_id,),
                ).fetchone()
                if row is not None:
                    result[scope.session_id] = _turn_from_payload(row[0])
        return result

    def _read_profile_sync(self, namespace: MemoryNamespace) -> DeclarativeProfile | None:
        key = _profile_key(namespace)
        with self._connect() as database:
            row = database.execute(
                "SELECT payload, expires_at FROM chat_profiles WHERE profile_key = ?", (key,)
            ).fetchone()
        if row is None or _expired(row[1]):
            return None
        return DeclarativeProfile.from_dict(_load(row[0]))

    def _write_profile_sync(
        self, namespace: MemoryNamespace, profile: DeclarativeProfile
    ) -> DeclarativeProfile:
        key = _profile_key(namespace)
        if profile.user_id != namespace.user_id:
            raise ValueError("profile namespace must match the profile user")
        with self._connect() as database:
            database.execute(
                """
                INSERT INTO chat_profiles (
                    profile_key, tenant_id, user_id, feature, payload, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_key) DO UPDATE SET
                    payload = excluded.payload, expires_at = excluded.expires_at
                """,
                (
                    key,
                    namespace.scope.tenant_id,
                    namespace.user_id,
                    namespace.feature,
                    _dump(profile.to_dict()),
                    _as_text(profile.expires_at),
                ),
            )
        return profile

    def _delete_profile_sync(self, namespace: MemoryNamespace) -> bool:
        with self._connect() as database:
            cursor = database.execute(
                "DELETE FROM chat_profiles WHERE profile_key = ?", (_profile_key(namespace),)
            )
        return cursor.rowcount == 1

    def _write_task_episode_sync(
        self, namespace: MemoryNamespace, episode: TaskEpisode, expires_at: datetime | None
    ) -> TaskEpisode:
        _validate_task_episode(namespace, episode, expires_at)
        with self._connect() as database:
            row = database.execute(
                """
                SELECT episode_id, chat_turn_id, payload FROM task_episodes
                WHERE tenant_id = ? AND user_id = ? AND feature = ?
                    AND chat_session_id = ? AND record_id = ?
                """,
                _episode_identity(namespace),
            ).fetchone()
            if row is not None:
                existing = TaskEpisode.from_dict(_load(row[2]))
                if str(row[0]) != episode.episode_id or str(row[1]) != episode.chat_turn_id:
                    raise ValueError("task episode immutable identity conflict")
                if (
                    existing.validation_status is not ValidationStatus.SYSTEM_GENERATED
                    or episode.updated_at < existing.updated_at
                ):
                    return existing
            database.execute(
                """
                INSERT INTO task_episodes
                    (tenant_id, user_id, feature, chat_session_id, record_id, episode_id,
                     chat_turn_id, payload, expires_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, user_id, feature, chat_session_id, record_id)
                DO UPDATE SET payload = excluded.payload, expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
                """,
                (
                    *_episode_identity(namespace),
                    episode.episode_id,
                    episode.chat_turn_id,
                    _dump(episode.to_dict()),
                    _as_text(expires_at),
                    episode.updated_at.isoformat(),
                ),
            )
        return episode

    def _transition_task_episode_sync(self, transition: EpisodeTransition) -> TaskEpisode | None:
        namespace = transition.namespace
        _require_episode_namespace(namespace, mutable=True)
        # source_id is the chat turn that produced the episode; mutable=True
        # above has already established both it and record_id are present.
        assert namespace.source_id is not None
        with self._connect() as database:
            row = database.execute(
                """
                SELECT payload, expires_at FROM task_episodes
                WHERE tenant_id = ? AND user_id = ? AND feature = ? AND chat_session_id = ?
                    AND record_id = ? AND chat_turn_id = ? AND episode_id = ?
                """,
                (*_episode_identity(namespace), namespace.source_id, transition.episode_id),
            ).fetchone()
            if row is None or _expired(row[1]):
                return None
            existing = TaskEpisode.from_dict(_load(row[0]))
            if (
                existing.validation_status is not transition.from_status
                or existing.updated_at > transition.transitioned_at
            ):
                return None
            updated = replace(
                existing,
                validation_status=transition.to_status,
                retrieval_eligible=transition.retrieval_eligible,
                updated_at=transition.transitioned_at,
            )
            database.execute(
                """
                UPDATE task_episodes SET payload = ?, updated_at = ?
                WHERE tenant_id = ? AND user_id = ? AND feature = ? AND chat_session_id = ?
                    AND record_id = ?
                """,
                (
                    _dump(updated.to_dict()),
                    updated.updated_at.isoformat(),
                    *_episode_identity(namespace),
                ),
            )
        return updated

    def _read_task_episode_sync(
        self, namespace: MemoryNamespace, episode_id: str
    ) -> TaskEpisode | None:
        _require_episode_namespace(namespace)
        if not episode_id:
            raise ValueError("episode_id must be a non-empty string")
        with self._connect() as database:
            row = database.execute(
                """
                SELECT payload, expires_at FROM task_episodes
                WHERE tenant_id = ? AND user_id = ? AND feature = ?
                    AND chat_session_id = ? AND episode_id = ?
                """,
                (
                    namespace.scope.tenant_id,
                    namespace.user_id,
                    namespace.feature,
                    namespace.session_id,
                    episode_id,
                ),
            ).fetchone()
        if row is None or _expired(row[1]):
            return None
        return TaskEpisode.from_dict(_load(row[0]))

    def _read_episodes_sync(
        self, namespace: MemoryNamespace, query: EpisodicMemoryQuery
    ) -> tuple[TaskEpisode, ...]:
        candidates = self._list_episodes_sync(namespace, MAX_EPISODIC_RETRIEVAL_ITEMS)
        terms = tuple(term for term in query.query.casefold().split() if term)
        ranked: list[tuple[float, TaskEpisode]] = []
        for episode in candidates:
            if not episode.retrieval_eligible or episode.validation_status not in {
                ValidationStatus.USER_APPROVED,
                ValidationStatus.COMPLETED,
            }:
                continue
            searchable = " ".join(
                (episode.task_title, episode.minimal_request_paraphrase, *episode.action_plan)
            ).casefold()
            score = sum(term in searchable for term in terms) / max(1, len(terms))
            if score >= query.min_score and score > 0:
                ranked.append((score, episode))
        ranked.sort(key=lambda item: (item[0], item[1].updated_at, item[1].record_id), reverse=True)
        return tuple(episode for _, episode in ranked[: query.max_items])

    def _list_episodes_sync(
        self, namespace: MemoryNamespace, limit: int
    ) -> tuple[TaskEpisode, ...]:
        _require_episode_namespace(namespace)
        with self._connect() as database:
            rows = database.execute(
                """
                SELECT payload, expires_at FROM task_episodes
                WHERE tenant_id = ? AND user_id = ? AND feature = ?
                ORDER BY updated_at DESC, record_id DESC
                LIMIT ?
                """,
                (
                    namespace.scope.tenant_id,
                    namespace.user_id,
                    namespace.feature,
                    max(1, min(limit, MAX_EPISODIC_RETRIEVAL_ITEMS)),
                ),
            ).fetchall()
        return tuple(TaskEpisode.from_dict(_load(row[0])) for row in rows if not _expired(row[1]))

    def _delete_task_episode_sync(self, namespace: MemoryNamespace, episode_id: str) -> bool:
        _require_episode_namespace(namespace, mutable=True)
        with self._connect() as database:
            cursor = database.execute(
                """
                DELETE FROM task_episodes
                WHERE tenant_id = ? AND user_id = ? AND feature = ? AND chat_session_id = ?
                    AND record_id = ? AND chat_turn_id = ? AND episode_id = ?
                """,
                (*_episode_identity(namespace), episode_id),
            )
        return cursor.rowcount == 1

    def _write_chat_summary_sync(
        self, namespace: MemoryNamespace, episode: ChatSummaryEpisode
    ) -> ChatSummaryEpisode:
        key = _summary_key(namespace, deleting=False)
        if episode.user_id != namespace.user_id or episode.chat_session_id != namespace.session_id:
            raise ValueError("chat summary namespace must match the episode identity")
        with self._connect() as database:
            database.execute(
                """
                INSERT INTO chat_summary_episodes
                    (
                        summary_key, tenant_id, user_id, feature, chat_session_id,
                        chat_turn_id, payload, expires_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(summary_key) DO UPDATE SET payload = excluded.payload,
                    expires_at = excluded.expires_at
                """,
                (
                    key,
                    namespace.scope.tenant_id,
                    namespace.user_id,
                    namespace.feature,
                    namespace.session_id,
                    episode.chat_turn_id,
                    _dump(episode.to_dict()),
                    _as_text(episode.expires_at),
                ),
            )
        return episode

    def _delete_chat_summary_sync(self, namespace: MemoryNamespace) -> bool:
        with self._connect() as database:
            cursor = database.execute(
                "DELETE FROM chat_summary_episodes WHERE summary_key = ?",
                (_summary_key(namespace, deleting=True),),
            )
        return cursor.rowcount == 1

    def _delete_all_for_user_sync(self, namespace: MemoryNamespace) -> int:
        _require_episode_namespace(namespace)
        with self._connect() as database:
            cursor = database.execute(
                "DELETE FROM task_episodes WHERE tenant_id = ? AND user_id = ? AND feature = ?",
                (namespace.scope.tenant_id, namespace.user_id, namespace.feature),
            )
        return cursor.rowcount

    def _purge_expired_sync(self, now: datetime) -> int:
        cutoff = now.isoformat()
        with self._connect() as database:
            profile_count = database.execute(
                "DELETE FROM chat_profiles WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (cutoff,),
            ).rowcount
            task_count = database.execute(
                "DELETE FROM task_episodes WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (cutoff,),
            ).rowcount
            summary_count = database.execute(
                "DELETE FROM chat_summary_episodes"
                " WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (cutoff,),
            ).rowcount
        return profile_count + task_count + summary_count

    def _connect(self) -> sqlite3.Connection:
        database = sqlite3.connect(self._path)
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA foreign_keys = ON")
        return database

    def _require_in_database(self, database: sqlite3.Connection, scope: ChatMemoryScope) -> None:
        if not self._is_owner_in_database(database, scope):
            raise ChatSessionAccessDenied(scope.session_id)

    @staticmethod
    def _is_owner_in_database(database: sqlite3.Connection, scope: ChatMemoryScope) -> bool:
        row = database.execute(
            "SELECT 1 FROM chat_sessions WHERE session_id = ? AND tenant_id = ? AND user_id = ?",
            (scope.session_id, scope.tenant_id, scope.user_id),
        ).fetchone()
        return row is not None


def _scope_from_row(row: sqlite3.Row) -> ChatMemoryScope:
    return ChatMemoryScope(
        tenant_id=str(row["tenant_id"]),
        user_id=str(row["user_id"]),
        session_id=str(row["session_id"]),
        feature=str(row["feature"]),
        project_id=str(row["project_id"]),
    )


def _validate_turn_scope(scope: ChatMemoryScope, turn: ChatTurn) -> None:
    if turn.session_id != scope.session_id:
        raise ValueError("chat turn session must match its history scope")


def _profile_key(namespace: MemoryNamespace) -> str:
    if namespace.memory_type is not MemoryType.LONG_TERM:
        raise ValueError("chat profiles require a long-term namespace")
    return "/".join((namespace.scope.tenant_id, namespace.user_id, namespace.feature, "long_term"))


def _require_episode_namespace(namespace: MemoryNamespace, *, mutable: bool = False) -> None:
    if namespace.memory_type is not MemoryType.EPISODIC:
        raise ValueError("task episodes require an episodic namespace")
    if mutable and (namespace.record_id is None or namespace.source_id is None):
        raise ValueError("task episode mutations require record and source identifiers")


def _episode_identity(namespace: MemoryNamespace) -> tuple[str, str, str, str, str]:
    _require_episode_namespace(namespace, mutable=True)
    assert namespace.record_id is not None
    return (
        namespace.scope.tenant_id,
        namespace.user_id,
        namespace.feature,
        namespace.session_id,
        namespace.record_id,
    )


def _validate_task_episode(
    namespace: MemoryNamespace, episode: TaskEpisode, expires_at: datetime | None
) -> None:
    _require_episode_namespace(namespace, mutable=True)
    if (
        namespace.user_id != episode.user_id
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
    if expires_at is not None and expires_at <= episode.created_at:
        raise ValueError("expires_at must be later than episode.created_at")


def _summary_key(namespace: MemoryNamespace, *, deleting: bool) -> str:
    if namespace.memory_type is not MemoryType.EPISODIC:
        raise ValueError("chat summaries require an episodic namespace")
    if deleting:
        if namespace.source_id is not None:
            raise ValueError("chat summary deletion requires an episodic record namespace")
    elif namespace.source_id is None:
        raise ValueError("chat summary writes require an episodic turn namespace")
    return "/".join(
        (
            namespace.scope.tenant_id,
            namespace.user_id,
            namespace.feature,
            namespace.session_id,
            namespace.record_id or "",
            namespace.source_id or "",
        )
    )


def _turn_from_payload(payload: object) -> ChatTurn:
    return ChatTurn.from_dict(_load(payload))


def _load(payload: object) -> Mapping[str, object]:
    loaded = json.loads(str(payload))
    if not isinstance(loaded, dict):
        raise ValueError("stored chat payload must be an object")
    return cast(Mapping[str, object], loaded)


def _dump(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _as_text(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _expired(value: object) -> bool:
    if value is None:
        return False
    return datetime.fromisoformat(str(value)) <= datetime.now(UTC)


def _now_text() -> str:
    return datetime.now(UTC).isoformat()
