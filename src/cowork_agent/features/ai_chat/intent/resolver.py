"""Pure truth-table resolver and deterministic capability narrowing."""

from __future__ import annotations

from cowork_agent.domain.chat_contracts import (
    ChatRoute,
    IntentDecision,
    IntentReasonCode,
    RoutingOutcome,
)


def resolve_route(*, needs_rag: bool, needs_tool: bool, needs_clarification: bool) -> ChatRoute:
    """Map three boolean axes to one route in documented precedence order."""

    if needs_clarification:
        return ChatRoute.CLARIFY
    if needs_rag and needs_tool:
        return ChatRoute.RAG_TOOL
    if needs_rag:
        return ChatRoute.RAG
    if needs_tool:
        return ChatRoute.TOOL
    return ChatRoute.CHAT


def finalize_route(
    decision: IntentDecision,
    *,
    has_ready_documents: bool,
    tool_axis_enabled: bool,
    classifier_retried: bool,
    fallback_used: bool,
    prompt_version: str,
) -> RoutingOutcome:
    """Apply tool and readiness gates without mutating the classifier decision."""

    effective_tool = decision.needs_tool and tool_axis_enabled
    reasons = list(decision.reason_codes)
    if decision.needs_tool and not tool_axis_enabled:
        _append_unique(reasons, IntentReasonCode.TOOL_REQUESTED_BUT_DISABLED)
    route = resolve_route(
        needs_rag=decision.needs_rag,
        needs_tool=effective_tool,
        needs_clarification=decision.needs_clarification,
    )
    effective_rag = decision.needs_rag
    retrieval_query = decision.retrieval_query if effective_rag else None
    if route is ChatRoute.RAG and not has_ready_documents:
        route = ChatRoute.CHAT
        effective_rag = False
        retrieval_query = None
        _append_unique(reasons, IntentReasonCode.NO_READY_DOCUMENTS)
    return RoutingOutcome(
        decision=decision,
        route=route,
        effective_needs_rag=effective_rag,
        effective_needs_tool=effective_tool,
        effective_needs_clarification=decision.needs_clarification,
        retrieval_query=retrieval_query,
        reason_codes=tuple(reasons),
        classifier_retried=classifier_retried,
        fallback_used=fallback_used,
        prompt_version=prompt_version,
    )


def _append_unique(reasons: list[IntentReasonCode], code: IntentReasonCode) -> None:
    if code not in reasons:
        reasons.append(code)
