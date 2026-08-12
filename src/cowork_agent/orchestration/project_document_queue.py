"""Metadata-only Redis Stream for private project-document ingestion."""

from __future__ import annotations

import socket
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import ResponseError

PROJECT_DOCUMENT_STREAM = "project:document-ingestion"
PROJECT_DOCUMENT_GROUP = "project-document-workers"


class ProjectDocumentExecutor(Protocol):
    async def execute(self, document_id: str) -> None: ...


class RedisProjectDocumentQueue:
    """Enqueues only opaque document IDs; bytes and extracted text never enter Redis."""

    def __init__(self, redis: Redis, *, stream: str = PROJECT_DOCUMENT_STREAM) -> None:
        self._redis = redis
        self._stream = stream

    async def enqueue(self, document_id: str) -> None:
        if not document_id.strip():
            raise ValueError("document_id must not be empty")
        await self._redis.xadd(
            self._stream,
            {"document_id": document_id, "enqueued_at": datetime.now(UTC).isoformat()},
            maxlen=10_000,
            approximate=True,
        )


class RedisProjectDocumentConsumer:
    """Small consumer: repository CAS determines whether a delivery is actionable."""

    def __init__(
        self,
        redis: Redis,
        executor: ProjectDocumentExecutor,
        *,
        stream: str = PROJECT_DOCUMENT_STREAM,
        group: str = PROJECT_DOCUMENT_GROUP,
        consumer_name: str | None = None,
        block_ms: int = 500,
    ) -> None:
        self._redis = redis
        self._executor = executor
        self._stream = stream
        self._group = group
        self._consumer = consumer_name or f"{socket.gethostname()}-{uuid4().hex[:8]}"
        self._block_ms = block_ms

    async def ensure_group(self) -> None:
        try:
            await self._redis.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def deliver_once(self) -> int:
        response = await self._redis.xreadgroup(
            self._group,
            self._consumer,
            {self._stream: ">"},
            count=1,
            block=self._block_ms,
        )
        handled = 0
        for _stream, messages in response or []:
            for message_id, fields in messages:
                await self._handle(message_id, fields)
                handled += 1
        return handled

    async def run_forever(self) -> None:
        await self.ensure_group()
        while True:
            await self.deliver_once()

    async def _handle(self, message_id: str, fields: Mapping[str, str]) -> None:
        document_id = str(fields.get("document_id", ""))
        if not document_id:
            await self._redis.xack(self._stream, self._group, message_id)
            return
        # The executor converts controlled extract/index failures to a durable
        # safe state. Infrastructure failures remain pending for redelivery.
        await self._executor.execute(document_id)
        await self._redis.xack(self._stream, self._group, message_id)
