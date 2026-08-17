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
    assert TARGET_CONTRACTS_VERSION == "1.1.0"


def test_trace_content_policy_constants():
    assert TRACE_CONTENT_POLICY_PRODUCTION == "metadata_only"
    assert TRACE_CONTENT_POLICY_DEVELOPMENT == "full_content_allowed"
    assert TRACE_DEVELOPMENT_MARKER == "ALLOW ONLY FOR CURRENT DEVELOPMENT STAGE"


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


@pytest.mark.parametrize(
    "build", [_retrieval_request, _retrieval_response], ids=["request", "response"]
)
def test_retrieval_contract_round_trip(build):
    instance = build()
    payload = instance.to_dict()
    assert type(instance).from_dict(payload) == instance
    text = json.dumps(payload)
    assert type(instance).from_dict(json.loads(text)) == instance


def test_retrieval_status_values():
    assert {member.value for member in RetrievalStatus} == {
        "success",
        "no_results",
        "timeout",
        "authorization_denied",
        "partial",
    }


def test_retrieval_response_chunk_page_fields_default_none():
    chunk = _retrieval_response().chunks[0]
    assert chunk.page_start is None
    assert chunk.page_end is None
    restored = SemanticRetrievalResponse.from_dict(_retrieval_response().to_dict())
    assert restored == _retrieval_response()
    assert restored.chunks[0].page_start is None
    assert restored.chunks[0].page_end is None


def test_semantic_chunk_from_dict_omits_page_keys():
    payload = _retrieval_response().to_dict()["chunks"][0]
    assert isinstance(payload, dict)
    payload.pop("page_start", None)
    payload.pop("page_end", None)
    chunk = SemanticChunk.from_dict(payload)
    assert chunk.page_start is None
    assert chunk.page_end is None


def test_semantic_chunk_from_dict_round_trips_page_coordinates():
    chunk = SemanticChunk(
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
    )
    restored = SemanticChunk.from_dict(chunk.to_dict())
    assert restored == chunk
    assert restored.page_start == 1
    assert restored.page_end == 2


def test_retrieval_filters_defaults_document_ids_years_months_empty():
    filters = RetrievalFilters()
    assert filters.document_status == ("ready",)
    assert filters.document_ids == ()
    assert filters.years == ()
    assert filters.months == ()


def test_retrieval_filters_from_dict_without_new_keys_still_works():
    filters = RetrievalFilters.from_dict({"document_status": ["ready"]})
    assert filters.document_status == ("ready",)
    assert filters.document_ids == ()
    assert filters.years == ()
    assert filters.months == ()


def test_retrieval_filters_from_dict_ignores_unknown_extra_keys():
    filters = RetrievalFilters.from_dict(
        {
            "document_status": ["ready"],
            "category": "policy",
            "unexpected": True,
        }
    )
    assert filters.document_status == ("ready",)
    assert filters.document_ids == ()
    assert filters.years == ()
    assert filters.months == ()


def test_retrieval_filters_round_trip_with_document_ids_years_months():
    filters = RetrievalFilters(
        document_status=("ready",),
        document_ids=("doc-1", "doc-2"),
        years=(2025, 2026),
        months=(1, 8, 12),
    )
    payload = filters.to_dict()
    assert payload["document_ids"] == ["doc-1", "doc-2"]
    assert payload["years"] == [2025, 2026]
    assert payload["months"] == [1, 8, 12]
    restored = RetrievalFilters.from_dict(payload)
    assert restored == filters
    text = json.dumps(payload)
    assert RetrievalFilters.from_dict(json.loads(text)) == filters


def test_semantic_chunk_document_date_defaults_none():
    chunk = _retrieval_response().chunks[0]
    assert chunk.document_date is None
    payload = chunk.to_dict()
    payload.pop("document_date", None)
    restored = SemanticChunk.from_dict(payload)
    assert restored.document_date is None


def test_semantic_chunk_round_trip_with_document_date():
    chunk = SemanticChunk(
        chunk_id="doc#0",
        document_id="doc",
        document_title="Quarterly Report Template",
        section="Usage",
        text="Use the shared template.",
        source_url="data/extracted/doc.md",
        document_version=None,
        relevance_score=0.91,
        rerank_score=None,
        document_date=date(2026, 8, 7),
    )
    payload = chunk.to_dict()
    assert payload["document_date"] == "2026-08-07"
    text = json.dumps(payload)
    restored = SemanticChunk.from_dict(json.loads(text))
    assert restored == chunk
    assert restored.document_date == date(2026, 8, 7)
