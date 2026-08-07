"""Ports owned by the application layer."""

from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from typing import Protocol

from cowork_agent.domain import (
    ActionItem,
    AttachmentWarning,
    DigestRun,
    ExtractedAttachment,
    MailboxConnection,
    ProcessedEmail,
    RunStatus,
)
from cowork_agent.domain.target_contracts import (
    EphemeralEmailEnvelope,
    SemanticRetrievalRequest,
    SemanticRetrievalResponse,
)

from .schemas import ClassificationResult, ExtractionBatch, ExtractionLimits, SearchPage


class MailboxPort(Protocol):
    async def search_unread(
        self, connection_id: str, query: str, page_size: int, cursor: str | None = None
    ) -> SearchPage: ...

    async def get_thread(
        self, connection_id: str, thread_id: str
    ) -> Sequence[EphemeralEmailEnvelope]: ...

    def download_attachment(
        self, connection_id: str, message_id: str, attachment_id: str, max_bytes: int
    ) -> AsyncIterator[bytes]:
        """Deprecated (ADR-003 transition clause): compatibility code, never wired in production."""
        ...


class MailboxConnectionRepository(Protocol):
    async def upsert(self, connection: MailboxConnection) -> MailboxConnection: ...
    async def get(self, connection_id: str) -> MailboxConnection | None: ...
    async def list_for_user(self, user_id: str) -> Sequence[MailboxConnection]: ...
    async def delete(self, connection_id: str, user_id: str) -> bool: ...


class AttachmentExtractorPort(Protocol):
    async def extract(
        self,
        attachment_id: str,
        filename: str,
        declared_mime_type: str,
        content: AsyncIterator[bytes],
        limits: ExtractionLimits,
    ) -> ExtractedAttachment: ...


class RouteClassifierPort(Protocol):
    """One structured Route Decision per selected email (PRD-v1 FR-05, §6.2).

    Implementations decide actionability and knowledge sufficiency only; the
    deterministic Route Resolver owns the final route (master-comparison §3.6).
    """

    async def classify(
        self,
        user_timezone: str,
        current_time: datetime,
        messages: Sequence[EphemeralEmailEnvelope],
    ) -> ClassificationResult: ...


class ActionPlanGeneratorPort(Protocol):
    """Compatibility bridge used by the V1-M2 rewiring.

    V1-M3 T3.3 replaces it with the ActionPlanOutput-based Generator.
    """

    async def generate(
        self,
        user_timezone: str,
        current_time: datetime,
        messages: Sequence[EphemeralEmailEnvelope],
    ) -> ExtractionBatch: ...


class RunRepository(Protocol):
    async def create(self, run: DigestRun) -> tuple[DigestRun, bool]: ...
    async def get(self, run_id: str) -> DigestRun | None: ...
    async def claim(self, run_id: str, started_at: datetime) -> DigestRun | None: ...
    async def save(self, run: DigestRun) -> None: ...


class ResultRepository(Protocol):
    async def save_items(self, run_id: str, items: Sequence[ActionItem]) -> None: ...
    async def list_items(self, run_id: str) -> Sequence[ActionItem]: ...
    async def save_warning(self, run_id: str, warning: AttachmentWarning) -> None: ...
    async def list_warnings(self, run_id: str) -> Sequence[AttachmentWarning]: ...
    async def fingerprint_seen(self, mailbox_id: str, fingerprint: str) -> bool: ...
    async def save_processed_emails(
        self, run_id: str, emails: Sequence[ProcessedEmail]
    ) -> None: ...
    async def list_processed_emails(self, run_id: str) -> Sequence[ProcessedEmail]: ...


class SemanticMemoryPort(Protocol):
    """Retrieval-only Semantic Memory boundary (PRD-v1 FR-08, §6.4/§6.5).

    Returns chunks, citation metadata, and scores only. There is no
    retrieve-and-answer operation: generation stays in the Generator.
    """

    async def retrieve(
        self, request: SemanticRetrievalRequest
    ) -> SemanticRetrievalResponse: ...


TERMINAL_STATUSES = frozenset({RunStatus.SUCCEEDED, RunStatus.PARTIAL, RunStatus.FAILED})
