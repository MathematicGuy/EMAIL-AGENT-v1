import itertools

from cowork_agent.domain.chat_contracts import (
    ChatIntent,
    ChatRoute,
    IntentDecision,
    IntentReasonCode,
)
from cowork_agent.features.ai_chat.intent.resolver import finalize_route, resolve_route


def test_resolver_truth_table_and_tool_axis_disabled() -> None:
    # 8 combinations of truth table
    cases = [
        (False, False, False, ChatRoute.CHAT),
        (False, True, False, ChatRoute.TOOL),
        (True, False, False, ChatRoute.RAG),
        (True, True, False, ChatRoute.RAG_TOOL),
        (False, False, True, ChatRoute.CLARIFY),
        (False, True, True, ChatRoute.CLARIFY),
        (True, False, True, ChatRoute.CLARIFY),
        (True, True, True, ChatRoute.CLARIFY),
    ]
    for rag, tool, clarify, expected in cases:
        assert (
            resolve_route(needs_rag=rag, needs_tool=tool, needs_clarification=clarify) is expected
        )

    # Tool axis disabled never routes to TOOL or RAG_TOOL
    for rag, tool, clarify in itertools.product((False, True), repeat=3):
        decision = IntentDecision(
            intent=ChatIntent.KNOWLEDGE_QUERY,
            needs_rag=rag,
            needs_tool=tool,
            tool_name="email" if tool else None,
            needs_clarification=clarify,
            retrieval_query="query" if rag else None,
            confidence=0.8,
            reason_codes=(IntentReasonCode.USER_DOCUMENT_REQUIRED,),
        )
        outcome = finalize_route(
            decision,
            has_ready_documents=True,
            tool_axis_enabled=False,
            classifier_retried=False,
            fallback_used=False,
            prompt_version="v1",
        )
        assert outcome.route not in {ChatRoute.TOOL, ChatRoute.RAG_TOOL}
        assert outcome.effective_needs_tool is False


def test_resolver_ready_documents_and_rag_tool_downgrade() -> None:
    def _dec(rag: bool, tool: bool, clarify: bool, name: str | None = None) -> IntentDecision:
        return IntentDecision(
            intent=ChatIntent.KNOWLEDGE_QUERY,
            needs_rag=rag,
            needs_tool=tool,
            tool_name=name,
            needs_clarification=clarify,
            retrieval_query="query" if rag else None,
            confidence=0.8,
            reason_codes=(IntentReasonCode.USER_DOCUMENT_REQUIRED,),
        )

    # No ready docs downgrades RAG to CHAT
    rag = finalize_route(
        _dec(True, False, False),
        has_ready_documents=False,
        tool_axis_enabled=False,
        classifier_retried=False,
        fallback_used=False,
        prompt_version="v1",
    )
    assert rag.route is ChatRoute.CHAT and IntentReasonCode.NO_READY_DOCUMENTS in rag.reason_codes

    # Clarify preserved even without ready docs
    clarify = finalize_route(
        _dec(True, False, True),
        has_ready_documents=False,
        tool_axis_enabled=False,
        classifier_retried=False,
        fallback_used=False,
        prompt_version="v1",
    )
    assert clarify.route is ChatRoute.CLARIFY

    # RAG_TOOL downgraded to TOOL
    rag_tool = finalize_route(
        _dec(True, True, False, "create_calendar_event"),
        has_ready_documents=True,
        tool_axis_enabled=True,
        classifier_retried=False,
        fallback_used=False,
        prompt_version="v1",
        available_tools=("create_calendar_event",),
    )
    assert rag_tool.route is ChatRoute.TOOL and rag_tool.effective_needs_rag is False

    # Narrowed RAG_TOOL with missing tool falls back to RAG
    rag_fallback = finalize_route(
        _dec(True, True, False, "missing_tool"),
        has_ready_documents=True,
        tool_axis_enabled=True,
        classifier_retried=False,
        fallback_used=False,
        prompt_version="v1",
        available_tools=("create_calendar_event",),
    )
    assert rag_fallback.route is ChatRoute.RAG and rag_fallback.effective_needs_rag is True


def test_resolver_tool_registration_exact_match_and_reason_codes() -> None:
    def _tool_dec(name: str) -> IntentDecision:
        return IntentDecision(
            intent=ChatIntent.ACTION_REQUEST,
            needs_rag=False,
            needs_tool=True,
            tool_name=name,
            needs_clarification=False,
            retrieval_query=None,
            confidence=0.8,
            reason_codes=(IntentReasonCode.EXTERNAL_ACTION_REQUESTED,),
        )

    # Registered tool matches
    matched = finalize_route(
        _tool_dec("create_calendar_event"),
        has_ready_documents=True,
        tool_axis_enabled=True,
        classifier_retried=False,
        fallback_used=False,
        prompt_version="v1",
        available_tools=("create_calendar_event",),
    )
    assert matched.route is ChatRoute.TOOL and matched.effective_needs_tool is True

    # Near misses fall back to CHAT without fuzzy matching
    for bad_name in ("create_calender_event", "send_email", "CREATE_CALENDAR_EVENT"):
        unmatched = finalize_route(
            _tool_dec(bad_name),
            has_ready_documents=True,
            tool_axis_enabled=True,
            classifier_retried=False,
            fallback_used=False,
            prompt_version="v1",
            available_tools=("create_calendar_event",),
        )
        assert (
            unmatched.route is ChatRoute.CHAT
            and IntentReasonCode.TOOL_NOT_AVAILABLE in unmatched.reason_codes
        )
