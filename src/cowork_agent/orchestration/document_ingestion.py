"""Metadata-only Redis/local dispatch and startup recovery for document ingestion."""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from collections.abc import Callable, Coroutine, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from redis.exceptions import ResponseError

from cowork_agent.domain.project_documents import ProjectDocument
from cowork_agent.features.user_documents.ports import ProjectDocumentRepositoryPort

ProcessDocument = Callable[[ProjectDocument], Coroutine[Any, Any, object]]
logger = logging.getLogger(__name__)


class RedisStreamPort(Protocol):
    async def xadd(self, name: str, fields: dict[str, str]) -> object: ...


class DocumentIngestionDispatcher:
    def __init__(
        self,
        *,
        documents: ProjectDocumentRepositoryPort,
        process: ProcessDocument,
        stream_name: str,
        redis: RedisStreamPort | None = None,
    ) -> None:
        self._documents = documents
        self._process = process
        self._stream_name = stream_name
        self._redis = redis
        self._tasks: set[asyncio.Task[object]] = set()

    @property
    def mode(self) -> str:
        return "redis" if self._redis is not None else "local"

    async def dispatch(self, document: ProjectDocument) -> None:
        metadata = {
            "document_id": document.document_id,
            "project_id": document.project_id,
            "tenant_id": document.tenant_id,
            "user_id": document.user_id,
        }
        if self._redis is not None:
            await self._redis.xadd(self._stream_name, metadata)
            return
        task: asyncio.Task[object] = asyncio.create_task(self._process(document))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def recover(self) -> int:
        ids = await self._documents.reclaimable(now=datetime.now(UTC))
        count = 0
        for document_id in ids:
            # Recovery only needs IDs in Redis. The worker reloads and validates
            # scope from PostgreSQL before claiming; local mode is composed with
            # a closure that can perform the same reload.
            if self._redis is not None:
                await self._redis.xadd(self._stream_name, {"document_id": document_id})
                count += 1
            else:
                document = await self._documents.get_job(document_id)
                if document is not None:
                    await self.dispatch(document)
                    count += 1
        return count

    async def close(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)


class RedisDocumentIngestionConsumer:
    """At-least-once Redis consumer with PostgreSQL lease/CAS authority."""

    def __init__(
        self,
        *,
        redis: Any,
        documents: ProjectDocumentRepositoryPort,
        process: ProcessDocument,
        stream_name: str,
        group: str = "project-document-workers",
        consumer_name: str | None = None,
        block_ms: int = 500,
        claim_min_idle_ms: int = 30_000,
        recovery_interval_seconds: float = 60,
    ) -> None:
        self._redis = redis
        self._documents = documents
        self._process = process
        self._stream = stream_name
        self._group = group
        self._consumer = consumer_name or f"{socket.gethostname()}-{uuid4().hex[:8]}"
        self._block_ms = block_ms
        self._claim_min_idle_ms = claim_min_idle_ms
        self._recovery_interval = recovery_interval_seconds

    async def ensure_group(self) -> None:
        try:
            await self._redis.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def deliver_once(self) -> int:
        handled = await self.claim_stale()
        response = await self._redis.xreadgroup(
            self._group,
            self._consumer,
            {self._stream: ">"},
            count=1,
            block=self._block_ms,
        )
        for _stream, messages in response or []:
            for message_id, fields in messages:
                handled += await self._handle(message_id, fields)
        return handled

    async def claim_stale(self) -> int:
        pending = await self._redis.xpending_range(
            self._stream, self._group, min="-", max="+", count=100
        )
        handled = 0
        for entry in pending:
            if entry["time_since_delivered"] < self._claim_min_idle_ms:
                continue
            claimed = await self._redis.xclaim(
                self._stream,
                self._group,
                self._consumer,
                min_idle_time=self._claim_min_idle_ms,
                message_ids=(entry["message_id"],),
            )
            for message_id, fields in claimed:
                handled += await self._handle(message_id, fields or {})
        return handled

    async def _handle(self, message_id: str, fields: Mapping[str, str]) -> int:
        document_id = str(fields.get("document_id", ""))
        if not document_id:
            await self._redis.xack(self._stream, self._group, message_id)
            return 1
        document = await self._documents.get_job(document_id)
        if document is None:
            await self._redis.xack(self._stream, self._group, message_id)
            return 1
        try:
            await self._process(document)
        except Exception:
            logger.exception("Project document ingestion will be redelivered: %s", document_id)
            return 0
        await self._redis.xack(self._stream, self._group, message_id)
        return 1

    async def recover(self) -> int:
        ids = await self._documents.reclaimable(now=datetime.now(UTC))
        for document_id in ids:
            await self._redis.xadd(self._stream, {"document_id": document_id})
        return len(ids)

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        await self.ensure_group()
        next_recovery = 0.0
        while stop is None or not stop.is_set():
            if time.monotonic() >= next_recovery:
                try:
                    await self.recover()
                except Exception:
                    logger.exception("Project document recovery scan failed")
                next_recovery = time.monotonic() + self._recovery_interval
            await self.deliver_once()
