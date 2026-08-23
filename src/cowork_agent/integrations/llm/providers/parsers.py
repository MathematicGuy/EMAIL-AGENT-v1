"""Shared Email Action Plan response parsing and schema-repair helpers."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import uuid4

from cowork_agent.domain import Priority
from cowork_agent.domain.target_contracts import (
    Actionability,
    ActionPlanOutput,
    EmailRouteDecision,
    EmailSourceLink,
    EphemeralEmailEnvelope,
    ExpectedDocumentType,
    PlanStep,
    ReasonCode,
    Route,
    SupportingDocument,
    Task,
    ValidationStatus,
)
from cowork_agent.features.email_action_plan.correlation import TaskCandidate
from cowork_agent.features.email_action_plan.routing import resolve_route
from cowork_agent.features.email_action_plan.schemas import (
    ClassifiedMessage,
    GenerationContext,
)
from cowork_agent.features.email_action_plan.shaping import parse_iso_datetime

from .prompts import FALLBACK_ROUTE_DECISION, GENERATOR_REPAIR_INSTRUCTION

_CLASSIFIER_LOGGER = logging.getLogger(__name__)


def _parse_action_plan_output(
    payload: Mapping[str, Any],
    *,
    run_context: GenerationContext,
    candidate: TaskCandidate,
    first_envelope: EphemeralEmailEnvelope,
    source_links: tuple[EmailSourceLink, ...],
    current_time: datetime,
) -> ActionPlanOutput:
    """Validate one generation payload into the §6.6 ActionPlanOutput.

    Enums validate through the contract types; invalid values raise.
    Identity fields are stamped server-side: the provider's ``taskId`` is
    ignored, ``run_id`` comes from the GenerationContext, correlation fields
    from the Task Candidate, and the Gmail pointers from the first envelope.
    """
    raw_task = payload.get("task")
    if not isinstance(raw_task, Mapping):
        raise ValueError("Generation response must contain a task object")
    actionability = Actionability(_require_str(raw_task["actionability"], "actionability"))
    route = Route(_require_str(raw_task["route"], "route"))
    raw_priority = raw_task["priority"]
    priority = None if raw_priority is None else Priority(_require_str(raw_priority, "priority"))
    validation_status = ValidationStatus(
        _require_str(raw_task["validationStatus"], "validationStatus")
    )
    raw_generation_confidence = raw_task["generationConfidence"]
    return ActionPlanOutput(
        task=Task(
            # Server-side identity: never trust the provider-generated taskId.
            task_id=f"task_{uuid4().hex}",
            run_id=run_context.run_id,
            gmail_message_id=first_envelope.gmail_message_id,
            gmail_url=first_envelope.gmail_url,
            source_message_ids=candidate.source_message_ids,
            incident_key=candidate.incident_key,
            title=_require_str(raw_task["title"], "title"),
            request_summary=_require_str(raw_task["requestSummary"], "requestSummary"),
            actionability=actionability,
            route=route,
            priority=priority,
            deadline=parse_iso_datetime(raw_task.get("deadline")),
            action_plan=_parse_plan_steps(raw_task.get("actionPlan")),
            supporting_documents=_parse_supporting_documents(raw_task.get("supportingDocuments")),
            missing_information=_require_str_tuple(
                raw_task["missingInformation"], "missingInformation"
            ),
            classifier_confidence=_require_confidence(raw_task["classifierConfidence"]),
            generation_confidence=(
                None
                if raw_generation_confidence is None
                else _require_confidence(raw_generation_confidence)
            ),
            validation_status=validation_status,
            created_at=current_time,
            source_links=source_links,
        )
    )


def _task_source_links(
    envelopes: Sequence[EphemeralEmailEnvelope], source_message_ids: Sequence[str]
) -> tuple[EmailSourceLink, ...]:
    """Merge candidate email links by exact URL and assign task-local refs."""

    by_message_id = {envelope.gmail_message_id: envelope for envelope in envelopes}
    ordered: list[tuple[str, str | None]] = []
    positions: dict[str, int] = {}
    for message_id in source_message_ids:
        envelope = by_message_id.get(message_id)
        if envelope is None:
            continue
        for link in envelope.source_links:
            position = positions.get(link.url)
            if position is None:
                positions[link.url] = len(ordered)
                ordered.append((link.url, link.label))
            elif ordered[position][1] is None and link.label:
                ordered[position] = (link.url, link.label)
    return tuple(
        EmailSourceLink(ref=f"link{index}", label=label, url=url)
        for index, (url, label) in enumerate(ordered, start=1)
    )


def _parse_plan_steps(value: object) -> tuple[PlanStep, ...]:
    """Parse actionPlan steps: drop empty instructions, reindex 1..n."""
    steps: list[PlanStep] = []
    for raw_step in _require_sequence(value, "actionPlan"):
        if not isinstance(raw_step, Mapping):
            raise ValueError("Each actionPlan step must be an object")
        instruction = _require_str(raw_step["instruction"], "instruction").strip()
        if not instruction:
            continue
        steps.append(
            PlanStep(
                step=len(steps) + 1,
                instruction=instruction,
                supporting_citation_ids=_require_str_tuple(
                    raw_step["supportingCitationIds"], "supportingCitationIds"
                ),
            )
        )
    return tuple(steps)


def _parse_supporting_documents(value: object) -> tuple[SupportingDocument, ...]:
    documents: list[SupportingDocument] = []
    for raw_document in _require_sequence(value, "supportingDocuments"):
        if not isinstance(raw_document, Mapping):
            raise ValueError("Each supportingDocuments entry must be an object")
        raw_relevance = raw_document["relevanceScore"]
        if isinstance(raw_relevance, bool) or not isinstance(raw_relevance, int | float):
            raise ValueError("relevanceScore must be a number")
        section = raw_document["section"]
        documents.append(
            SupportingDocument(
                citation_id=_require_str(raw_document["citationId"], "citationId"),
                document_id=_require_str(raw_document["documentId"], "documentId"),
                title=_require_str(raw_document["title"], "title"),
                section=None if section is None else _require_str(section, "section"),
                url=_require_str(raw_document["url"], "url"),
                relevance_score=float(raw_relevance),
            )
        )
    return tuple(documents)


async def _generate_with_schema_repair(
    complete: Callable[[str], Awaitable[Mapping[str, Any]]],
    prompt: str,
    parse: Callable[[Mapping[str, Any]], ActionPlanOutput],
) -> ActionPlanOutput:
    """§12.4 ladder shared by both providers: parse, one repair retry, raise.

    A still-invalid payload re-raises the parse error; each adapter wraps it
    into its provider-specific user-safe error.
    """
    try:
        return parse(await complete(prompt))
    except (KeyError, TypeError, ValueError):
        pass
    return parse(await complete(prompt + GENERATOR_REPAIR_INSTRUCTION))


def _validated_decisions(
    payload: Mapping[str, Any] | None,
    expected_ids: frozenset[str],
) -> dict[str, EmailRouteDecision]:
    """Validate one classifier response; unusable payloads yield no decisions."""
    if payload is None:
        return {}
    try:
        return _parse_classification_payload(payload, expected_ids)
    except ValueError as exc:
        _CLASSIFIER_LOGGER.warning("Classifier response rejected: %s", exc)
        return {}


def _parse_classification_payload(
    payload: Mapping[str, Any],
    expected_ids: frozenset[str],
) -> dict[str, EmailRouteDecision]:
    """Validate one batch response into per-message Route Decisions.

    Keeps exactly one valid decision per expected ``providerMessageId``;
    malformed, duplicate, or unknown entries are dropped so the caller can
    repair the batch (PRD-v1 §12.2) or emit the per-message fallback. Raises
    ``ValueError`` when the response envelope itself is unusable.
    """
    raw_emails = payload.get("emails")
    if not isinstance(raw_emails, list):
        raise ValueError("Classification response must contain an emails array")
    decisions: dict[str, EmailRouteDecision] = {}
    duplicated: set[str] = set()
    for raw_email in raw_emails:
        if not isinstance(raw_email, Mapping):
            continue
        raw_id = raw_email.get("providerMessageId")
        if not isinstance(raw_id, str) or raw_id in duplicated or raw_id not in expected_ids:
            continue
        if raw_id in decisions:
            decisions.pop(raw_id)
            duplicated.add(raw_id)
            continue
        try:
            decisions[raw_id] = _parse_route_decision(raw_email)
        except (KeyError, TypeError, ValueError):
            continue
    return decisions


def _parse_route_decision(raw_email: Mapping[str, Any]) -> EmailRouteDecision:
    """Build one schema-validated Route Decision from a raw classifier entry."""
    actionability = Actionability(_require_str(raw_email["actionability"], "actionability"))
    document_types = tuple(
        ExpectedDocumentType(_require_str(item, "expectedDocumentTypes"))
        for item in _require_sequence(raw_email["expectedDocumentTypes"], "expectedDocumentTypes")
    )
    reason_codes = tuple(
        ReasonCode(_require_str(item, "reasonCodes"))
        for item in _require_sequence(raw_email["reasonCodes"], "reasonCodes")
    )
    provisional = EmailRouteDecision(
        actionability=actionability,
        route=Route.RETRIEVE_RAG,
        candidate_action_item=_require_optional_str(
            raw_email["candidateActionItem"], "candidateActionItem"
        ),
        email_is_sufficient=_require_bool(raw_email["emailIsSufficient"], "emailIsSufficient"),
        knowledge_gaps=_require_str_tuple(raw_email["knowledgeGaps"], "knowledgeGaps"),
        retrieval_query=_require_optional_str(raw_email["retrievalQuery"], "retrievalQuery"),
        expected_document_types=document_types,
        reason_codes=reason_codes,
        confidence=_require_confidence(raw_email["confidence"]),
    )
    # The Classifier never owns the route (FR-05): record the deterministic
    # Route Resolver verdict so the stored decision stays self-consistent.
    return replace(provisional, route=resolve_route(provisional).route)


def _classified_messages_for(
    batch_ids: Sequence[str],
    decisions: Mapping[str, EmailRouteDecision],
) -> tuple[ClassifiedMessage, ...]:
    """Bind one decision per batch message; still-missing ids get the §12.2 fallback."""
    return tuple(
        ClassifiedMessage(
            message_id,
            decisions.get(message_id, FALLBACK_ROUTE_DECISION),
            is_fallback=message_id not in decisions,
        )
        for message_id in batch_ids
    )


def _require_str(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _require_optional_str(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _require_str(value, field)


def _require_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _require_sequence(value: object, field: str) -> Sequence[object]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be an array")
    return value


def _require_str_tuple(value: object, field: str) -> tuple[str, ...]:
    return tuple(_require_str(item, field) for item in _require_sequence(value, field))


def _require_confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("confidence must be a number")
    confidence = float(value)
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be between 0 and 1")
    return confidence
