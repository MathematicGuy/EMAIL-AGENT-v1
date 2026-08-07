"""Task Candidate correlation tests (frozen correlation contract, rule 3).

Covers ``docs/references/task-candidate-correlation-contract.md``: one Task
Candidate per thread of selected emails, every paired Route Decision in
exactly one candidate, ``source_message_ids`` preserved in fetch order,
deterministic output, and pairing violations named by message id.
"""

from datetime import UTC, datetime

import pytest

from cowork_agent.domain.target_contracts import (
    Actionability,
    BodyFormat,
    EmailRouteDecision,
    EphemeralEmailEnvelope,
    FetchStatus,
    Route,
)
from cowork_agent.features.email_action_plan.correlation import correlate_candidates

_RECEIVED_AT = datetime(2026, 8, 7, 9, 0, tzinfo=UTC)


def _envelope(message_id: str, thread_id: str) -> EphemeralEmailEnvelope:
    return EphemeralEmailEnvelope(
        run_id="run-1",
        tenant_id="tenant-1",
        user_id="user-1",
        gmail_message_id=message_id,
        gmail_thread_id=thread_id,
        gmail_url="",
        sender_name="Sender",
        sender_email="sender@example.com",
        recipients=(),
        subject=f"Subject {message_id}",
        received_at=_RECEIVED_AT,
        labels=(),
        normalized_body="Please review this item.",
        body_format=BodyFormat.TEXT,
        attachments_present=False,
        fetch_status=FetchStatus.COMPLETE,
    )


def _decision(confidence: float = 0.9) -> EmailRouteDecision:
    return EmailRouteDecision(
        actionability=Actionability.ACTION_REQUIRED,
        route=Route.DIRECT_PLAN,
        candidate_action_item="Handle the request.",
        email_is_sufficient=True,
        knowledge_gaps=(),
        retrieval_query=None,
        expected_document_types=(),
        reason_codes=(),
        confidence=confidence,
    )


def test_single_thread_forms_one_candidate_in_fetch_order() -> None:
    envelopes = {
        "message-1": _envelope("message-1", "thread-A"),
        "message-2": _envelope("message-2", "thread-A"),
        "message-3": _envelope("message-3", "thread-A"),
    }
    # Built in a different order than the envelopes: envelope (fetch) order
    # governs candidate content, not decision insertion order.
    decisions = {
        "message-3": _decision(0.3),
        "message-1": _decision(0.1),
        "message-2": _decision(0.2),
    }

    candidates = correlate_candidates(decisions, envelopes)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.candidate_key == "thread-A"
    assert candidate.gmail_thread_id == "thread-A"
    assert candidate.incident_key is None
    assert candidate.source_message_ids == ("message-1", "message-2", "message-3")
    assert candidate.decisions == (
        ("message-1", decisions["message-1"]),
        ("message-2", decisions["message-2"]),
        ("message-3", decisions["message-3"]),
    )


def test_multiple_threads_form_one_candidate_per_thread_in_first_seen_order() -> None:
    envelopes = {
        "message-b1": _envelope("message-b1", "thread-B"),
        "message-a1": _envelope("message-a1", "thread-A"),
        "message-b2": _envelope("message-b2", "thread-B"),
        "message-c1": _envelope("message-c1", "thread-C"),
    }
    decisions = {message_id: _decision() for message_id in envelopes}

    candidates = correlate_candidates(decisions, envelopes)

    assert [candidate.gmail_thread_id for candidate in candidates] == [
        "thread-B",
        "thread-A",
        "thread-C",
    ]
    assert [candidate.candidate_key for candidate in candidates] == [
        "thread-B",
        "thread-A",
        "thread-C",
    ]
    assert [candidate.source_message_ids for candidate in candidates] == [
        ("message-b1", "message-b2"),
        ("message-a1",),
        ("message-c1",),
    ]
    assert all(candidate.incident_key is None for candidate in candidates)


def test_empty_input_yields_no_candidates() -> None:
    assert correlate_candidates({}, {}) == ()


def test_decision_without_envelope_raises_naming_the_id() -> None:
    decisions = {"message-1": _decision(), "message-orphan": _decision()}
    envelopes = {"message-1": _envelope("message-1", "thread-A")}

    with pytest.raises(ValueError, match="message-orphan"):
        correlate_candidates(decisions, envelopes)


def test_envelope_without_decision_raises_naming_the_id() -> None:
    decisions = {"message-1": _decision()}
    envelopes = {
        "message-1": _envelope("message-1", "thread-A"),
        "message-stray": _envelope("message-stray", "thread-A"),
    }

    with pytest.raises(ValueError, match="message-stray"):
        correlate_candidates(decisions, envelopes)


def test_correlation_is_deterministic_across_calls() -> None:
    envelopes = {
        "message-b1": _envelope("message-b1", "thread-B"),
        "message-a1": _envelope("message-a1", "thread-A"),
        "message-b2": _envelope("message-b2", "thread-B"),
    }
    decisions = {message_id: _decision() for message_id in envelopes}

    first = correlate_candidates(decisions, envelopes)
    second = correlate_candidates(dict(decisions), dict(envelopes))

    assert first == second


def test_candidates_partition_every_input_message_id_exactly_once() -> None:
    envelopes = {
        "message-a1": _envelope("message-a1", "thread-A"),
        "message-a2": _envelope("message-a2", "thread-A"),
        "message-b1": _envelope("message-b1", "thread-B"),
        "message-c1": _envelope("message-c1", "thread-C"),
        "message-c2": _envelope("message-c2", "thread-C"),
    }
    decisions = {
        message_id: _decision(confidence=0.5 + index / 10)
        for index, message_id in enumerate(envelopes)
    }

    candidates = correlate_candidates(decisions, envelopes)

    grouped_ids = [set(candidate.source_message_ids) for candidate in candidates]
    assert set().union(*grouped_ids) == set(envelopes)
    for index, earlier in enumerate(grouped_ids):
        for later in grouped_ids[index + 1 :]:
            assert earlier.isdisjoint(later)
    assert sum(len(candidate.source_message_ids) for candidate in candidates) == len(envelopes)

    correlated_pairs = [pair for candidate in candidates for pair in candidate.decisions]
    assert len(correlated_pairs) == len(decisions)
    assert dict(correlated_pairs) == decisions
