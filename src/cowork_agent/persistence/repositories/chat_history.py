"""Durable, owned AI Chat turn history for the conversation UI."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import cast

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from cowork_agent.domain.chat_contracts import ChatMemoryScope, ChatTurn, ChatTurnStatus


class PostgresChatHistoryRepository:
    """Persist turn lifecycles and LLM-generated titles for owned chat sessions."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def begin_turn(
        self,
        scope: ChatMemoryScope,
        turn: ChatTurn,
        *,
        idempotency_key: str,
        title: str,
    ) -> ChatTurn:
        """Atomically insert a submission or return its prior idempotent result."""
        _validate_scope(scope, turn)
        if turn.idempotency_key is not None and turn.idempotency_key != idempotency_key:
            raise ValueError("chat turn idempotency key must match begin_turn")
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                INSERT INTO chat_turns (
                    session_id, turn_id, user_message, assistant_message,
                    citation_coordinates, rag_evidence, retrieval_status, mail_scan,
                    created_at, status, idempotency_key, error_code
                )
                SELECT sessions.id, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                FROM chat_sessions AS sessions
                WHERE sessions.id = %s
                  AND sessions.workspace_id = %s
                  AND sessions.user_id = %s
                ON CONFLICT (session_id, idempotency_key) DO UPDATE
                SET idempotency_key = EXCLUDED.idempotency_key
                RETURNING turn_id, session_id, user_message, assistant_message, created_at,
                          citation_coordinates, rag_evidence, retrieval_status, mail_scan,
                          status, idempotency_key, error_code
                """,
                (
                    turn.turn_id,
                    turn.user_message,
                    turn.assistant_message,
                    Jsonb([dict(item) for item in turn.citation_coordinates]),
                    Jsonb([item.to_dict() for item in turn.rag_evidence]),
                    turn.retrieval_status,
                    Jsonb(turn.mail_scan.to_dict()) if turn.mail_scan is not None else None,
                    turn.created_at,
                    turn.status,
                    idempotency_key,
                    turn.error_code,
                    scope.session_id,
                    scope.tenant_id,
                    scope.user_id,
                ),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ValueError("chat session was not found for its history scope")
            if str(row[2]) != turn.user_message:
                raise ValueError("idempotency key was already used for another message")
            await _set_title_if_missing(connection, scope, title)
        persisted = _turn_from_row(row)
        if persisted.user_message != turn.user_message:
            raise ValueError("idempotency key was already used for another message")
        return persisted

    async def update_turn(
        self,
        scope: ChatMemoryScope,
        turn: ChatTurn,
        *,
        title: str | None = None,
    ) -> ChatTurn:
        """Update the assistant result and lifecycle of one existing durable turn."""
        _validate_scope(scope, turn)
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE chat_turns AS turns
                SET assistant_message = %s,
                    citation_coordinates = %s,
                    rag_evidence = %s,
                    retrieval_status = %s,
                    mail_scan = %s,
                    status = %s,
                    error_code = %s
                FROM chat_sessions AS sessions
                WHERE turns.session_id = sessions.id
                  AND turns.session_id = %s
                  AND turns.turn_id = %s
                  AND sessions.workspace_id = %s
                  AND sessions.user_id = %s
                RETURNING turns.turn_id, turns.session_id, turns.user_message,
                          turns.assistant_message, turns.created_at,
                          turns.citation_coordinates, turns.rag_evidence,
                          turns.retrieval_status, turns.mail_scan, turns.status,
                          turns.idempotency_key, turns.error_code
                """,
                (
                    turn.assistant_message,
                    Jsonb([dict(item) for item in turn.citation_coordinates]),
                    Jsonb([item.to_dict() for item in turn.rag_evidence]),
                    turn.retrieval_status,
                    Jsonb(turn.mail_scan.to_dict()) if turn.mail_scan is not None else None,
                    turn.status,
                    turn.error_code,
                    scope.session_id,
                    turn.turn_id,
                    scope.tenant_id,
                    scope.user_id,
                ),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ValueError("chat turn was not found for its history scope")
            if title is not None:
                await _set_title(connection, scope, title)
        return _turn_from_row(row)

    async def write_turn(
        self, scope: ChatMemoryScope, turn: ChatTurn, *, title: str
    ) -> None:
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
        if connection is None:
            async with self._pool.connection() as borrowed:
                return await self.list_turns(scope, connection=borrowed)
        cursor = await connection.execute(  # type: ignore[attr-defined]
            """
            SELECT turns.turn_id, turns.session_id, turns.user_message,
                   turns.assistant_message, turns.created_at,
                   turns.citation_coordinates, turns.rag_evidence,
                   turns.retrieval_status, turns.mail_scan, turns.status,
                   turns.idempotency_key, turns.error_code
            FROM chat_turns AS turns
            JOIN chat_sessions AS sessions ON sessions.id = turns.session_id
            WHERE turns.session_id = %s
              AND sessions.workspace_id = %s
              AND sessions.user_id = %s
            ORDER BY turns.created_at, turns.turn_id
            """,
            (scope.session_id, scope.tenant_id, scope.user_id),
        )
        rows = await cursor.fetchall()
        return tuple(_turn_from_row(row) for row in rows)

    async def list_owned_turns(
        self, *, session_id: str, tenant_id: str, user_id: str
    ) -> tuple[ChatMemoryScope, tuple[ChatTurn, ...]] | None:
        """Return owned turns, or None when the session is missing or not owned."""
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT sessions.workspace_id, sessions.user_id, sessions.id,
                       sessions.feature, sessions.project_id
                FROM chat_sessions AS sessions
                JOIN workspace_members AS members
                  ON members.workspace_id = sessions.workspace_id
                 AND members.user_id = sessions.user_id
                WHERE sessions.id = %s
                  AND sessions.user_id = %s
                  AND sessions.workspace_id = %s
                """,
                (session_id, user_id, tenant_id),
            )
            session_row = await cursor.fetchone()
            if session_row is None:
                return None
            scope = ChatMemoryScope(
                tenant_id=str(session_row[0]),
                user_id=str(session_row[1]),
                session_id=str(session_row[2]),
                feature=str(session_row[3]),
                project_id=str(session_row[4]),
            )
            turns = await self.list_turns(scope, connection=connection)
        return scope, turns

    async def titles_for(self, scopes: Sequence[ChatMemoryScope]) -> Mapping[str, str]:
        if not scopes:
            return {}
        session_ids = [scope.session_id for scope in scopes]
        owner = scopes[0]
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT id, title
                FROM chat_sessions
                WHERE id = ANY(%s)
                  AND workspace_id = %s
                  AND user_id = %s
                  AND title IS NOT NULL
                """,
                (session_ids, owner.tenant_id, owner.user_id),
            )
            rows = await cursor.fetchall()
        return {str(row[0]): str(row[1]) for row in rows}

    async def latest_turns_for(
        self, scopes: Sequence[ChatMemoryScope]
    ) -> Mapping[str, ChatTurn]:
        """Return the newest durable turn for each requested owned session."""
        if not scopes:
            return {}
        session_ids = [scope.session_id for scope in scopes]
        owner = scopes[0]
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT DISTINCT ON (turns.session_id)
                       turns.turn_id, turns.session_id, turns.user_message,
                       turns.assistant_message, turns.created_at,
                       turns.citation_coordinates, turns.rag_evidence,
                       turns.retrieval_status, turns.mail_scan, turns.status,
                       turns.idempotency_key, turns.error_code
                FROM chat_turns AS turns
                JOIN chat_sessions AS sessions ON sessions.id = turns.session_id
                WHERE turns.session_id = ANY(%s)
                  AND sessions.workspace_id = %s
                  AND sessions.user_id = %s
                ORDER BY turns.session_id, turns.created_at DESC, turns.turn_id DESC
                """,
                (session_ids, owner.tenant_id, owner.user_id),
            )
            rows = await cursor.fetchall()
        turns = (_turn_from_row(row) for row in rows)
        return {turn.session_id: turn for turn in turns}


def _turn_from_row(row: Sequence[object]) -> ChatTurn:
    citations = cast(list[object], row[5])
    evidence = cast(list[object], row[6])
    return ChatTurn.from_dict(
        {
            "turn_id": str(row[0]),
            "session_id": str(row[1]),
            "user_message": str(row[2]),
            "assistant_message": None if row[3] is None else str(row[3]),
            "created_at": cast(datetime, row[4]).isoformat(),
            "citation_coordinates": citations,
            "rag_evidence": evidence,
            "retrieval_status": None if row[7] is None else str(row[7]),
            "mail_scan": cast(dict[str, object] | None, row[8]),
            "status": str(row[9]),
            "idempotency_key": str(row[10]),
            "error_code": None if row[11] is None else str(row[11]),
        }
    )


def _validate_scope(scope: ChatMemoryScope, turn: ChatTurn) -> None:
    if turn.session_id != scope.session_id:
        raise ValueError("chat turn session must match its history scope")


async def _set_title_if_missing(
    connection: object, scope: ChatMemoryScope, title: str
) -> None:
    await connection.execute(  # type: ignore[attr-defined]
        """
        UPDATE chat_sessions
        SET title = %s, updated_at = now()
        WHERE id = %s
          AND workspace_id = %s
          AND user_id = %s
          AND title IS NULL
        """,
        (title, scope.session_id, scope.tenant_id, scope.user_id),
    )


async def _set_title(connection: object, scope: ChatMemoryScope, title: str) -> None:
    await connection.execute(  # type: ignore[attr-defined]
        """
        UPDATE chat_sessions
        SET title = %s, updated_at = now()
        WHERE id = %s
          AND workspace_id = %s
          AND user_id = %s
        """,
        (title, scope.session_id, scope.tenant_id, scope.user_id),
    )
