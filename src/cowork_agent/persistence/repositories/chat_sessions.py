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

    async def create(
        self,
        *,
        user_id: str,
        tenant_id: str = "local",
        project_id: str = "default-project",
    ) -> ChatMemoryScope:
        session_id = self._new_id()
        project_filter = "AND projects.is_default = TRUE"
        params: tuple[object, ...] = (session_id, "ai_chat", user_id, tenant_id)
        if project_id != "default-project":
            project_filter = "AND projects.id = %s"
            params = (*params, project_id)
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                f"""
                INSERT INTO chat_sessions (id, workspace_id, user_id, project_id, feature)
                SELECT %s, members.workspace_id, members.user_id, projects.id, %s
                FROM workspace_members AS members
                JOIN projects
                  ON projects.workspace_id = members.workspace_id
                 AND projects.owner_user_id = members.user_id
                WHERE members.user_id = %s
                  AND members.workspace_id = %s
                  {project_filter}
                RETURNING id, project_id
                """,
                params,
            )
            row = await cursor.fetchone()
        if row is None:
            raise ChatSessionAccessDenied(session_id)
        return ChatMemoryScope(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=str(row[0]),
            project_id=str(row[1]),
        )

    async def require(
        self, session_id: str, *, user_id: str, tenant_id: str = "local"
    ) -> ChatMemoryScope:
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
            row = await cursor.fetchone()
        if row is None:
            raise ChatSessionAccessDenied(session_id)
        return ChatMemoryScope(
            tenant_id=str(row[0]),
            user_id=str(row[1]),
            session_id=str(row[2]),
            feature=str(row[3]),
            project_id=str(row[4]),
        )

    async def list_for(
        self, *, user_id: str, tenant_id: str = "local", project_id: str | None = None
    ) -> tuple[ChatMemoryScope, ...]:
        project_filter = "" if project_id is None else "AND sessions.project_id = %s"
        params: tuple[object, ...] = (
            (user_id, tenant_id)
            if project_id is None
            else (user_id, tenant_id, project_id)
        )
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                f"""
                SELECT sessions.workspace_id, sessions.user_id, sessions.id,
                       sessions.feature, sessions.project_id
                FROM chat_sessions AS sessions
                JOIN workspace_members AS members
                  ON members.workspace_id = sessions.workspace_id
                 AND members.user_id = sessions.user_id
                WHERE sessions.user_id = %s
                  AND sessions.workspace_id = %s
                  {project_filter}
                ORDER BY sessions.created_at, sessions.id
                """,
                params,
            )
            rows = await cursor.fetchall()
        return tuple(
            ChatMemoryScope(
                tenant_id=str(row[0]),
                user_id=str(row[1]),
                session_id=str(row[2]),
                feature=str(row[3]),
                project_id=str(row[4]),
            )
            for row in rows
        )

    async def delete(
        self, session_id: str, *, user_id: str, tenant_id: str = "local"
    ) -> bool:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                DELETE FROM chat_sessions
                WHERE id = %s AND workspace_id = %s AND user_id = %s
                RETURNING id
                """,
                (session_id, tenant_id, user_id),
            )
            return await cursor.fetchone() is not None

    async def delete_project(
        self, *, user_id: str, project_id: str, tenant_id: str = "local"
    ) -> tuple[str, ...]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                DELETE FROM chat_sessions
                WHERE workspace_id = %s AND user_id = %s AND project_id = %s
                RETURNING id
                """,
                (tenant_id, user_id, project_id),
            )
            return tuple(str(row[0]) for row in await cursor.fetchall())
