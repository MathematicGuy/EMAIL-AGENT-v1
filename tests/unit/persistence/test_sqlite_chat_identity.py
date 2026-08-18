"""SQLite guest chat identities persist hashed opaque sessions."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from cowork_agent.persistence.repositories.sqlite_chat_identity import (
    SQLiteChatIdentityRepository,
)


def test_sqlite_guest_chat_session_survives_repository_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "chat_identity.db"
        repository = SQLiteChatIdentityRepository(path)
        await repository.initialize()
        principal = await repository.resolve_or_create_principal("guest-1@guest.invalid")
        token, _ = await repository.create(principal, now=datetime.now(UTC), ttl_seconds=60)

        restarted = SQLiteChatIdentityRepository(path)
        await restarted.initialize()
        assert await restarted.resolve(token, now=datetime.now(UTC)) == principal

    asyncio.run(scenario())
