"""Domain entities for the unread-email digest."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class RunTrigger(StrEnum):
    ON_DEMAND = "on_demand"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class Priority(StrEnum):
    URGENT = "urgent"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class DeadlineSource(StrEnum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    NONE = "none"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ActionFreshness(StrEnum):
    NEW = "new"
    SEEN = "seen"
    CHANGED = "changed"


@dataclass(frozen=True, slots=True)
class MailboxConnection:
    id: str
    user_id: str
    provider: str
    external_account_id: str
    email_address: str
    encrypted_refresh_token: str
    scopes: tuple[str, ...]
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class CalendarConnection:
    """One user's Google Calendar grant.

    A sibling of `MailboxConnection` rather than a variant of it. Mail routing
    iterates mailbox connections, so a calendar row living in that table becomes
    a mail-routing bug the first time someone adds a provider branch
    (SPEC-per-user-google-calendar-oauth J7).
    """

    id: str
    user_id: str
    provider: str
    external_account_id: str
    calendar_id: str
    encrypted_refresh_token: str
    scopes: tuple[str, ...]
    timezone: str
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ProcessedEmail:
    provider_message_id: str
    provider_thread_id: str
    subject: str
    sender_address: str
    received_at: datetime


@dataclass(frozen=True, slots=True)
class ExtractedUnit:
    kind: str
    label: str
    text: str


@dataclass(frozen=True, slots=True)
class ExtractedAttachment:
    attachment_id: str
    filename: str
    detected_mime_type: str
    sha256: str
    status: str
    text: str | None
    units: tuple[ExtractedUnit, ...] = ()
    warning_code: str | None = None


@dataclass(frozen=True, slots=True)
class ActionPlanStep:
    order: int
    instruction: str
    basis: str


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    source_kind: str
    filename: str | None
    location: str | None
    excerpt: str
    source_message_id: str | None = None


@dataclass(frozen=True, slots=True)
class ActionItem:
    id: str
    run_id: str
    mailbox_connection_id: str
    provider_message_id: str
    provider_thread_id: str
    fingerprint: str
    freshness: ActionFreshness
    title: str
    summary: str
    sender_name: str | None
    sender_address: str
    email_subject: str
    email_received_at: datetime
    email_deep_link: str | None
    deadline_at: datetime | None
    deadline_source: DeadlineSource
    deadline_text: str | None
    priority: Priority
    priority_reason: str
    action_plan: tuple[ActionPlanStep, ...]
    evidence: tuple[EvidenceRef, ...]
    confidence: Confidence
    created_at: datetime
    impact: str = "none"
    incident_key: str | None = None
    related_message_ids: tuple[str, ...] = ()


@dataclass(slots=True)
class DigestRun:
    id: str
    user_id: str
    mailbox_connection_id: str
    trigger: RunTrigger
    status: RunStatus
    query: str
    idempotency_key: str
    max_emails: int
    emails_matched: int = 0
    emails_processed: int = 0
    emails_actionable: int = 0
    action_items_count: int = 0
    ignored_emails_count: int = 0
    filtered_summary: str | None = None
    attachments_found: int = 0
    attachments_extracted: int = 0
    attachment_warnings_count: int = 0
    truncated: bool = False
    next_cursor: str | None = None
    error_code: str | None = None
    error_message_safe: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AttachmentWarning:
    filename: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class DigestCompletedEvent:
    run_id: str
    user_id: str
    status: RunStatus
    occurred_at: datetime
