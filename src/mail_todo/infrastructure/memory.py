"""In-memory adapters for local development and deterministic tests."""

import hashlib
from collections.abc import AsyncIterator, Sequence
from datetime import datetime

from mail_todo.application.contracts import (
    ExtractionBatch,
    ExtractionLimits,
    MessageRef,
    SearchPage,
    ThreadContext,
)
from mail_todo.domain import (
    ActionItem,
    AttachmentWarning,
    DigestCompletedEvent,
    DigestRun,
    EmailEnvelope,
    ExtractedAttachment,
    ExtractedUnit,
    ProcessedEmail,
    RunStatus,
)


class InMemoryRunRepository:
    def __init__(self) -> None:
        self.runs: dict[str, DigestRun] = {}
        self._idempotency: dict[tuple[str, str], str] = {}

    async def create(self, run: DigestRun) -> tuple[DigestRun, bool]:
        key = (run.user_id, run.idempotency_key)
        existing_id = self._idempotency.get(key)
        if existing_id:
            return self.runs[existing_id], False
        self.runs[run.id], self._idempotency[key] = run, run.id
        return run, True

    async def get(self, run_id: str) -> DigestRun | None:
        return self.runs.get(run_id)

    async def claim(self, run_id: str, started_at: datetime) -> DigestRun | None:
        run = self.runs.get(run_id)
        if run is None or run.status is not RunStatus.QUEUED:
            return None
        run.status, run.started_at = RunStatus.RUNNING, started_at
        return run

    async def save(self, run: DigestRun) -> None:
        self.runs[run.id] = run


class InMemoryResultRepository:
    def __init__(self) -> None:
        self.items: dict[str, list[ActionItem]] = {}
        self.warnings: dict[str, list[AttachmentWarning]] = {}
        self.processed_emails: dict[str, list[ProcessedEmail]] = {}

    async def save_items(self, run_id: str, items: Sequence[ActionItem]) -> None:
        self.items[run_id] = list(items)

    async def list_items(self, run_id: str) -> Sequence[ActionItem]:
        return tuple(self.items.get(run_id, ()))

    async def save_warning(self, run_id: str, warning: AttachmentWarning) -> None:
        self.warnings.setdefault(run_id, []).append(warning)

    async def list_warnings(self, run_id: str) -> Sequence[AttachmentWarning]:
        return tuple(self.warnings.get(run_id, ()))

    async def fingerprint_seen(self, mailbox_id: str, fingerprint: str) -> bool:
        return any(
            item.mailbox_connection_id == mailbox_id and item.fingerprint == fingerprint
            for values in self.items.values()
            for item in values
        )

    async def save_processed_emails(self, run_id: str, emails: Sequence[ProcessedEmail]) -> None:
        self.processed_emails[run_id] = list(emails)

    async def list_processed_emails(self, run_id: str) -> Sequence[ProcessedEmail]:
        return tuple(self.processed_emails.get(run_id, ()))


class InMemoryQueue:
    def __init__(self) -> None:
        self.run_ids: list[str] = []

    async def enqueue_digest_run(self, run_id: str) -> None:
        if run_id not in self.run_ids:
            self.run_ids.append(run_id)


class InMemoryOutbox:
    def __init__(self) -> None:
        self.events: dict[str, DigestCompletedEvent] = {}
        self.published: set[str] = set()

    async def add(self, event: DigestCompletedEvent) -> None:
        self.events.setdefault(event.run_id, event)

    async def pending(self) -> Sequence[DigestCompletedEvent]:
        return tuple(event for key, event in self.events.items() if key not in self.published)

    async def mark_published(self, run_id: str) -> None:
        self.published.add(run_id)


class FakeMailbox:
    """Read-only mailbox fixture. It intentionally exposes no mutation method."""

    def __init__(
        self,
        messages: Sequence[EmailEnvelope] = (),
        attachment_bytes: dict[str, bytes] | None = None,
    ) -> None:
        self.messages = tuple(messages)
        self.attachment_bytes = attachment_bytes or {}

    async def search_unread(
        self, connection_id: str, query: str, page_size: int, cursor: str | None = None
    ) -> SearchPage:
        del connection_id, query
        start = int(cursor or 0)
        page = self.messages[start : start + page_size]
        next_cursor = str(start + page_size) if start + page_size < len(self.messages) else None
        refs = tuple(MessageRef(item.provider_message_id, item.provider_thread_id) for item in page)
        return SearchPage(refs, next_cursor, len(self.messages))

    async def get_thread(self, connection_id: str, thread_id: str) -> Sequence[EmailEnvelope]:
        del connection_id
        return tuple(item for item in self.messages if item.provider_thread_id == thread_id)

    async def download_attachment(
        self, connection_id: str, message_id: str, attachment_id: str, max_bytes: int
    ) -> AsyncIterator[bytes]:
        del connection_id, message_id
        data = self.attachment_bytes[attachment_id]
        for offset in range(0, len(data), 64 * 1024):
            chunk = data[offset : offset + 64 * 1024]
            if offset + len(chunk) > max_bytes:
                raise ValueError("ATTACHMENT_TOO_LARGE")
            yield chunk


class SafeTextAttachmentExtractor:
    """Minimal local adapter; rich document parsing belongs in an isolated service."""

    _TEXT_MIMES = frozenset({"text/plain", "text/csv", "application/json"})

    async def extract(
        self,
        attachment_id: str,
        filename: str,
        declared_mime_type: str,
        content: AsyncIterator[bytes],
        limits: ExtractionLimits,
    ) -> ExtractedAttachment:
        if declared_mime_type not in self._TEXT_MIMES:
            return ExtractedAttachment(
                attachment_id,
                filename,
                declared_mime_type,
                "",
                "skipped",
                None,
                (),
                "ATTACHMENT_UNSUPPORTED",
            )
        chunks, size = [], 0
        async for chunk in content:
            size += len(chunk)
            if size > limits.max_bytes:
                return ExtractedAttachment(
                    attachment_id,
                    filename,
                    declared_mime_type,
                    "",
                    "skipped",
                    None,
                    (),
                    "ATTACHMENT_TOO_LARGE",
                )
            chunks.append(chunk)
        raw = b"".join(chunks)
        text = raw.decode("utf-8-sig")[: limits.max_characters]
        return ExtractedAttachment(
            attachment_id,
            filename,
            declared_mime_type,
            hashlib.sha256(raw).hexdigest(),
            "extracted",
            text,
            (ExtractedUnit("section", "content", text),),
        )


class FakeActionExtractor:
    def __init__(self, batch: ExtractionBatch) -> None:
        self.batch = batch
        self.received_threads: tuple[ThreadContext, ...] = ()

    async def extract(
        self, user_timezone: str, current_time: datetime, threads: Sequence[ThreadContext]
    ) -> ExtractionBatch:
        del user_timezone, current_time
        self.received_threads = tuple(threads)
        return self.batch
