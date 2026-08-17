"""Durable, owned AI Chat turn history for the conversation UI."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import cast

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from cowork_agent.domain.chat_contracts import ChatMemoryScope, ChatTurn


class PostgresChatHistoryRepository:
    """Persist completed turns and LLM-generated titles for owned chat sessions."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def write_turn(
        self, scope: ChatMemoryScope, turn: ChatTurn, *, title: str
    ) -> None:
        if turn.session_id != scope.session_id:
            raise ValueError("chat turn session must match its history scope")
        if turn.assistant_message is None:
            raise ValueError("only completed assistant replies may enter chat history")
        async with self._pool.connection() as connection:
            await connection.execute(
                """
                INSERT INTO chat_turns (
                    session_id, turn_id, user_message, assistant_message,
                    citation_coordinates, rag_evidence, retrieval_status, mail_scan, created_at
                )
                SELECT sessions.id, %s, %s, %s, %s, %s, %s, %s, %s
                FROM chat_sessions AS sessions
                WHERE sessions.id = %s
                  AND sessions.workspace_id = %s
                  AND sessions.user_id = %s
                ON CONFLICT (session_id, turn_id) DO NOTHING
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
                    scope.session_id,
                    scope.tenant_id,
                    scope.user_id,
                ),
            )
            await connection.execute(
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

    async def list_turns(self, scope: ChatMemoryScope) -> tuple[ChatTurn, ...]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT turns.turn_id, turns.session_id, turns.user_message,
                       turns.assistant_message, turns.created_at,
                       turns.citation_coordinates, turns.rag_evidence,
                       turns.retrieval_status, turns.mail_scan
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


def _turn_from_row(row: Sequence[object]) -> ChatTurn:
    citations = cast(list[object], row[5])
    evidence = cast(list[object], row[6])
    return ChatTurn.from_dict(
        {
            "turn_id": str(row[0]),
            "session_id": str(row[1]),
            "user_message": str(row[2]),
            "assistant_message": str(row[3]),
            "created_at": cast(datetime, row[4]).isoformat(),
            "citation_coordinates": citations,
            "rag_evidence": evidence,
            "retrieval_status": None if row[7] is None else str(row[7]),
            "mail_scan": cast(dict[str, object] | None, row[8]),
        }
    )
