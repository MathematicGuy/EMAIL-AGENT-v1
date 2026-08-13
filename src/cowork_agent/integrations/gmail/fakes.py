"""Local mailbox and attachment adapters for deterministic tests."""

import hashlib
from collections.abc import AsyncIterator, Sequence
from datetime import datetime

from cowork_agent.domain import ExtractedAttachment, ExtractedUnit
from cowork_agent.domain.target_contracts import EphemeralEmailEnvelope
from cowork_agent.features.email_action_plan.schemas import (
    ExtractionLimits,
    MessageRef,
    SearchPage,
)


class FakeMailbox:
    """Read-only mailbox fixture. It intentionally exposes no mutation method."""

    def __init__(
        self,
        messages: Sequence[EphemeralEmailEnvelope] = (),
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
        refs = tuple(MessageRef(item.gmail_message_id, item.gmail_thread_id) for item in page)
        return SearchPage(refs, next_cursor, len(self.messages))

    async def get_thread(
        self, connection_id: str, thread_id: str
    ) -> Sequence[EphemeralEmailEnvelope]:
        del connection_id
        return tuple(item for item in self.messages if item.gmail_thread_id == thread_id)

    async def get_message_received_at(self, connection_id: str, message_id: str) -> datetime:
        del connection_id
        return next(
            item.received_at for item in self.messages if item.gmail_message_id == message_id
        )

    async def download_attachment(
        self, connection_id: str, message_id: str, attachment_id: str, max_bytes: int
    ) -> AsyncIterator[bytes]:
        """Deprecated (ADR-003 transition clause): compatibility code, never wired in production."""
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
