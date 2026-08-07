"""Tests for the versioned target contracts (tasks/plan.md T1.1)."""

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from cowork_agent.domain.models import Priority
from cowork_agent.domain.target_contracts import (
    TARGET_CONTRACTS_VERSION,
    TRACE_CONTENT_POLICY_DEVELOPMENT,
    TRACE_CONTENT_POLICY_PRODUCTION,
    TRACE_DEVELOPMENT_MARKER,
    Actionability,
    ActionPlanOutput,
    BodyFormat,
    EmailRouteDecision,
    EphemeralEmailEnvelope,
    ExpectedDocumentType,
    FetchStatus,
    PlanStep,
    ReasonCode,
    Route,
    SupportingDocument,
    Task,
    TraceEvent,
    TraceLatency,
    TraceStatus,
    ValidationStatus,
)


def _envelope() -> EphemeralEmailEnvelope:
    return EphemeralEmailEnvelope(
        run_id="run-1",
        tenant_id="tenant-local",
        user_id="user@example.com",
        gmail_message_id="msg-1",
        gmail_thread_id="thread-1",
        gmail_url="https://mail.google.com/mail/u/0#all/msg-1",
        sender_name="Ada Lovelace",
        sender_email="ada@example.com",
        recipients=("user@example.com", "cc@example.com"),
        subject="Quarterly report",
        received_at=datetime(2026, 8, 7, 9, 30, tzinfo=UTC),
        labels=("INBOX", "UNREAD"),
        normalized_body="Please submit the quarterly report by Friday.",
        body_format=BodyFormat.TEXT,
        attachments_present=True,
        fetch_status=FetchStatus.COMPLETE,
    )


def _route_decision() -> EmailRouteDecision:
    return EmailRouteDecision(
        actionability=Actionability.ACTION_REQUIRED,
        route=Route.RETRIEVE_RAG,
        candidate_action_item="Submit the quarterly report",
        email_is_sufficient=False,
        knowledge_gaps=("quarterly report template location",),
        retrieval_query="quarterly report template",
        expected_document_types=(
            ExpectedDocumentType.TEMPLATE,
            ExpectedDocumentType.COMPANY_POLICY,
        ),
        reason_codes=(
            ReasonCode.TEMPLATE_REQUIRED,
            ReasonCode.COMPANY_PROCEDURE_REQUIRED,
        ),
        confidence=0.87,
    )


def _task() -> Task:
    return Task(
        task_id="task-1",
        run_id="run-1",
        gmail_message_id="msg-1",
        gmail_url="https://mail.google.com/mail/u/0#all/msg-1",
        source_message_ids=("msg-1", "msg-2"),
        incident_key="incident-1",
        title="Submit quarterly report",
        request_summary="Ada requests the quarterly report by Friday.",
        actionability=Actionability.ACTION_REQUIRED,
        route=Route.RETRIEVE_RAG,
        priority=Priority.URGENT,
        deadline=datetime(2026, 8, 8, 17, 0, tzinfo=UTC),
        action_plan=(
            PlanStep(
                step=1,
                instruction="Open the quarterly report template.",
                supporting_citation_ids=("cit-1",),
            ),
            PlanStep(
                step=2,
                instruction="Fill in the figures and submit.",
                supporting_citation_ids=(),
            ),
        ),
        supporting_documents=(
            SupportingDocument(
                citation_id="cit-1",
                document_id="doc-1",
                title="Quarterly Report Template",
                section="Usage",
                url="https://hub.example.com/doc-1",
                relevance_score=0.92,
            ),
        ),
        missing_information=("Latest template version number",),
        classifier_confidence=0.87,
        generation_confidence=0.81,
        validation_status=ValidationStatus.SYSTEM_GENERATED,
        created_at=datetime(2026, 8, 7, 9, 35, tzinfo=UTC),
    )


def _action_plan_output() -> ActionPlanOutput:
    return ActionPlanOutput(task=_task())


def _trace_event() -> TraceEvent:
    return TraceEvent(
        run_id="run-1",
        tenant_id="tenant-local",
        user_id="user@example.com",
        gmail_message_id="msg-1",
        event_name="task_generated",
        status=TraceStatus.SUCCESS,
        route=Route.RETRIEVE_RAG,
        reason_codes=("template_required",),
        classifier_confidence=0.87,
        rag_result_count=3,
        retrieval_status="success",
        generation_status="success",
        validation_status="system_generated",
        latency_ms=TraceLatency(
            email=120,
            memory=5,
            classifier=900,
            rag=250,
            generation=1500,
            persistence=12,
        ),
    )


_ROUND_TRIP_CASES = {
    "envelope": _envelope,
    "route_decision": _route_decision,
    "action_plan_output": _action_plan_output,
    "trace_event": _trace_event,
}


@pytest.mark.parametrize("build", list(_ROUND_TRIP_CASES.values()), ids=_ROUND_TRIP_CASES)
def test_round_trip_dict_and_json(build):
    instance = build()
    payload = instance.to_dict()

    # Round-trip through the plain dict.
    assert type(instance).from_dict(payload) == instance

    # The payload must be JSON-safe and survive an actual JSON round-trip.
    text = json.dumps(payload)
    assert type(instance).from_dict(json.loads(text)) == instance


def test_to_dict_is_json_safe():
    payload = _envelope().to_dict()
    assert payload["received_at"] == "2026-08-07T09:30:00+00:00"
    assert payload["body_format"] == "text"
    assert payload["fetch_status"] == "complete"
    assert payload["attachments_processed"] is False

    task_payload = _action_plan_output().to_dict()
    assert task_payload["task"]["priority"] == "urgent"
    assert task_payload["task"]["deadline"] == "2026-08-08T17:00:00+00:00"
    assert task_payload["task"]["validation_status"] == "system_generated"
    assert task_payload["task"]["action_plan"][0]["supporting_citation_ids"] == ["cit-1"]


def test_attachments_processed_defaults_to_false():
    assert _envelope().attachments_processed is False
    assert EphemeralEmailEnvelope.from_dict(_envelope().to_dict()).attachments_processed is False


def test_attachments_processed_rejects_true():
    payload = _envelope().to_dict()
    payload["attachments_processed"] = True
    with pytest.raises(ValueError, match="attachments_processed"):
        EphemeralEmailEnvelope.from_dict(payload)


def test_actionability_values():
    assert {member.value for member in Actionability} == {
        "action_required",
        "action_suggested",
        "informational",
        "unclear",
        "irrelevant",
    }


def test_route_values():
    assert {member.value for member in Route} == {"no_action", "direct_plan", "retrieve_rag"}


def test_reason_code_values():
    assert {member.value for member in ReasonCode} == {
        "no_action",
        "email_self_contained",
        "company_procedure_required",
        "governance_required",
        "policy_required",
        "template_required",
        "internal_term_unresolved",
        "domain_knowledge_required",
    }


def test_expected_document_type_values():
    assert {member.value for member in ExpectedDocumentType} == {
        "company_policy",
        "governance_document",
        "procedure",
        "guideline",
        "template",
        "product_documentation",
    }


def test_supporting_enum_values():
    assert {member.value for member in BodyFormat} == {"text", "html_converted"}
    assert {member.value for member in FetchStatus} == {"complete", "partial"}
    assert {member.value for member in ValidationStatus} == {
        "system_generated",
        "user_approved",
        "completed",
        "rejected",
    }
    assert {member.value for member in TraceStatus} == {"success", "partial", "failed"}


def test_task_priority_supports_urgent():
    assert _task().priority is Priority.URGENT
    restored = ActionPlanOutput.from_dict(_action_plan_output().to_dict()).task
    assert restored.priority is Priority.URGENT


def test_trace_latency_defaults_to_all_none():
    assert TraceLatency() == TraceLatency(
        email=None,
        memory=None,
        classifier=None,
        rag=None,
        generation=None,
        persistence=None,
    )


@pytest.mark.parametrize(
    ("build", "field"),
    [
        (_envelope, "subject"),
        (_route_decision, "confidence"),
        (_action_plan_output, "task"),
        (_trace_event, "event_name"),
    ],
    ids=list(_ROUND_TRIP_CASES),
)
def test_frozen_rejects_mutation(build, field):
    instance = build()
    with pytest.raises(FrozenInstanceError):
        setattr(instance, field, "mutated")


def test_target_contracts_version():
    assert TARGET_CONTRACTS_VERSION == "1.0.0"


def test_trace_content_policy_constants():
    assert TRACE_CONTENT_POLICY_PRODUCTION == "metadata_only"
    assert TRACE_CONTENT_POLICY_DEVELOPMENT == "full_content_allowed"
    assert TRACE_DEVELOPMENT_MARKER == "ALLOW ONLY FOR CURRENT DEVELOPMENT STAGE"
