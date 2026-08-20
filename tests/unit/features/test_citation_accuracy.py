"""Citation-accuracy inspection tests."""

from datetime import UTC, datetime

import pytest

from cowork_agent.domain.target_contracts import (
    Actionability,
    PlanStep,
    RetrievalStatus,
    Route,
    SemanticChunk,
    SemanticRetrievalResponse,
    Task,
    ValidationStatus,
)
from cowork_agent.features.email_action_plan.citation_accuracy import inspect_citation_accuracy

pytestmark = pytest.mark.extended

_NOW = datetime(2026, 8, 10, 9, tzinfo=UTC)


def _chunk(chunk_id: str, text: str) -> SemanticChunk:
    return SemanticChunk(
        chunk_id=chunk_id,
        document_id=f"doc_{chunk_id}",
        document_title="Company handbook",
        section=None,
        text=text,
        source_url="https://docs.example.com",
        document_version=None,
        relevance_score=0.9,
        rerank_score=None,
    )


def _retrieval(*chunks: SemanticChunk) -> SemanticRetrievalResponse:
    return SemanticRetrievalResponse(
        query_id="query_1",
        chunks=chunks,
        retrieval_status=RetrievalStatus.SUCCESS,
        latency_ms=12,
    )


def _step(step_num: int, instruction: str, *citation_ids: str) -> PlanStep:
    return PlanStep(
        step=step_num,
        instruction=instruction,
        supporting_citation_ids=citation_ids,
    )


def _task(*steps: PlanStep) -> Task:
    return Task(
        task_id="task_1",
        run_id="run_1",
        gmail_message_id="m1",
        gmail_url="https://mail.google.com/mail/u/0/#inbox/m1",
        source_message_ids=("m1",),
        incident_key=None,
        title="Send weekly report",
        request_summary="Send weekly report before Friday.",
        actionability=Actionability.ACTION_REQUIRED,
        route=Route.RETRIEVE_RAG,
        priority=None,
        deadline=None,
        action_plan=steps,
        supporting_documents=(),
        missing_information=(),
        classifier_confidence=0.9,
        generation_confidence=0.9,
        validation_status=ValidationStatus.SYSTEM_GENERATED,
        created_at=_NOW,
    )


def test_no_citations_returns_empty_report() -> None:
    report = inspect_citation_accuracy(_task(_step(1, "Review the report")), _retrieval())

    assert report.task_id == "task_1"
    assert report.total_citations == 0
    assert report.found_count == 0
    assert report.missing_count == 0
    assert report.mean_overlap == 0.0
    assert report.overlaps == ()


def test_perfect_overlap_when_instruction_text_equals_chunk_text() -> None:
    report = inspect_citation_accuracy(
        _task(_step(1, "Submit report today", "cit_1")),
        _retrieval(_chunk("cit_1", "Submit report today")),
    )

    assert report.found_count == 1
    assert report.mean_overlap == 1.0
    assert report.overlaps[0].overlap_score == 1.0


def test_partial_overlap_on_shared_words() -> None:
    report = inspect_citation_accuracy(
        _task(_step(1, "Submit report today", "cit_1")),
        _retrieval(_chunk("cit_1", "Report today to manager")),
    )

    assert report.overlaps[0].overlap_score == 0.4


def test_zero_overlap_when_no_shared_words() -> None:
    report = inspect_citation_accuracy(
        _task(_step(1, "Submit report", "cit_1")),
        _retrieval(_chunk("cit_1", "Schedule meeting")),
    )

    assert report.overlaps[0].overlap_score == 0.0
    assert report.mean_overlap == 0.0


def test_missing_chunk_when_citation_id_not_in_retrieval() -> None:
    report = inspect_citation_accuracy(
        _task(_step(1, "Submit report", "cit_missing")),
        _retrieval(_chunk("cit_1", "Submit report")),
    )

    assert report.total_citations == 1
    assert report.found_count == 0
    assert report.missing_count == 1
    assert report.overlaps[0].chunk_found is False
    assert report.overlaps[0].chunk_preview == ""


def test_none_retrieval_marks_all_citations_missing() -> None:
    report = inspect_citation_accuracy(
        _task(_step(1, "Submit report", "cit_1", "cit_2")),
        None,
    )

    assert report.total_citations == 2
    assert report.found_count == 0
    assert report.missing_count == 2
    assert all(not overlap.chunk_found for overlap in report.overlaps)


def test_mean_overlap_excludes_missing_chunks() -> None:
    report = inspect_citation_accuracy(
        _task(_step(1, "Submit report", "cit_1", "cit_missing")),
        _retrieval(_chunk("cit_1", "Submit report")),
    )

    assert report.found_count == 1
    assert report.missing_count == 1
    assert report.mean_overlap == 1.0


def test_multiple_citations_per_step() -> None:
    report = inspect_citation_accuracy(
        _task(_step(3, "Submit report today", "cit_1", "cit_2")),
        _retrieval(
            _chunk("cit_1", "Submit report today"),
            _chunk("cit_2", "Submit report"),
        ),
    )

    assert report.total_citations == 2
    assert [overlap.step for overlap in report.overlaps] == [3, 3]
    assert [overlap.citation_id for overlap in report.overlaps] == ["cit_1", "cit_2"]
    assert report.mean_overlap == 0.8334


def test_preview_truncates_at_80_chars() -> None:
    text = "a" * 81
    report = inspect_citation_accuracy(
        _task(_step(1, text, "cit_1")),
        _retrieval(_chunk("cit_1", text)),
    )

    assert report.overlaps[0].instruction_preview == "a" * 80
    assert report.overlaps[0].chunk_preview == "a" * 80


def test_instruction_preview_strips_newlines() -> None:
    report = inspect_citation_accuracy(
        _task(_step(1, "Submit\nreport", "cit_1")),
        _retrieval(_chunk("cit_1", "Submit report")),
    )

    assert report.overlaps[0].instruction_preview == "Submit report"
