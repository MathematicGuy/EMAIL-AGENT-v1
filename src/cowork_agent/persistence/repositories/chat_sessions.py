"""Durable authorization metadata for AI Chat sessions."""

from collections.abc import Callable
from uuid import uuid4

from psycopg_pool import AsyncConnectionPool

from cowork_agent.domain.chat_contracts import ChatMemoryScope
from cowork_agent.features.ai_chat.controller import (
    ChatSessionAccessDenied,
    ChatSessionRegistryPort,
)

IdFactory = Callable[[], str]


def _new_id() -> str:
    return str(uuid4())


class PostgresChatSessionRegistry(ChatSessionRegistryPort):
    """PostgreSQL session registry that requires current workspace membership."""

    def __init__(self, pool: AsyncConnectionPool, *, new_id: IdFactory = _new_id) -> None:
        self._pool = pool
        self._new_id = new_id

    async def create(self, *, tenant_id: str, user_id: str) -> ChatMemoryScope:
        scope = ChatMemoryScope(tenant_id=tenant_id, user_id=user_id, session_id=self._new_id())
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                INSERT INTO chat_sessions (id, workspace_id, user_id, feature)
                SELECT %s, members.workspace_id, members.user_id, %s
                FROM workspace_members AS members
                WHERE members.workspace_id = %s AND members.user_id = %s
                RETURNING id
                """,
                (scope.session_id, scope.feature, tenant_id, user_id),
            )
            row = await cursor.fetchone()
        if row is None:
            raise ChatSessionAccessDenied(scope.session_id)
        return scope

    async def require(
        self, session_id: str, *, tenant_id: str, user_id: str
    ) -> ChatMemoryScope:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT sessions.workspace_id, sessions.user_id, sessions.id, sessions.feature
                FROM chat_sessions AS sessions
                JOIN workspace_members AS members
                  ON members.workspace_id = sessions.workspace_id
                 AND members.user_id = sessions.user_id
                WHERE sessions.id = %s
                  AND sessions.workspace_id = %s
                  AND sessions.user_id = %s
                """,
                (session_id, tenant_id, user_id),
            )
            row = await cursor.fetchone()
        if row is None:
            raise ChatSessionAccessDenied(session_id)
        return ChatMemoryScope(
            tenant_id=str(row[0]), user_id=str(row[1]), session_id=str(row[2]), feature=str(row[3])
        )

    async def list_for(self, *, tenant_id: str, user_id: str) -> tuple[ChatMemoryScope, ...]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT sessions.workspace_id, sessions.user_id, sessions.id, sessions.feature
                FROM chat_sessions AS sessions
                JOIN workspace_members AS members
                  ON members.workspace_id = sessions.workspace_id
                 AND members.user_id = sessions.user_id
                WHERE sessions.workspace_id = %s AND sessions.user_id = %s
                ORDER BY sessions.created_at, sessions.id
                """,
                (tenant_id, user_id),
            )
            rows = await cursor.fetchall()
        return tuple(
            ChatMemoryScope(
                tenant_id=str(row[0]),
                user_id=str(row[1]),
                session_id=str(row[2]),
                feature=str(row[3]),
            )
            for row in rows
        )
