"""Data contracts crossing application ports."""

from dataclasses import dataclass
from datetime import datetime

from cowork_agent.domain import (
    ActionPlanStep,
    Confidence,
    DeadlineSource,
    EmailEnvelope,
    EvidenceRef,
    ExtractedAttachment,
)


@dataclass(frozen=True, slots=True)
class MessageRef:
    message_id: str
    thread_id: str


@dataclass(frozen=True, slots=True)
class SearchPage:
    messages: tuple[MessageRef, ...]
    next_cursor: str | None = None
    estimated_total: int | None = None


@dataclass(frozen=True, slots=True)
class ExtractionLimits:
    max_bytes: int = 20 * 1024 * 1024
    max_units: int = 100
    max_characters: int = 200_000
    timeout_seconds: int = 60


@dataclass(frozen=True, slots=True)
class ThreadContext:
    messages: tuple[EmailEnvelope, ...]
    attachments: tuple[ExtractedAttachment, ...]


@dataclass(frozen=True, slots=True)
class ExtractedAction:
    provider_message_id: str
    title: str
    summary: str
    deadline_at: datetime | None
    deadline_text: str | None
    deadline_source: DeadlineSource
    action_plan: tuple[ActionPlanStep, ...]
    evidence: tuple[EvidenceRef, ...]
    confidence: Confidence
    required: bool = True
    explicit_blocker: bool = False
    impact: str = "none"
    incident_key: str | None = None
    related_message_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EmailExtraction:
    provider_message_id: str
    classification: str
    classification_reason: str
    action_items: tuple[ExtractedAction, ...]


@dataclass(frozen=True, slots=True)
class ExtractionBatch:
    emails: tuple[EmailExtraction, ...]
