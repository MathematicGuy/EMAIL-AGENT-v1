"""Route mailbox reads using the provider stored on the connection."""

from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import datetime

from cowork_agent.domain.target_contracts import EphemeralEmailEnvelope
from cowork_agent.features.email_action_plan.ports import (
    MailboxConnectionRepository,
    MailboxPort,
)
from cowork_agent.features.email_action_plan.schemas import SearchPage

from .errors import MailboxNotConnectedError, MailboxTemporaryError


class ProviderRoutingMailboxAdapter:
    def __init__(
        self,
        repository: MailboxConnectionRepository,
        adapters: Mapping[str, MailboxPort],
    ) -> None:
        self._repository = repository
        self._adapters = dict(adapters)

    async def _adapter(self, connection_id: str) -> MailboxPort:
        connection = await self._repository.get(connection_id)
        if connection is None or connection.status != "active":
            raise MailboxNotConnectedError(connection_id)
        adapter = self._adapters.get(connection.provider)
        if adapter is None:
            raise MailboxTemporaryError("Mailbox provider is not configured")
        return adapter

    async def search_unread(
        self,
        connection_id: str,
        query: str,
        page_size: int,
        cursor: str | None = None,
    ) -> SearchPage:
        adapter = await self._adapter(connection_id)
        return await adapter.search_unread(connection_id, query, page_size, cursor)

    async def get_thread(
        self, connection_id: str, thread_id: str
    ) -> Sequence[EphemeralEmailEnvelope]:
        adapter = await self._adapter(connection_id)
        return await adapter.get_thread(connection_id, thread_id)

    async def get_message_received_at(
        self, connection_id: str, message_id: str
    ) -> datetime:
        adapter = await self._adapter(connection_id)
        return await adapter.get_message_received_at(connection_id, message_id)

    async def download_attachment(
        self,
        connection_id: str,
        message_id: str,
        attachment_id: str,
        max_bytes: int,
    ) -> AsyncIterator[bytes]:
        adapter = await self._adapter(connection_id)
        async for chunk in adapter.download_attachment(
            connection_id, message_id, attachment_id, max_bytes
        ):
            yield chunk
