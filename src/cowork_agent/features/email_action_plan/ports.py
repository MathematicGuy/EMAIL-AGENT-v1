"""Ports owned by the application layer."""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from cowork_agent.domain import (
    ActionFreshness,
    AttachmentWarning,
    DigestCompletedEvent,
    DigestRun,
    ExtractedAttachment,
    MailboxConnection,
    ProcessedEmail,
    RunStatus,
)
from cowork_agent.domain.target_contracts import (
    ActionPlanOutput,
    EphemeralEmailEnvelope,
    SemanticRetrievalRequest,
    SemanticRetrievalResponse,
    Task,
)

from .correlation import TaskCandidate
from .routing import RouteResolution
from .schemas import ClassificationResult, ExtractionLimits, GenerationContext, SearchPage


class MailboxTemporaryError(RuntimeError):
    """Transient mailbox failure; safe to retry or skip (V1-H T5.4)."""


class MailboxPort(Protocol):
    async def search_unread(
        self, connection_id: str, query: str, page_size: int, cursor: str | None = None
    ) -> SearchPage: ...

    async def get_thread(
        self, connection_id: str, thread_id: str
    ) -> Sequence[EphemeralEmailEnvelope]: ...

    async def get_message_received_at(
        self, connection_id: str, message_id: str
    ) -> datetime:
        """Return Gmail's internal receipt time without fetching an email body."""
        ...

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
    """One structured generation call per resolved Task Candidate.

    PRD-v1 FR-09 / master-comparison §3.8: after route resolution and
    optional retrieval, the Generator is called exactly once per resolved
    non-``NO_ACTION`` Task Candidate and returns exactly one Task
    (master-comparison §6.6). Inputs are the ephemeral email context, the
    Route Decisions, the Route Resolution, and retrieved RAG context only —
    v1 never feeds long-term or episodic memory. ``retrieval`` stays
    ``None`` until T3.5 wires the Semantic Memory port.
    """

    async def generate(
        self,
        *,
        user_timezone: str,
        current_time: datetime,
        run_context: GenerationContext,
        candidate: TaskCandidate,
        envelopes: Sequence[EphemeralEmailEnvelope],
        resolution: RouteResolution,
        retrieval: SemanticRetrievalResponse | None,
    ) -> ActionPlanOutput: ...


class RunRepository(Protocol):
    async def create(self, run: DigestRun) -> tuple[DigestRun, bool]: ...
    async def get(self, run_id: str) -> DigestRun | None: ...
    async def list_recent(
        self, *, user_id: str, mailbox_connection_id: str, limit: int
    ) -> Sequence[DigestRun]: ...
    async def claim(self, run_id: str, started_at: datetime) -> DigestRun | None: ...
    async def save(self, run: DigestRun) -> None: ...
    async def list_stuck_runs(
        self, *, running_before: datetime, queued_before: datetime
    ) -> Sequence[DigestRun]:
        """Runs stuck in RUNNING past ``running_before`` or in QUEUED past
        ``queued_before`` (V1-H T5.4 recovery sweep)."""
        ...

    async def reset_stuck_run(self, run_id: str, *, started_before: datetime) -> bool:
        """Compare-and-set recovery reset: back to QUEUED only if the run is
        still RUNNING with ``started_at`` before ``started_before``. Returns
        whether the reset happened (V1-H T5.4)."""
        ...


class ResultRepository(Protocol):
    async def save_warning(self, run_id: str, warning: AttachmentWarning) -> None: ...
    async def list_warnings(self, run_id: str) -> Sequence[AttachmentWarning]: ...
    async def save_processed_emails(
        self, run_id: str, emails: Sequence[ProcessedEmail]
    ) -> None: ...
    async def list_processed_emails(self, run_id: str) -> Sequence[ProcessedEmail]: ...


class CompletionOutboxPort(Protocol):
    """Durable lifecycle events for terminal runs (V1-H T5.3).

    Payloads are metadata-only (run/user IDs, status, timestamp) — never
    email content (invariant 1). ``add`` is idempotent per run; ``pending``
    and ``mark_published`` drive publication to observers."""

    async def add(self, event: DigestCompletedEvent) -> None: ...
    async def pending(self) -> Sequence[DigestCompletedEvent]: ...
    async def mark_published(self, run_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class TaskPointer:
    """Body-free Gmail pointer metadata accompanying one persisted Task.

    These fields reconstruct the legacy Action Item surface (sender, subject,
    thread, deep link) from durable storage; the raw email body is never
    among them (invariant 1).
    """

    mailbox_connection_id: str
    provider_thread_id: str
    sender_name: str | None
    sender_address: str
    email_subject: str
    email_received_at: datetime


@dataclass(frozen=True, slots=True)
class PersistedTask:
    """One durable Task row plus the metadata the compat mapper needs.

    ``freshness`` is stamped at save time (``new`` when no row with the same
    Mailbox Connection + fingerprint existed yet, ``seen`` otherwise) and
    stored per run link, freezing the legacy cross-run recall semantics at
    persistence instead of recomputing them at read time.
    """

    task: Task
    pointer: TaskPointer
    fingerprint: str
    freshness: ActionFreshness = ActionFreshness.NEW


class TaskRepository(Protocol):
    """Durable persistence for validated §6.6 Tasks (PRD-v1 FR-12, T4.1/T4.2).

    ``save_task`` is idempotent on the key
    ``tenant_id:user_id:gmail_message_id:pipeline_version``: re-saving a Task
    for the same Gmail message under the same pipeline version updates the
    stored row instead of duplicating it, so idempotent run replays stay safe.
    Every run that produces a row is linked to it, so ``list_for_run`` can
    rebuild any run's legacy result even after later runs updated the row.
    Rows never contain raw email bodies (invariant 1).
    """

    async def save_task(
        self,
        record: PersistedTask,
        *,
        tenant_id: str,
        user_id: str,
        pipeline_version: str,
        run_id: str,
    ) -> None: ...

    async def list_for_run(self, run_id: str) -> Sequence[PersistedTask]: ...


class SemanticMemoryPort(Protocol):
    """Retrieval-only Semantic Memory boundary (PRD-v1 FR-08, §6.4/§6.5).

    Returns chunks, citation metadata, and scores only. There is no
    retrieve-and-answer operation: generation stays in the Generator.
    """

    async def retrieve(
        self, request: SemanticRetrievalRequest
    ) -> SemanticRetrievalResponse: ...


TERMINAL_STATUSES = frozenset({RunStatus.SUCCEEDED, RunStatus.PARTIAL, RunStatus.FAILED})
