import pytest

from cowork_agent.domain.chat_contracts import (
    MAX_RETRIEVAL_QUERY_LENGTH,
    ChatIntent,
    ChatRoute,
    IntentDecision,
    IntentReasonCode,
    RoutingOutcome,
    classifier_decision_from_dict,
)


def _payload() -> dict[str, object]:
    return {
        "intent": "knowledge_query",
        "needs_rag": True,
        "needs_tool": False,
        "tool_name": None,
        "needs_clarification": False,
        "retrieval_query": "termination conditions",
        "confidence": 0.9,
        "reason_codes": ["user_document_required"],
    }


def test_classifier_decision_round_trips_strict_structured_output() -> None:
    decision = classifier_decision_from_dict(_payload())

    assert decision.intent is ChatIntent.KNOWLEDGE_QUERY
    assert decision.to_dict() == _payload()


def test_classifier_decision_rejects_extra_fields_and_system_reason_codes() -> None:
    extra = _payload() | {"route": "rag"}
    with pytest.raises(ValueError):
        classifier_decision_from_dict(extra)

    system_reason = _payload() | {"reason_codes": ["classifier_unavailable"]}
    with pytest.raises(ValueError):
        classifier_decision_from_dict(system_reason)


def test_intent_decision_enforces_query_tool_and_confidence_invariants() -> None:
    with pytest.raises(ValueError):
        IntentDecision(
            ChatIntent.KNOWLEDGE_QUERY,
            True,
            False,
            None,
            False,
            None,
            0.5,
            (IntentReasonCode.USER_DOCUMENT_REQUIRED,),
        )
    with pytest.raises(ValueError):
        IntentDecision(
            ChatIntent.ACTION_REQUEST,
            False,
            False,
            "email",
            False,
            None,
            0.5,
            (IntentReasonCode.EXTERNAL_ACTION_REQUESTED,),
        )
    with pytest.raises(ValueError):
        IntentDecision(
            ChatIntent.KNOWLEDGE_QUERY,
            True,
            False,
            None,
            False,
            "x" * (MAX_RETRIEVAL_QUERY_LENGTH + 1),
            1.1,
            (IntentReasonCode.USER_DOCUMENT_REQUIRED,),
        )


def test_routing_outcome_carries_original_and_effective_decisions() -> None:
    decision = classifier_decision_from_dict(_payload())
    outcome = RoutingOutcome(
        decision=decision,
        route=ChatRoute.RAG,
        effective_needs_rag=True,
        effective_needs_tool=False,
        effective_needs_clarification=False,
        retrieval_query="termination conditions",
        reason_codes=decision.reason_codes,
        classifier_retried=False,
        fallback_used=False,
        prompt_version="chat-intent-v1",
    )

    assert outcome.decision is decision
    assert outcome.route is ChatRoute.RAG
