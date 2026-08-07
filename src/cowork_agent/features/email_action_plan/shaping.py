"""Application-owned deterministic plan policy (master-comparison §3.4).

Shaping is the deterministic half of plan production: it groups fetched
envelopes into batched threads, sanitizes parsed plan steps, correlates
extractions by incident key, and merges them into bounded action plans.
Provider adapters keep only transport and structured parsing.
"""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime

from cowork_agent.domain import ActionPlanStep, DeadlineSource
from cowork_agent.domain.target_contracts import EphemeralEmailEnvelope
from cowork_agent.features.email_action_plan.schemas import (
    EmailExtraction,
    ExtractedAction,
)

_Thread = tuple[EphemeralEmailEnvelope, ...]


def group_by_thread(messages: Sequence[EphemeralEmailEnvelope]) -> tuple[_Thread, ...]:
    """Regroup the flat envelope list into threads, preserving first-seen order."""
    grouped: dict[str, list[EphemeralEmailEnvelope]] = {}
    for message in messages:
        grouped.setdefault(message.gmail_thread_id, []).append(message)
    return tuple(tuple(thread) for thread in grouped.values())


def batch_messages(
    threads: Sequence[_Thread], max_emails: int
) -> tuple[tuple[_Thread, ...], ...]:
    """Group whole threads without exceeding the target email count when possible."""
    if not threads:
        return ((),)

    batches: list[tuple[_Thread, ...]] = []
    current: list[_Thread] = []
    current_email_count = 0
    for thread in threads:
        thread_email_count = len(thread)
        if current and current_email_count + thread_email_count > max_emails:
            batches.append(tuple(current))
            current = []
            current_email_count = 0
        current.append(thread)
        current_email_count += thread_email_count
    if current:
        batches.append(tuple(current))
    return tuple(batches)


def parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_action_plan(value: object) -> tuple[ActionPlanStep, ...]:
    if not isinstance(value, list):
        raise ValueError("actionPlan must be an array")
    blocked_markers = (
        "single parseable json",
        "schema provided in the context",
        "unread email to-do summarizer",
        "relatedmessageids",
        "explicitblocker",
        "classificationreason",
        "<untrusted_data>",
        '"actionplan"',
        '"providermessageid"',
    )
    cleaned: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw_step in value:
        if not isinstance(raw_step, Mapping):
            continue
        instruction = str(raw_step.get("instruction", "")).strip()
        normalized = " ".join(instruction.casefold().split())
        if (
            not instruction
            or len(instruction) > 600
            or any(marker in normalized for marker in blocked_markers)
            or normalized in seen
        ):
            continue
        seen.add(normalized)
        cleaned.append((instruction, str(raw_step.get("basis", "suggestion"))))
        if len(cleaned) == 5:
            break
    return tuple(
        ActionPlanStep(index, instruction, basis)
        for index, (instruction, basis) in enumerate(cleaned, 1)
    )


def merge_correlated_emails(
    emails: Sequence[EmailExtraction],
) -> tuple[EmailExtraction, ...]:
    """Deterministically consolidate actions that the model assigned to one incident."""
    actions_by_email: list[list[ExtractedAction]] = [[] for _ in emails]
    correlated: dict[str, list[tuple[int, ExtractedAction]]] = {}
    for email_index, email_result in enumerate(emails):
        for action in email_result.action_items:
            incident_key = normalize_incident_key(action.incident_key)
            if incident_key:
                correlated.setdefault(incident_key, []).append((email_index, action))
            else:
                actions_by_email[email_index].append(action)

    for group in correlated.values():
        if len(group) == 1:
            email_index, action = group[0]
            actions_by_email[email_index].append(action)
            continue
        primary_index, primary = max(group, key=lambda pair: impact_rank(pair[1].impact))
        merged = merge_actions(primary, [action for _, action in group])
        actions_by_email[primary_index].append(merged)

    return tuple(
        replace(email_result, action_items=tuple(actions_by_email[index]))
        for index, email_result in enumerate(emails)
    )


def merge_actions(primary: ExtractedAction, actions: Sequence[ExtractedAction]) -> ExtractedAction:
    titles = list(dict.fromkeys(action.title.strip() for action in actions if action.title.strip()))
    summaries = list(
        dict.fromkeys(action.summary.strip() for action in actions if action.summary.strip())
    )
    related_ids = list(
        dict.fromkeys(
            message_id
            for action in actions
            for message_id in (action.provider_message_id, *action.related_message_ids)
        )
    )
    deadlines = [action for action in actions if action.deadline_at is not None]
    deadline_action = min(deadlines, key=deadline_sort_key) if deadlines else None
    steps = select_merged_steps(actions, max_steps=5)
    evidence = list(dict.fromkeys(item for action in actions for item in action.evidence))
    confidence = min(
        (action.confidence for action in actions),
        key=lambda value: {"low": 0, "medium": 1, "high": 2}[value.value],
    )
    return replace(
        primary,
        title=" & ".join(titles),
        summary=" ".join(summaries),
        deadline_at=deadline_action.deadline_at if deadline_action else None,
        deadline_text=deadline_action.deadline_text if deadline_action else None,
        deadline_source=(
            deadline_action.deadline_source if deadline_action else DeadlineSource.NONE
        ),
        action_plan=tuple(
            ActionPlanStep(index, instruction, basis)
            for index, (instruction, basis) in enumerate(steps, 1)
        ),
        evidence=tuple(evidence),
        confidence=confidence,
        required=any(action.required for action in actions),
        explicit_blocker=any(action.explicit_blocker for action in actions),
        impact=max((action.impact for action in actions), key=impact_rank),
        related_message_ids=tuple(related_ids),
    )


def impact_rank(impact: str) -> int:
    return {
        "none": 0,
        "production_blocked": 1,
        "service_outage": 2,
        "security_risk": 3,
        "data_loss_risk": 4,
    }.get(impact, 0)


def select_merged_steps(
    actions: Sequence[ExtractedAction], *, max_steps: int
) -> list[tuple[str, str]]:
    """Interleave plans so every correlated event contributes concise remediation steps."""
    plans = [
        [(step.instruction.strip(), step.basis) for step in action.action_plan]
        for action in actions
    ]
    selected: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    step_index = 0
    while len(selected) < max_steps and any(step_index < len(plan) for plan in plans):
        for plan in plans:
            if step_index >= len(plan):
                continue
            step = plan[step_index]
            if step[0] and step not in seen:
                seen.add(step)
                selected.append(step)
                if len(selected) >= max_steps:
                    break
        step_index += 1
    return selected


def normalize_incident_key(value: str | None) -> str:
    parts = [part.strip().casefold() for part in (value or "").split(":") if part.strip()]
    return ":".join(parts[:3])


def deadline_sort_key(action: ExtractedAction) -> datetime:
    if action.deadline_at is None:
        raise ValueError("Cannot sort an action without a deadline")
    return action.deadline_at
