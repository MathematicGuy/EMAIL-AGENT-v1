"""Data contracts crossing application ports."""

from dataclasses import dataclass
from datetime import datetime

from cowork_agent.domain import (
    ActionPlanStep,
    Confidence,
    DeadlineSource,
    EvidenceRef,
)
from cowork_agent.domain.target_contracts import EmailRouteDecision


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


@dataclass(frozen=True, slots=True)
class GenerationContext:
    """Run identity handed to the Generator for one Task Candidate (FR-09).

    The Generator stamps the produced Task with these values server-side;
    user identity never comes from the model output.
    """

    run_id: str
    user_id: str


@dataclass(frozen=True, slots=True)
class ClassifiedMessage:
    """One selected email bound to its schema-validated Route Decision (§6.2)."""

    gmail_message_id: str
    decision: EmailRouteDecision
    is_fallback: bool = False


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    """Outcome of one bounded classification stage (PRD-v1 FR-05).

    ``decisions`` holds exactly one Route Decision per selected email; the
    §12.2 conservative fallback counts as a decision. ``batch_count`` is the
    number of bounded classifier batch calls the stage performed.
    """

    decisions: tuple[ClassifiedMessage, ...]
    batch_count: int
    filtered_summary: str | None = None
