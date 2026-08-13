"""Idempotent expiry and physical cleanup for Project documents."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Protocol

from cowork_agent.features.user_documents.ports import ProjectDocumentRepositoryPort
from cowork_agent.integrations.project_documents.encrypted_store import EncryptedDocumentStore

logger = logging.getLogger(__name__)


class DocumentVectorDeletionPort(Protocol):
    async def delete_document(self, document_id: str) -> None: ...


class DocumentRetentionManager:
    def __init__(
        self,
        *,
        documents: ProjectDocumentRepositoryPort,
        store: EncryptedDocumentStore,
        vectors: DocumentVectorDeletionPort,
        interval_seconds: float = 3600,
    ) -> None:
        self._documents = documents
        self._store = store
        self._vectors = vectors
        self._interval = interval_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def run_once(self) -> int:
        now = datetime.now(UTC)
        await self._documents.mark_expired_deleted(at=now)
        completed = 0
        for document_id in await self._documents.cleanup_candidates():
            try:
                await self._vectors.delete_document(document_id)
                self._store.delete(document_id)
                await self._documents.confirm_cleanup(document_id, at=datetime.now(UTC))
                completed += 1
            except Exception:
                logger.exception("Project document cleanup remains pending: %s", document_id)
        return completed

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task

    async def _run(self) -> None:
        while not self._stop.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except TimeoutError:
                pass
