"""PostgreSQL identities, workspaces, and hashed opaque sessions."""

from datetime import datetime
from typing import cast
from uuid import uuid4

from psycopg_pool import AsyncConnectionPool

from cowork_agent.domain import MailboxConnection
from cowork_agent.identity import VerifiedPrincipal
from cowork_agent.security.sessions import new_session_token, session_expiry, session_token_hash


class PostgresIdentityRepository:
    """Resolves Gmail's verified email to stable application-owned IDs."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def resolve_or_create_principal(self, email_address: str) -> VerifiedPrincipal:
        normalized_email = email_address.strip().lower()
        if not normalized_email:
            raise ValueError("Verified Gmail email address must not be empty")
        async with self._pool.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))", (normalized_email,)
                )
                cursor = await connection.execute(
                    """
                    INSERT INTO app_users (id, primary_email) VALUES (%s, %s)
                    ON CONFLICT (primary_email) DO UPDATE SET primary_email = EXCLUDED.primary_email
                    RETURNING id
                    """,
                    (uuid4(), normalized_email),
                )
                user_row = await cursor.fetchone()
                assert user_row is not None
                user_id = str(user_row[0])
                cursor = await connection.execute(
                    """
                    SELECT workspace_id FROM workspace_members
                    WHERE user_id = %s AND role = 'owner'
                    ORDER BY created_at, workspace_id
                    LIMIT 1
                    """,
                    (user_id,),
                )
                workspace_row = await cursor.fetchone()
                if workspace_row is None:
                    workspace_id = str(uuid4())
                    await connection.execute(
                        "INSERT INTO workspaces (id, name) VALUES (%s, %s)",
                        (workspace_id, "Personal workspace"),
                    )
                    await connection.execute(
                        """
                        INSERT INTO workspace_members (workspace_id, user_id, role)
                        VALUES (%s, %s, 'owner')
                        """,
                        (workspace_id, user_id),
                    )
                else:
                    workspace_id = str(workspace_row[0])
        return VerifiedPrincipal(user_id=user_id)


class PostgresSessionRepository:
    """Opaque sessions that persist a hash, never their plaintext token."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def create(
        self,
        principal: VerifiedPrincipal,
        *,
        now: datetime,
        ttl_seconds: int,
    ) -> tuple[str, datetime]:
        token = new_session_token()
        expires_at = session_expiry(now, ttl_seconds)
        async with self._pool.connection() as connection:
            await connection.execute(
                """
                INSERT INTO app_sessions (token_hash, user_id, workspace_id, expires_at, created_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    session_token_hash(token),
                    principal.user_id,
                    "local",
                    expires_at,
                    now,
                ),
            )
        return token, expires_at

    async def resolve(self, token: str, *, now: datetime) -> VerifiedPrincipal | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT sessions.workspace_id, sessions.user_id
                FROM app_sessions AS sessions
                JOIN workspace_members AS members
                  ON members.workspace_id = sessions.workspace_id
                 AND members.user_id = sessions.user_id
                WHERE sessions.token_hash = %s
                  AND sessions.revoked_at IS NULL
                  AND sessions.expires_at > %s
                """,
                (session_token_hash(token), now),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return VerifiedPrincipal(user_id=str(row[1]))

    async def revoke(self, token: str, *, now: datetime) -> bool:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE app_sessions SET revoked_at = %s
                WHERE token_hash = %s AND revoked_at IS NULL AND expires_at > %s
                """,
                (now, session_token_hash(token), now),
            )
        return cursor.rowcount == 1


class PostgresMailboxConnectionRepository:
    """Mailbox-connection persistence for the Supabase PostgreSQL runtime."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def upsert(self, connection: MailboxConnection) -> MailboxConnection:
        workspace_id = await self._default_workspace_for_user(connection.user_id)
        return await self.upsert_for_workspace(connection, workspace_id=workspace_id)

    async def upsert_for_workspace(
        self, connection: MailboxConnection, *, workspace_id: str
    ) -> MailboxConnection:
        async with self._pool.connection() as database:
            cursor = await database.execute(
                """
                INSERT INTO mailbox_connections (
                    id, user_id, provider, external_account_id, email_address,
                    encrypted_refresh_token, scopes, status, workspace_id, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, provider, external_account_id) DO UPDATE SET
                    email_address = EXCLUDED.email_address,
                    encrypted_refresh_token = EXCLUDED.encrypted_refresh_token,
                    scopes = EXCLUDED.scopes,
                    status = EXCLUDED.status,
                    workspace_id = EXCLUDED.workspace_id,
                    updated_at = EXCLUDED.updated_at
                RETURNING id, user_id, provider, external_account_id, email_address,
                    encrypted_refresh_token, scopes, status, created_at, updated_at
                """,
                (
                    connection.id,
                    connection.user_id,
                    connection.provider,
                    connection.external_account_id.lower(),
                    connection.email_address.lower(),
                    connection.encrypted_refresh_token,
                    list(connection.scopes),
                    connection.status,
                    workspace_id,
                    connection.created_at,
                    connection.updated_at,
                ),
            )
            row = await cursor.fetchone()
        assert row is not None
        return _mailbox_connection_from_row(row)

    async def get(self, connection_id: str) -> MailboxConnection | None:
        async with self._pool.connection() as database:
            cursor = await database.execute(
                """
                SELECT id, user_id, provider, external_account_id, email_address,
                    encrypted_refresh_token, scopes, status, created_at, updated_at
                FROM mailbox_connections WHERE id = %s
                """,
                (connection_id,),
            )
            row = await cursor.fetchone()
        return None if row is None else _mailbox_connection_from_row(row)

    async def list_for_user(self, user_id: str) -> tuple[MailboxConnection, ...]:
        async with self._pool.connection() as database:
            cursor = await database.execute(
                """
                SELECT id, user_id, provider, external_account_id, email_address,
                    encrypted_refresh_token, scopes, status, created_at, updated_at
                FROM mailbox_connections WHERE user_id = %s ORDER BY created_at, id
                """,
                (user_id,),
            )
            rows = await cursor.fetchall()
        return tuple(_mailbox_connection_from_row(row) for row in rows)

    async def delete(self, connection_id: str, user_id: str) -> bool:
        async with self._pool.connection() as database:
            cursor = await database.execute(
                "DELETE FROM mailbox_connections WHERE id = %s AND user_id = %s",
                (connection_id, user_id),
            )
        return cursor.rowcount == 1

    async def _default_workspace_for_user(self, user_id: str) -> str:
        async with self._pool.connection() as database:
            cursor = await database.execute(
                """
                SELECT workspace_id FROM workspace_members
                WHERE user_id = %s AND role = 'owner'
                ORDER BY created_at, workspace_id
                LIMIT 1
                """,
                (user_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            raise LookupError("User has no owned workspace")
        return str(row[0])


def _mailbox_connection_from_row(row: tuple[object, ...]) -> MailboxConnection:
    return MailboxConnection(
        id=str(row[0]),
        user_id=str(row[1]),
        provider=str(row[2]),
        external_account_id=str(row[3]),
        email_address=str(row[4]),
        encrypted_refresh_token=str(row[5]),
        scopes=tuple(cast(list[str], row[6])),
        status=str(row[7]),
        created_at=cast(datetime, row[8]),
        updated_at=cast(datetime, row[9]),
    )
