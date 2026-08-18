"""GET /messages should borrow one pool connection for require + list_turns."""

from __future__ import annotations

import asyncio

from cowork_agent.api.chat import load_owned_history
from cowork_agent.domain.chat_contracts import ChatMemoryScope, ChatTurn
from cowork_agent.identity import VerifiedPrincipal


class _CountingPool:
    def __init__(self) -> None:
        self.checkouts = 0

    def connection(self) -> _CountingCheckout:
        return _CountingCheckout(self)


class _CountingCheckout:
    def __init__(self, pool: _CountingPool) -> None:
        self._pool = pool
        self.connection = object()

    async def __aenter__(self) -> object:
        self._pool.checkouts += 1
        return self.connection

    async def __aexit__(self, *_args: object) -> None:
        return None


class _PooledSessions:
    def __init__(self, pool: _CountingPool) -> None:
        self._pool = pool
        self.seen: list[object | None] = []

    async def require(
        self,
        session_id: str,
        *,
        user_id: str,
        tenant_id: str = "local",
        connection: object | None = None,
    ) -> ChatMemoryScope:
        self.seen.append(connection)
        return ChatMemoryScope(tenant_id, user_id, session_id)


class _PooledHistory:
    def __init__(self, pool: _CountingPool) -> None:
        self._pool = pool
        self.seen: list[object | None] = []

    async def list_turns(
        self, scope: ChatMemoryScope, *, connection: object | None = None
    ) -> tuple[ChatTurn, ...]:
        del scope
        self.seen.append(connection)
        return ()


def test_load_owned_history_uses_one_pool_checkout_for_require_and_list_turns() -> None:
    async def scenario() -> None:
        pool = _CountingPool()
        sessions = _PooledSessions(pool)
        history = _PooledHistory(pool)
        principal = VerifiedPrincipal(tenant_id="tenant-1", user_id="user@example.com")
        _scope, turns = await load_owned_history(
            sessions=sessions,
            history=history,
            buffer=None,
            principal=principal,
            session_id="session-1",
        )
        assert turns == ()
        assert pool.checkouts == 1
        assert sessions.seen[0] is history.seen[0]
        assert sessions.seen[0] is not None

    asyncio.run(scenario())
