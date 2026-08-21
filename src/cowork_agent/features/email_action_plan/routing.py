"""Deterministic Route Resolver with Policy Guards (PRD-v1 FR-06, FR-07).

Pure, table-driven resolution: the Route Resolver combines one Route Decision
(``EmailRouteDecision``, master-comparison §6.2) with the FR-07 Policy Guards
and a confidence floor to select the final Route. No I/O, no randomness, no
framework imports — the resolver stays a pure deterministic function
(master-comparison §3.7).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from cowork_agent.domain.target_contracts import (
    Actionability,
    EmailRouteDecision,
    ExpectedDocumentType,
    ReasonCode,
    Route,
)

from .correlation import TaskCandidate
from .evidence import EvidenceStatus

#: Policy Guard table (PRD-v1 FR-07): each Expected Document Type maps to the
#: reason code that forces retrieval. Cross-checked against
#: ``tests/fixtures/routing/README.md`` ("FR-07 guard category coverage").
GUARD_REASON_BY_DOCUMENT_TYPE: Mapping[ExpectedDocumentType, ReasonCode] = {
    ExpectedDocumentType.COMPANY_POLICY: ReasonCode.POLICY_REQUIRED,
    ExpectedDocumentType.GOVERNANCE_DOCUMENT: ReasonCode.GOVERNANCE_REQUIRED,
    ExpectedDocumentType.PROCEDURE: ReasonCode.COMPANY_PROCEDURE_REQUIRED,
    ExpectedDocumentType.GUIDELINE: ReasonCode.COMPANY_PROCEDURE_REQUIRED,
    ExpectedDocumentType.TEMPLATE: ReasonCode.TEMPLATE_REQUIRED,
    ExpectedDocumentType.PRODUCT_DOCUMENTATION: ReasonCode.DOMAIN_KNOWLEDGE_REQUIRED,
}

_NO_ACTION_ACTIONABILITY = frozenset({Actionability.INFORMATIONAL, Actionability.IRRELEVANT})
_GUARD_ACTIONABILITY = frozenset(
    {Actionability.ACTION_REQUIRED, Actionability.ACTION_SUGGESTED, Actionability.UNCLEAR}
)
#: Reason codes that by themselves trigger a Policy Guard: every
#: ``*_required`` code plus unresolved company-specific terminology (FR-07).
_GUARD_TRIGGER_CODES = frozenset(
    {
        ReasonCode.COMPANY_PROCEDURE_REQUIRED,
        ReasonCode.GOVERNANCE_REQUIRED,
        ReasonCode.POLICY_REQUIRED,
        ReasonCode.TEMPLATE_REQUIRED,
        ReasonCode.DOMAIN_KNOWLEDGE_REQUIRED,
        ReasonCode.INTERNAL_TERM_UNRESOLVED,
    }
)


@dataclass(frozen=True, slots=True)
class RouteResolution:
    """Final verdict of the Route Resolver for one Route Decision.

    ``reason_codes`` are resolver-level: the decision codes that fired plus
    any codes added by Policy Guards. ``forced_by_guard`` marks a retrieval
    forced by an FR-07 Policy Guard. ``mode`` is ``"partial"`` only for the
    direct-plan fallback that must surface a missing-information warning
    downstream (PRD-v1 FR-06 last rung).
    """

    route: Route
    reason_codes: tuple[ReasonCode, ...]
    forced_by_guard: bool
    mode: Literal["full", "partial"]


def apply_policy_guards(
    decision: EmailRouteDecision,
) -> tuple[bool, tuple[ReasonCode, ...]]:
    """Evaluate the FR-07 Policy Guards against one Route Decision.

    A guard fires when actionability is ``action_required``,
    ``action_suggested``, or ``unclear`` and the decision either expects
    company documents, already carries a ``*_required`` reason code, or flags
    an unresolved internal term. Returns ``(guard_fired, guard_codes)``;
    guard codes keep a stable order — decision-order trigger codes first,
    then codes mapped from the expected document types — deduplicated.
    """
    if decision.actionability not in _GUARD_ACTIONABILITY:
        return False, ()
    if not decision.expected_document_types and not any(
        code in _GUARD_TRIGGER_CODES for code in decision.reason_codes
    ):
        return False, ()
    codes: list[ReasonCode] = [
        code for code in decision.reason_codes if code in _GUARD_TRIGGER_CODES
    ]
    for document_type in decision.expected_document_types:
        mapped = GUARD_REASON_BY_DOCUMENT_TYPE[document_type]
        if mapped not in codes:
            codes.append(mapped)
    return True, tuple(codes)


def resolve_route(
    decision: EmailRouteDecision,
    *,
    confidence_floor: float = 0.5,
) -> RouteResolution:
    """Resolve the final Route for one Route Decision (PRD-v1 FR-06).

    Deterministic ladder; the first matching rung wins (FR-06 order):

    1. ``informational`` / ``irrelevant`` -> NO_ACTION.
    2. A Policy Guard fires (PRD-v1 FR-07) -> RETRIEVE_RAG, forced. Guards sit
       before sufficiency because they *force* retrieval even for emails the
       classifier calls self-contained.
    3. The email is sufficient and confidence reaches ``confidence_floor``
       -> DIRECT_PLAN (full mode). FR-06 imposes no actionability restriction
       here; rung 1 already excluded informational/irrelevant, so an
       unclear-but-sufficient decision also plans directly.
    4. ``unclear`` -> RETRIEVE_RAG: the knowledge state is unknown, so the
       resolver stays conservative.
    5. Remaining actionable decisions -> RETRIEVE_RAG when knowledge gaps or
       a retrieval query are present, otherwise DIRECT_PLAN in ``partial``
       mode (the direct fallback with a missing-information warning).

    The classifier-proposed ``decision.route`` is deliberately ignored: the
    Route Resolver owns the final route.
    """
    if decision.actionability in _NO_ACTION_ACTIONABILITY:
        return RouteResolution(
            route=Route.NO_ACTION,
            reason_codes=decision.reason_codes or (ReasonCode.NO_ACTION,),
            forced_by_guard=False,
            mode="full",
        )
    guard_fired, guard_codes = apply_policy_guards(decision)
    if guard_fired:
        return RouteResolution(
            route=Route.RETRIEVE_RAG,
            reason_codes=tuple(dict.fromkeys((*decision.reason_codes, *guard_codes))),
            forced_by_guard=True,
            mode="full",
        )
    if decision.email_is_sufficient and decision.confidence >= confidence_floor:
        return RouteResolution(
            route=Route.DIRECT_PLAN,
            reason_codes=decision.reason_codes,
            forced_by_guard=False,
            mode="full",
        )
    if decision.actionability is Actionability.UNCLEAR:
        return RouteResolution(
            route=Route.RETRIEVE_RAG,
            reason_codes=decision.reason_codes,
            forced_by_guard=False,
            mode="full",
        )
    if decision.knowledge_gaps or decision.retrieval_query:
        return RouteResolution(
            route=Route.RETRIEVE_RAG,
            reason_codes=decision.reason_codes,
            forced_by_guard=False,
            mode="full",
        )
    return RouteResolution(
        route=Route.DIRECT_PLAN,
        reason_codes=decision.reason_codes,
        forced_by_guard=False,
        mode="partial",
    )


#: Route precedence for candidate-level aggregation: the most knowledge-hungry
#: member route wins so a Task Candidate never skips retrieval it needs.
_ROUTE_PRECEDENCE: Mapping[Route, int] = {
    Route.RETRIEVE_RAG: 2,
    Route.DIRECT_PLAN: 1,
    Route.NO_ACTION: 0,
}


def resolve_candidate_route(
    candidate: TaskCandidate, *, confidence_floor: float = 0.5
) -> RouteResolution:
    """Resolve the single Route for one Task Candidate (frozen contract rule 4).

    Every member Route Decision is resolved with :func:`resolve_route`; the
    candidate route is the highest-precedence member route (RETRIEVE_RAG >
    DIRECT_PLAN > NO_ACTION). ``reason_codes`` keep member order, deduplicated,
    restricted to the winning members; ``forced_by_guard`` and ``mode``
    propagate from those winners (any guard / any partial).
    """
    resolutions = tuple(
        resolve_route(decision, confidence_floor=confidence_floor)
        for _message_id, decision in candidate.decisions
    )
    winning_route = max(
        (resolution.route for resolution in resolutions),
        key=lambda route: _ROUTE_PRECEDENCE[route],
    )
    winners = tuple(resolution for resolution in resolutions if resolution.route is winning_route)
    return RouteResolution(
        route=winning_route,
        reason_codes=tuple(
            dict.fromkeys(code for resolution in winners for code in resolution.reason_codes)
        ),
        forced_by_guard=any(resolution.forced_by_guard for resolution in winners),
        mode="partial" if any(resolution.mode == "partial" for resolution in winners) else "full",
    )


def candidate_requires_processing(candidate: TaskCandidate) -> bool:
    """Return whether a candidate has any non-informational decision.

    This deliberately ignores the classifier's provisional route: retrieve-first
    routing evaluates evidence only after this inexpensive NO_ACTION filter.
    """
    return any(
        decision.actionability not in _NO_ACTION_ACTIONABILITY
        for _message_id, decision in candidate.decisions
    )


def resolve_candidate_after_retrieval(
    candidate: TaskCandidate,
    evidence_status: EvidenceStatus,
    *,
    confidence_floor: float = 0.5,
) -> RouteResolution:
    """Resolve the final route after the evidence gate has run.

    A healthy retrieval with no acceptable Cohere evidence may use a full
    direct plan only when every actionable member is self-sufficient and has
    confidence strictly above the configured floor.  Policy guards remain
    visible, but become partial direct plans when retrieval cannot support
    them; weak chunks are never passed to generation.
    """
    actionable = tuple(
        decision
        for _message_id, decision in candidate.decisions
        if decision.actionability not in _NO_ACTION_ACTIONABILITY
    )
    if not actionable:
        return RouteResolution(
            route=Route.NO_ACTION,
            reason_codes=(ReasonCode.NO_ACTION,),
            forced_by_guard=False,
            mode="full",
        )

    guard_results = tuple(apply_policy_guards(decision) for decision in actionable)
    guard_fired = any(fired for fired, _codes in guard_results)
    reason_codes = tuple(
        dict.fromkeys(
            code
            for decision, (_fired, guard_codes) in zip(actionable, guard_results, strict=True)
            for code in (*decision.reason_codes, *guard_codes)
        )
    )
    if evidence_status is EvidenceStatus.SUPPORTED:
        return RouteResolution(Route.RETRIEVE_RAG, reason_codes, guard_fired, "full")
    if evidence_status is EvidenceStatus.UNAVAILABLE:
        return RouteResolution(Route.RETRIEVE_RAG, reason_codes, guard_fired, "partial")

    all_sufficient = all(
        decision.email_is_sufficient and decision.confidence > confidence_floor
        for decision in actionable
    )
    allows_full_direct = all_sufficient and not guard_fired and all(
        decision.actionability is not Actionability.UNCLEAR for decision in actionable
    )
    return RouteResolution(
        route=Route.DIRECT_PLAN,
        reason_codes=reason_codes,
        forced_by_guard=guard_fired,
        mode="full" if allows_full_direct else "partial",
    )
