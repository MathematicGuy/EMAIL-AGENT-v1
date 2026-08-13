"""Versioned compatibility mapper: persisted Tasks → legacy result shape.

T4.2 (master-comparison §7 Compatibility contract): the legacy run result
(``actionItems``, ``nextActions``, warnings, empty-state message) is derived
from durable §6.6 Task rows instead of process-local Action Items. The
mapping logic is relocated here from the worker — there is no second copy.

The mapper is stateless and read-time: Action Item IDs are derived from the
fingerprint and derived priority fallbacks recomputed per read. Versioning
lives in :data:`COMPAT_MAPPER_VERSION`; bump it whenever the derived legacy
shape changes semantics.
"""

from collections.abc import Sequence
from datetime import datetime

from cowork_agent.domain import (
    ActionItem,
    ActionPlanStep,
    AttachmentWarning,
    Confidence,
    DeadlineSource,
    DigestRun,
    EvidenceRef,
    ProcessedEmail,
)

from .policies import calculate_priority
from .ports import PersistedTask

COMPAT_MAPPER_VERSION = "1"

_EMPTY_STATE_MESSAGE = "Không có công việc cần xử lý"


def legacy_result_shape(
    *,
    run: DigestRun,
    persisted: Sequence[PersistedTask],
    warnings: Sequence[AttachmentWarning],
    processed_emails: Sequence[ProcessedEmail],
    clock: datetime,
) -> dict[str, object]:
    """Rebuild the frozen legacy run-result shape from persisted Tasks."""
    items = sorted(
        (action_item_from_task(record, run_id=run.id, clock=clock) for record in persisted),
        key=lambda item: action_item_sort_key(item, clock),
    )
    return {
        "run": run,
        "actionItems": items,
        "nextActions": items[:3],
        "attachmentWarnings": list(warnings),
        "processedEmails": list(processed_emails),
        "message": _EMPTY_STATE_MESSAGE if not items else None,
    }


def action_item_from_task(record: PersistedTask, *, run_id: str, clock: datetime) -> ActionItem:
    """Map one persisted §6.6 Task onto the legacy ActionItem surface.

    The Task owns content and priority; the stored pointer supplies the
    Gmail pointers; ``freshness`` was frozen at persistence time.
    """
    task = record.task
    pointer = record.pointer
    if task.priority is not None:
        priority, priority_reason = task.priority, "generated"
    else:
        priority, priority_reason = calculate_priority(task.deadline, clock)
    generation_confidence = task.generation_confidence
    if generation_confidence is None:
        confidence = Confidence.MEDIUM
    elif generation_confidence >= 0.8:
        confidence = Confidence.HIGH
    elif generation_confidence >= 0.5:
        confidence = Confidence.MEDIUM
    else:
        confidence = Confidence.LOW
    return ActionItem(
        # Deterministic per fingerprint so repeated reads of the same result
        # return stable ids (in-run dedupe guarantees uniqueness per run).
        id=f"act_{record.fingerprint}",
        run_id=run_id,
        mailbox_connection_id=pointer.mailbox_connection_id,
        provider_message_id=task.gmail_message_id,
        provider_thread_id=pointer.provider_thread_id,
        fingerprint=record.fingerprint,
        freshness=record.freshness,
        title=task.title.strip(),
        summary=task.request_summary.strip(),
        sender_name=pointer.sender_name or None,
        sender_address=pointer.sender_address,
        email_subject=pointer.email_subject,
        email_received_at=pointer.email_received_at,
        email_deep_link=task.gmail_url,
        deadline_at=task.deadline,
        deadline_source=DeadlineSource.EXPLICIT if task.deadline else DeadlineSource.NONE,
        deadline_text=None,
        priority=priority,
        priority_reason=priority_reason,
        action_plan=tuple(
            ActionPlanStep(
                step.step,
                step.instruction,
                "suggestion" if step.supporting_citation_ids else "inference",
            )
            for step in task.action_plan
        ),
        evidence=tuple(
            EvidenceRef(
                source_kind="rag",
                filename=document.title,
                location=document.section,
                excerpt="",
                source_message_id=None,
            )
            for document in task.supporting_documents
        ),
        confidence=confidence,
        created_at=task.created_at,
        impact="none",
        incident_key=task.incident_key,
        related_message_ids=task.source_message_ids[1:],
    )


def action_item_sort_key(item: ActionItem, clock: datetime) -> tuple[int, bool, datetime]:
    priority_order = {
        "urgent": 0,
        "high": 1,
        "medium": 2,
        "low": 3,
    }
    return (
        priority_order[item.priority.value],
        item.deadline_at is None,
        item.deadline_at or clock,
    )
