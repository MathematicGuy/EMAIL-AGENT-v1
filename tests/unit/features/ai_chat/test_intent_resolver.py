import itertools

import pytest

from cowork_agent.domain.chat_contracts import (
    ChatIntent,
    ChatRoute,
    IntentDecision,
    IntentReasonCode,
)
from cowork_agent.features.ai_chat.intent.resolver import finalize_route, resolve_route

pytestmark = pytest.mark.extended


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
