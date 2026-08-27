"""Tests for the versioned target contracts (tasks/plan.md T1.1)."""

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime

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
    EmailSourceLink,
    EphemeralEmailEnvelope,
    ExpectedDocumentType,
    FetchStatus,
    PlanStep,
    ReasonCode,
    RetrievalFilters,
    RetrievalLimits,
    RetrievalStatus,
    Route,
    SemanticChunk,
    SemanticRetrievalRequest,
    SemanticRetrievalResponse,
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
        source_links=(
            EmailSourceLink("link1", "Open report", "https://portal.example.com/report"),
        ),
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
        source_links=(EmailSourceLink("link1", None, "https://portal.example.com/report"),),
    )


def _action_plan_output() -> ActionPlanOutput:
    return ActionPlanOutput(task=_task())


def _trace_event() -> TraceEvent:
    return TraceEvent(
        run_id="run-1",
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


def test_round_trip_dict_and_json():
    for name, build in _ROUND_TRIP_CASES.items():
        instance = build()
        payload = instance.to_dict()
        assert type(instance).from_dict(payload) == instance, f"Failed for {name}"
        text = json.dumps(payload)
        assert type(instance).from_dict(json.loads(text)) == instance, f"Failed JSON for {name}"


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


def test_attachments_processed_and_source_links_defaults():
    assert _envelope().attachments_processed is False
    assert EphemeralEmailEnvelope.from_dict(_envelope().to_dict()).attachments_processed is False

    payload = _envelope().to_dict()
    payload["attachments_processed"] = True
    with pytest.raises(ValueError, match="attachments_processed"):
        EphemeralEmailEnvelope.from_dict(payload)

    envelope_payload = _envelope().to_dict()
    envelope_payload.pop("source_links")
    assert EphemeralEmailEnvelope.from_dict(envelope_payload).source_links == ()

    task_payload = _task().to_dict()
    task_payload.pop("source_links")
    assert Task.from_dict(task_payload).source_links == ()


def test_domain_enum_values_and_constants():
    assert {member.value for member in Actionability} == {
        "action_required",
        "action_suggested",
        "informational",
        "unclear",
        "irrelevant",
    }
    assert {member.value for member in Route} == {"no_action", "direct_plan", "retrieve_rag"}
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
    assert {member.value for member in ExpectedDocumentType} == {
        "company_policy",
        "governance_document",
        "procedure",
        "guideline",
        "template",
        "product_documentation",
    }
    assert {member.value for member in BodyFormat} == {"text", "html_converted"}
    assert {member.value for member in FetchStatus} == {"complete", "partial"}
    assert {member.value for member in ValidationStatus} == {
        "system_generated",
        "user_approved",
        "completed",
        "rejected",
    }
    assert {member.value for member in TraceStatus} == {"success", "partial", "failed"}
    assert TARGET_CONTRACTS_VERSION == "1.3.0"
    assert TRACE_CONTENT_POLICY_PRODUCTION == "metadata_only"
    assert TRACE_CONTENT_POLICY_DEVELOPMENT == "full_content_allowed"
    assert TRACE_DEVELOPMENT_MARKER == "ALLOW ONLY FOR CURRENT DEVELOPMENT STAGE"


def test_task_priority_and_trace_latency():
    assert _task().priority is Priority.URGENT
    restored = ActionPlanOutput.from_dict(_action_plan_output().to_dict()).task
    assert restored.priority is Priority.URGENT
    assert TraceLatency() == TraceLatency(
        email=None,
        memory=None,
        classifier=None,
        rag=None,
        generation=None,
        persistence=None,
    )


def test_frozen_rejects_mutation():
    cases = [
        (_envelope, "subject"),
        (_route_decision, "confidence"),
        (_action_plan_output, "task"),
        (_trace_event, "event_name"),
    ]
    for build, field in cases:
        instance = build()
        with pytest.raises(FrozenInstanceError):
            setattr(instance, field, "mutated")


def _retrieval_request() -> SemanticRetrievalRequest:
    return SemanticRetrievalRequest(
        run_id="run-1",
        user_id="user@example.com",
        query="quarterly report template",
        knowledge_gaps=("template location",),
        filters=RetrievalFilters(document_status=("ready",)),
        limits=RetrievalLimits(top_k=5, min_score=0.2, timeout_ms=1500),
    )


def _retrieval_response() -> SemanticRetrievalResponse:
    return SemanticRetrievalResponse(
        query_id="q-1",
        chunks=(
            SemanticChunk(
                chunk_id="doc#0",
                document_id="doc",
                document_title="Quarterly Report Template",
                section="Usage",
                text="Use the shared template.",
                source_url="data/extracted/doc.md",
                document_version=None,
                relevance_score=0.91,
                rerank_score=None,
            ),
        ),
        retrieval_status=RetrievalStatus.SUCCESS,
        latency_ms=42,
    )


def test_retrieval_contract_round_trip():
    for instance in (_retrieval_request(), _retrieval_response()):
        payload = instance.to_dict()
        assert type(instance).from_dict(payload) == instance
        text = json.dumps(payload)
        assert type(instance).from_dict(json.loads(text)) == instance

    assert {member.value for member in RetrievalStatus} == {
        "success",
        "no_results",
        "timeout",
        "authorization_denied",
        "partial",
        "unavailable",
    }


def test_semantic_chunk_coordinates_and_document_date():
    chunk = _retrieval_response().chunks[0]
    assert chunk.page_start is None
    assert chunk.page_end is None
    assert chunk.document_date is None

    payload = _retrieval_response().to_dict()["chunks"][0]
    payload.pop("page_start", None)
    payload.pop("page_end", None)
    payload.pop("document_date", None)
    restored_default = SemanticChunk.from_dict(payload)
    assert restored_default.page_start is None
    assert restored_default.page_end is None
    assert restored_default.document_date is None

    chunk_with_coords = SemanticChunk(
        chunk_id="doc#0",
        document_id="doc",
        document_title="Quarterly Report Template",
        section="Usage",
        text="Use the shared template.",
        source_url="data/extracted/doc.md",
        document_version=None,
        relevance_score=0.91,
        rerank_score=None,
        page_start=1,
        page_end=2,
        document_date=date(2026, 8, 7),
    )
    payload_coords = chunk_with_coords.to_dict()
    assert payload_coords["page_start"] == 1
    assert payload_coords["page_end"] == 2
    assert payload_coords["document_date"] == "2026-08-07"
    restored_coords = SemanticChunk.from_dict(payload_coords)
    assert restored_coords == chunk_with_coords
    assert restored_coords.document_date == date(2026, 8, 7)


def test_retrieval_filters_options_and_round_trip():
    filters = RetrievalFilters()
    assert filters.document_status == ("ready",)
    assert filters.document_ids == ()
    assert filters.years == ()
    assert filters.months == ()

    from_dict_sparse = RetrievalFilters.from_dict({"document_status": ["ready"]})
    assert from_dict_sparse == filters

    from_dict_extra = RetrievalFilters.from_dict(
        {"document_status": ["ready"], "category": "policy", "unexpected": True}
    )
    assert from_dict_extra == filters

    filters_rich = RetrievalFilters(
        document_status=("ready",),
        document_ids=("doc-1", "doc-2"),
        years=(2025, 2026),
        months=(1, 8, 12),
    )
    payload = filters_rich.to_dict()
    assert payload["document_ids"] == ["doc-1", "doc-2"]
    assert payload["years"] == [2025, 2026]
    assert payload["months"] == [1, 8, 12]
    restored = RetrievalFilters.from_dict(payload)
    assert restored == filters_rich
    text = json.dumps(payload)
    assert RetrievalFilters.from_dict(json.loads(text)) == filters_rich
