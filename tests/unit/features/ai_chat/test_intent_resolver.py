import itertools

import pytest

from cowork_agent.domain.chat_contracts import (
    ChatIntent,
    ChatRoute,
    IntentDecision,
    IntentReasonCode,
    RoutingOutcome,
)
from cowork_agent.features.ai_chat.intent.resolver import finalize_route, resolve_route


@pytest.mark.parametrize(
    ("needs_rag", "needs_tool", "needs_clarification", "expected"),
    [
        (False, False, False, ChatRoute.CHAT),
        (False, True, False, ChatRoute.TOOL),
        (True, False, False, ChatRoute.RAG),
        (True, True, False, ChatRoute.RAG_TOOL),
        (False, False, True, ChatRoute.CLARIFY),
        (False, True, True, ChatRoute.CLARIFY),
        (True, False, True, ChatRoute.CLARIFY),
        (True, True, True, ChatRoute.CLARIFY),
    ],
)
def test_resolver_truth_table(
    needs_rag: bool,
    needs_tool: bool,
    needs_clarification: bool,
    expected: ChatRoute,
) -> None:
    assert (
        resolve_route(
            needs_rag=needs_rag,
            needs_tool=needs_tool,
            needs_clarification=needs_clarification,
        )
        is expected
    )


def _decision(*, rag: bool, tool: bool, clarify: bool) -> IntentDecision:
    return IntentDecision(
        intent=ChatIntent.KNOWLEDGE_QUERY,
        needs_rag=rag,
        needs_tool=tool,
        tool_name="email" if tool else None,
        needs_clarification=clarify,
        retrieval_query="query" if rag else None,
        confidence=0.8,
        reason_codes=(IntentReasonCode.USER_DOCUMENT_REQUIRED,),
    )


def test_runtime_tool_axis_is_unreachable_for_all_boolean_combinations() -> None:
    for rag, tool, clarify in itertools.product((False, True), repeat=3):
        outcome = finalize_route(
            _decision(rag=rag, tool=tool, clarify=clarify),
            has_ready_documents=True,
            tool_axis_enabled=False,
            classifier_retried=False,
            fallback_used=False,
            prompt_version="v1",
        )
        assert outcome.route not in {ChatRoute.TOOL, ChatRoute.RAG_TOOL}
        assert outcome.effective_needs_tool is False


def test_no_ready_documents_downgrades_only_rag_and_preserves_clarify() -> None:
    rag = finalize_route(
        _decision(rag=True, tool=False, clarify=False),
        has_ready_documents=False,
        tool_axis_enabled=False,
        classifier_retried=False,
        fallback_used=False,
        prompt_version="v1",
    )
    clarify = finalize_route(
        _decision(rag=True, tool=False, clarify=True),
        has_ready_documents=False,
        tool_axis_enabled=False,
        classifier_retried=False,
        fallback_used=False,
        prompt_version="v1",
    )

    assert rag.route is ChatRoute.CHAT
    assert rag.retrieval_query is None
    assert IntentReasonCode.NO_READY_DOCUMENTS in rag.reason_codes
    assert clarify.route is ChatRoute.CLARIFY


def _tool_decision(name: str, *, rag: bool = False) -> IntentDecision:
    return IntentDecision(
        intent=ChatIntent.ACTION_REQUEST,
        needs_rag=rag,
        needs_tool=True,
        tool_name=name,
        needs_clarification=False,
        retrieval_query="query" if rag else None,
        confidence=0.8,
        reason_codes=(IntentReasonCode.EXTERNAL_ACTION_REQUESTED,),
    )


def _finalize(decision: IntentDecision, *, available: tuple[str, ...]) -> RoutingOutcome:
    return finalize_route(
        decision,
        has_ready_documents=True,
        tool_axis_enabled=True,
        classifier_retried=False,
        fallback_used=False,
        prompt_version="v1",
        available_tools=available,
    )


def test_a_registered_tool_reaches_the_tool_route() -> None:
    outcome = _finalize(
        _tool_decision("create_calendar_event"), available=("create_calendar_event",)
    )

    assert outcome.route is ChatRoute.TOOL
    assert outcome.effective_needs_tool is True
    assert IntentReasonCode.TOOL_NOT_AVAILABLE not in outcome.reason_codes


@pytest.mark.parametrize("name", ["create_calender_event", "send_email", "CREATE_CALENDAR_EVENT"])
def test_an_unregistered_tool_name_falls_back_to_chat_without_fuzzy_matching(name: str) -> None:
    """A near-miss on a tool that writes to a real calendar creates the wrong event."""

    outcome = _finalize(_tool_decision(name), available=("create_calendar_event",))

    assert outcome.route is ChatRoute.CHAT
    assert outcome.effective_needs_tool is False
    assert IntentReasonCode.TOOL_NOT_AVAILABLE in outcome.reason_codes


def test_an_empty_registry_narrows_every_tool_request() -> None:
    outcome = _finalize(_tool_decision("create_calendar_event"), available=())

    assert outcome.route is ChatRoute.CHAT
    assert IntentReasonCode.TOOL_NOT_AVAILABLE in outcome.reason_codes


def test_the_disabled_axis_is_reported_ahead_of_the_unknown_tool() -> None:
    outcome = finalize_route(
        _tool_decision("create_calendar_event"),
        has_ready_documents=True,
        tool_axis_enabled=False,
        classifier_retried=False,
        fallback_used=False,
        prompt_version="v1",
        available_tools=(),
    )

    assert IntentReasonCode.TOOL_REQUESTED_BUT_DISABLED in outcome.reason_codes
    assert IntentReasonCode.TOOL_NOT_AVAILABLE not in outcome.reason_codes


def test_rag_tool_is_downgraded_to_tool_and_drops_the_retrieval_half() -> None:
    """RAG_TOOL has no implementation; creating an event needs no document evidence."""

    outcome = _finalize(
        _tool_decision("create_calendar_event", rag=True), available=("create_calendar_event",)
    )

    assert outcome.route is ChatRoute.TOOL
    assert outcome.effective_needs_rag is False
    assert outcome.retrieval_query is None


def test_a_narrowed_rag_tool_request_still_retrieves() -> None:
    outcome = _finalize(
        _tool_decision("unknown_tool", rag=True), available=("create_calendar_event",)
    )

    assert outcome.route is ChatRoute.RAG
    assert outcome.effective_needs_rag is True
    assert outcome.retrieval_query == "query"
