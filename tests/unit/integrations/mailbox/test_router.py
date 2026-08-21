from datetime import UTC, datetime
from typing import Any

import pytest

from cowork_agent.domain import MailboxConnection
from cowork_agent.features.email_action_plan.schemas import SearchPage
from cowork_agent.integrations.mailbox import (
    MailboxNotConnectedError,
    MailboxTemporaryError,
    ProviderRoutingMailboxAdapter,
)


class Repository:
    def __init__(self, connection: MailboxConnection | None) -> None:
        self.connection = connection

    async def get(self, connection_id: str) -> MailboxConnection | None:
        del connection_id
        return self.connection


class Adapter:
    async def search_unread(
        self, connection_id: str, query: str, page_size: int, cursor: str | None = None
    ) -> SearchPage:
        del connection_id, query, page_size, cursor
        return SearchPage(())

    async def get_thread(self, connection_id: str, thread_id: str) -> tuple[()]:
        del connection_id, thread_id
        return ()

    async def get_message_received_at(self, connection_id: str, message_id: str) -> datetime:
        del connection_id, message_id
        return datetime(2026, 1, 1, tzinfo=UTC)

    async def download_attachment(self, *args: Any) -> Any:
        del args
        if False:
            yield b""


def connection(provider: str = "outlook", status: str = "active") -> MailboxConnection:
    now = datetime.now(UTC)
    return MailboxConnection(
        "mbx", "user", provider, "account", "user@example.com", "token", (), status, now, now
    )


@pytest.mark.asyncio
async def test_router_dispatches_by_stored_provider() -> None:
    expected = Adapter()
    router = ProviderRoutingMailboxAdapter(Repository(connection()), {"outlook": expected})  # type: ignore[arg-type]
    assert await router.search_unread("mbx", "unread_inbox", 10) == SearchPage(())


@pytest.mark.asyncio
async def test_router_rejects_inactive_and_unconfigured_connections() -> None:
    inactive = ProviderRoutingMailboxAdapter(Repository(connection(status="inactive")), {})  # type: ignore[arg-type]
    with pytest.raises(MailboxNotConnectedError):
        await inactive.search_unread("mbx", "unread_inbox", 10)

    missing = ProviderRoutingMailboxAdapter(Repository(connection()), {})  # type: ignore[arg-type]
    with pytest.raises(MailboxTemporaryError) as raised:
        await missing.search_unread("mbx", "unread_inbox", 10)
    assert raised.value.safe_message == "The email service is temporarily unavailable."
