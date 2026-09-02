"""Local opaque guest identities and sessions for the SQLite chat runtime."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from cowork_agent.identity import VerifiedPrincipal
from cowork_agent.security.sessions import new_session_token, session_expiry, session_token_hash


class SQLiteChatIdentityRepository:
    """Persist guest principals and hashed browser session tokens locally."""

    def __init__(self, path: Path) -> None:
        self._path = path

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    async def resolve_or_create_principal(self, identifier: str) -> VerifiedPrincipal:
        return await asyncio.to_thread(self._resolve_or_create_principal, identifier)

    async def create(
        self,
        principal: VerifiedPrincipal,
        *,
        now: datetime,
        ttl_seconds: int,
    ) -> tuple[str, datetime]:
        token = new_session_token()
        expires_at = session_expiry(now, ttl_seconds)
        await asyncio.to_thread(self._create_session, token, principal, now, expires_at)
        return token, expires_at

    async def resolve(self, token: str, *, now: datetime) -> VerifiedPrincipal | None:
        return await asyncio.to_thread(self._resolve, token, now)

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS local_chat_identities (
                    identifier TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS local_chat_sessions (
                    token_hash TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    revoked_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_local_chat_sessions_expiry
                    ON local_chat_sessions (expires_at);
                """
            )

    def _resolve_or_create_principal(self, identifier: str) -> VerifiedPrincipal:
        normalized = identifier.strip().lower()
        if not normalized:
            raise ValueError("chat identity must not be empty")
        with self._connect() as db:
            row = db.execute(
                "SELECT tenant_id, user_id FROM local_chat_identities WHERE identifier=?",
                (normalized,),
            ).fetchone()
            if row is None:
                principal = VerifiedPrincipal(tenant_id=str(uuid4()), user_id=str(uuid4()))
                db.execute(
                    "INSERT INTO local_chat_identities VALUES (?, ?, ?)",
                    (normalized, principal.tenant_id, principal.user_id),
                )
                return principal
        return VerifiedPrincipal(tenant_id=str(row["tenant_id"]), user_id=str(row["user_id"]))

    def _create_session(
        self,
        token: str,
        principal: VerifiedPrincipal,
        now: datetime,
        expires_at: datetime,
    ) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO local_chat_sessions VALUES (?, ?, ?, ?, ?, NULL)",
                (
                    session_token_hash(token),
                    principal.tenant_id,
                    principal.user_id,
                    expires_at.isoformat(),
                    now.isoformat(),
                ),
            )

    def _resolve(self, token: str, now: datetime) -> VerifiedPrincipal | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT tenant_id, user_id FROM local_chat_sessions
                WHERE token_hash=? AND revoked_at IS NULL AND expires_at > ?
                """,
                (session_token_hash(token), now.isoformat()),
            ).fetchone()
        if row is None:
            return None
        return VerifiedPrincipal(tenant_id=str(row["tenant_id"]), user_id=str(row["user_id"]))

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=5000")
        return db
