"""Route Resolver tests: fixture table plus targeted Policy Guard coverage."""

import importlib.util
from collections.abc import Mapping
from pathlib import Path

import pytest

from cowork_agent.domain.target_contracts import (
    Actionability,
    EmailRouteDecision,
    ExpectedDocumentType,
    ReasonCode,
    Route,
)
from cowork_agent.features.email_action_plan.correlation import TaskCandidate
from cowork_agent.features.email_action_plan.evidence import EvidenceStatus
from cowork_agent.features.email_action_plan.routing import (
    GUARD_REASON_BY_DOCUMENT_TYPE,
    RouteResolution,
    apply_policy_guards,
    candidate_requires_processing,
    resolve_candidate_after_retrieval,
    resolve_candidate_route,
    resolve_route,
)

_LOADER_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "routing" / "loader.py"
_spec = importlib.util.spec_from_file_location("routing_fixture_loader", _LOADER_PATH)
assert _spec is not None and _spec.loader is not None
loader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(loader)

_CASES = loader.load_routing_labels()

#: Reverse of GUARD_REASON_BY_DOCUMENT_TYPE used to synthesize the expected
#: document types from labeled reason codes. ``procedure`` is the canonical
#: document type for ``company_procedure_required`` (``guideline`` also maps
#: to it). ``internal_term_unresolved`` deliberately has no document type.
_DOCUMENT_TYPE_BY_GUARD_CODE: Mapping[ReasonCode, ExpectedDocumentType] = {
    ReasonCode.POLICY_REQUIRED: ExpectedDocumentType.COMPANY_POLICY,
    ReasonCode.GOVERNANCE_REQUIRED: ExpectedDocumentType.GOVERNANCE_DOCUMENT,
    ReasonCode.COMPANY_PROCEDURE_REQUIRED: ExpectedDocumentType.PROCEDURE,
    ReasonCode.TEMPLATE_REQUIRED: ExpectedDocumentType.TEMPLATE,
    ReasonCode.DOMAIN_KNOWLEDGE_REQUIRED: ExpectedDocumentType.PRODUCT_DOCUMENTATION,
}


def _decision_from_case(case: loader.RoutingCase) -> EmailRouteDecision:
    """Build an EmailRouteDecision from fixture labels.

    ``labels.expected_route`` is the expected resolver output; it is passed
    only as the classifier's proposal, which the resolver must ignore.
    """
    labels = case.labels
    reason_codes = tuple(ReasonCode(code.value) for code in labels.reason_codes)
    guard_relevant = tuple(
        code
        for code in reason_codes
        if code in _DOCUMENT_TYPE_BY_GUARD_CODE or code is ReasonCode.INTERNAL_TERM_UNRESOLVED
    )
    knowledge_gaps = tuple(f"knowledge gap: {code.value}" for code in guard_relevant)
    document_types = tuple(
        _DOCUMENT_TYPE_BY_GUARD_CODE[code]
        for code in reason_codes
        if code in _DOCUMENT_TYPE_BY_GUARD_CODE
    )
    actionable = labels.actionability in {
        loader.Actionability.ACTION_REQUIRED,
        loader.Actionability.ACTION_SUGGESTED,
        loader.Actionability.UNCLEAR,
    }
    return EmailRouteDecision(
        actionability=Actionability(labels.actionability.value),
        route=Route[labels.expected_route.name],
        candidate_action_item="Handle the request." if actionable else None,
        email_is_sufficient=labels.email_is_sufficient,
        knowledge_gaps=knowledge_gaps,
        retrieval_query="Resolve the documented knowledge gaps." if knowledge_gaps else None,
        expected_document_types=document_types,
        reason_codes=reason_codes,
        confidence=0.9,
    )


def test_resolve_route_reproduces_fixture_labels() -> None:
    for case in _CASES:
        resolution = resolve_route(_decision_from_case(case))
        assert resolution.route is Route[case.labels.expected_route.name], (
            f"Failed on case {case.id}"
        )


def test_unclear_but_sufficient_plans_directly_per_fr06() -> None:
    case = next(item for item in _CASES if item.id == "r-017")
    assert case.labels.expected_route is loader.ExpectedRoute.DIRECT_PLAN
    resolution = resolve_route(_decision_from_case(case))
    # FR-06 rung 3 checks sufficiency without an actionability restriction,
    # so an unclear-but-sufficient email plans directly (no guard fired).
    assert resolution.route is Route.DIRECT_PLAN
    assert resolution.forced_by_guard is False


def _decision(
    *,
    actionability: Actionability = Actionability.ACTION_REQUIRED,
    route: Route = Route.DIRECT_PLAN,
    candidate_action_item: str | None = "Handle the request.",
    email_is_sufficient: bool = True,
    knowledge_gaps: tuple[str, ...] = (),
    retrieval_query: str | None = None,
    expected_document_types: tuple[ExpectedDocumentType, ...] = (),
    reason_codes: tuple[ReasonCode, ...] = (ReasonCode.EMAIL_SELF_CONTAINED,),
    confidence: float = 0.9,
) -> EmailRouteDecision:
    return EmailRouteDecision(
        actionability=actionability,
        route=route,
        candidate_action_item=candidate_action_item,
        email_is_sufficient=email_is_sufficient,
        knowledge_gaps=knowledge_gaps,
        retrieval_query=retrieval_query,
        expected_document_types=expected_document_types,
        reason_codes=reason_codes,
        confidence=confidence,
    )


def test_guard_table_matches_readme_category_mapping() -> None:
    assert dict(GUARD_REASON_BY_DOCUMENT_TYPE) == {
        ExpectedDocumentType.COMPANY_POLICY: ReasonCode.POLICY_REQUIRED,
        ExpectedDocumentType.GOVERNANCE_DOCUMENT: ReasonCode.GOVERNANCE_REQUIRED,
        ExpectedDocumentType.PROCEDURE: ReasonCode.COMPANY_PROCEDURE_REQUIRED,
        ExpectedDocumentType.GUIDELINE: ReasonCode.COMPANY_PROCEDURE_REQUIRED,
        ExpectedDocumentType.TEMPLATE: ReasonCode.TEMPLATE_REQUIRED,
        ExpectedDocumentType.PRODUCT_DOCUMENTATION: ReasonCode.DOMAIN_KNOWLEDGE_REQUIRED,
    }


def test_each_guard_category_forces_retrieval_even_when_sufficient() -> None:
    for document_type, reason_code in GUARD_REASON_BY_DOCUMENT_TYPE.items():
        decision = _decision(
            email_is_sufficient=True,
            expected_document_types=(document_type,),
            reason_codes=(reason_code,),
        )
        resolution = resolve_route(decision)
        assert resolution.route is Route.RETRIEVE_RAG
        assert resolution.forced_by_guard is True
        assert reason_code in resolution.reason_codes


def test_guard_fires_on_trigger_code_without_document_types() -> None:
    trigger_codes = [
        ReasonCode.COMPANY_PROCEDURE_REQUIRED,
        ReasonCode.GOVERNANCE_REQUIRED,
        ReasonCode.POLICY_REQUIRED,
        ReasonCode.TEMPLATE_REQUIRED,
        ReasonCode.DOMAIN_KNOWLEDGE_REQUIRED,
        ReasonCode.INTERNAL_TERM_UNRESOLVED,
    ]
    for reason_code in trigger_codes:
        decision = _decision(email_is_sufficient=True, reason_codes=(reason_code,))
        resolution = resolve_route(decision)
        assert resolution.route is Route.RETRIEVE_RAG
        assert resolution.forced_by_guard is True


def test_guard_does_not_fire_without_trigger_signals() -> None:
    fired, codes = apply_policy_guards(_decision())
    assert fired is False
    assert codes == ()


def test_guard_codes_keep_decision_order_then_mapped_codes() -> None:
    decision = _decision(
        reason_codes=(ReasonCode.INTERNAL_TERM_UNRESOLVED, ReasonCode.POLICY_REQUIRED),
        expected_document_types=(
            ExpectedDocumentType.COMPANY_POLICY,
            ExpectedDocumentType.GOVERNANCE_DOCUMENT,
        ),
    )
    fired, codes = apply_policy_guards(decision)
    assert fired is True
    assert codes == (
        ReasonCode.INTERNAL_TERM_UNRESOLVED,
        ReasonCode.POLICY_REQUIRED,
        ReasonCode.GOVERNANCE_REQUIRED,
    )
    resolution = resolve_route(decision)
    assert resolution.route is Route.RETRIEVE_RAG
    assert resolution.reason_codes == codes


def test_informational_and_irrelevant_never_retrieve() -> None:
    for actionability in (Actionability.INFORMATIONAL, Actionability.IRRELEVANT):
        for email_is_sufficient in (True, False):
            decision = _decision(
                actionability=actionability,
                email_is_sufficient=email_is_sufficient,
                reason_codes=(ReasonCode.NO_ACTION,),
            )
            resolution = resolve_route(decision)
            assert resolution.route is Route.NO_ACTION
            assert resolution.forced_by_guard is False
            assert resolution.mode == "full"


def test_no_action_rung_beats_guard_categories() -> None:
    decision = _decision(
        actionability=Actionability.INFORMATIONAL,
        reason_codes=(ReasonCode.POLICY_REQUIRED,),
        expected_document_types=(ExpectedDocumentType.COMPANY_POLICY,),
    )
    resolution = resolve_route(decision)
    assert resolution.route is Route.NO_ACTION
    assert resolution.forced_by_guard is False


def test_no_action_defaults_to_no_action_reason_code() -> None:
    decision = _decision(actionability=Actionability.IRRELEVANT, reason_codes=())
    assert resolve_route(decision).reason_codes == (ReasonCode.NO_ACTION,)


def test_direct_fallback_is_partial_when_no_retrievable_gap() -> None:
    decision = _decision(email_is_sufficient=False, reason_codes=(ReasonCode.EMAIL_SELF_CONTAINED,))
    resolution = resolve_route(decision)
    assert resolution.route is Route.DIRECT_PLAN
    assert resolution.mode == "partial"
    assert resolution.forced_by_guard is False


def test_low_confidence_without_gaps_falls_back_to_partial_direct_plan() -> None:
    resolution = resolve_route(_decision(confidence=0.2))
    assert resolution.route is Route.DIRECT_PLAN
    assert resolution.mode == "partial"


def test_confidence_floor_boundary_is_inclusive() -> None:
    at_floor = resolve_route(_decision(confidence=0.5))
    below_floor = resolve_route(_decision(confidence=0.49))
    assert at_floor.route is Route.DIRECT_PLAN
    assert at_floor.mode == "full"
    assert below_floor.route is Route.DIRECT_PLAN
    assert below_floor.mode == "partial"


def test_confidence_floor_is_configurable() -> None:
    decision = _decision(confidence=0.7)
    assert resolve_route(decision, confidence_floor=0.7).mode == "full"
    assert resolve_route(decision, confidence_floor=0.8).mode == "partial"


def test_unclear_without_guard_signals_routes_to_retrieval() -> None:
    # An unclear email is conservative only when it is not self-contained;
    # unclear-but-sufficient plans directly (FR-06 rung 3).
    decision = _decision(actionability=Actionability.UNCLEAR, email_is_sufficient=False)
    resolution = resolve_route(decision)
    assert resolution.route is Route.RETRIEVE_RAG
    assert resolution.forced_by_guard is False


def test_knowledge_gaps_without_guard_codes_route_to_retrieval() -> None:
    decision = _decision(email_is_sufficient=False, knowledge_gaps=("deadline unknown",))
    resolution = resolve_route(decision)
    assert resolution.route is Route.RETRIEVE_RAG
    assert resolution.forced_by_guard is False


def test_retrieval_query_without_guard_codes_routes_to_retrieval() -> None:
    decision = _decision(email_is_sufficient=False, retrieval_query="find the deadline")
    assert resolve_route(decision).route is Route.RETRIEVE_RAG


def test_resolver_ignores_classifier_proposed_route() -> None:
    for proposed_route in Route:
        resolution = resolve_route(_decision(route=proposed_route))
        assert resolution.route is Route.DIRECT_PLAN
        assert resolution == resolve_route(_decision(route=Route.NO_ACTION))


def test_same_decision_resolves_identically_twice() -> None:
    decision = _decision(email_is_sufficient=False, knowledge_gaps=("deadline unknown",))
    first = resolve_route(decision)
    second = resolve_route(decision)
    assert first == second
    assert isinstance(first, RouteResolution)


def test_resolution_is_pure_under_environment_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    decision = _decision(email_is_sufficient=False, knowledge_gaps=("deadline unknown",))
    monkeypatch.setenv("COWORK_AGENT_TEST_ENV", "one")
    first = resolve_route(decision)
    monkeypatch.setenv("COWORK_AGENT_TEST_ENV", "two")
    second = resolve_route(decision)
    assert first == second


def _candidate(*decisions: EmailRouteDecision) -> TaskCandidate:
    return TaskCandidate(
        candidate_key="thread-1",
        gmail_thread_id="thread-1",
        incident_key=None,
        source_message_ids=tuple(f"m{i + 1}" for i in range(len(decisions))),
        decisions=tuple((f"m{i + 1}", decision) for i, decision in enumerate(decisions)),
    )


def test_candidate_route_retrieve_rag_wins_over_direct_plan() -> None:
    candidate = _candidate(
        _decision(),  # sufficient -> DIRECT_PLAN
        _decision(
            email_is_sufficient=False,
            knowledge_gaps=("policy location",),
            reason_codes=(ReasonCode.POLICY_REQUIRED,),
        ),  # -> RETRIEVE_RAG
    )
    resolution = resolve_candidate_route(candidate)
    assert resolution.route is Route.RETRIEVE_RAG
    # Only the winning member's codes are carried, in member order.
    assert resolution.reason_codes == (ReasonCode.POLICY_REQUIRED,)


def test_candidate_route_direct_plan_beats_no_action() -> None:
    candidate = _candidate(
        _decision(actionability=Actionability.INFORMATIONAL, reason_codes=()),
        _decision(),
    )
    resolution = resolve_candidate_route(candidate)
    assert resolution.route is Route.DIRECT_PLAN
    assert resolution.reason_codes == (ReasonCode.EMAIL_SELF_CONTAINED,)


def test_candidate_route_all_no_action() -> None:
    candidate = _candidate(
        _decision(actionability=Actionability.INFORMATIONAL, reason_codes=()),
        _decision(actionability=Actionability.IRRELEVANT, reason_codes=()),
    )
    resolution = resolve_candidate_route(candidate)
    assert resolution.route is Route.NO_ACTION
    assert resolution.forced_by_guard is False


@pytest.mark.parametrize(
    ("evidence", "route", "mode"),
    [
        (EvidenceStatus.SUPPORTED, Route.RETRIEVE_RAG, "full"),
        (EvidenceStatus.UNSUPPORTED, Route.DIRECT_PLAN, "full"),
        (EvidenceStatus.UNAVAILABLE, Route.RETRIEVE_RAG, "partial"),
    ],
)
def test_post_retrieval_route_matrix_for_self_sufficient_candidate(
    evidence: EvidenceStatus, route: Route, mode: str
) -> None:
    resolution = resolve_candidate_after_retrieval(_candidate(_decision()), evidence)
    assert (resolution.route, resolution.mode) == (route, mode)


def test_unsupported_evidence_produces_partial_direct_plan_when_not_safe_for_full() -> None:
    decisions = [
        _decision(email_is_sufficient=False),
        _decision(confidence=0.5),
        _decision(
            expected_document_types=(ExpectedDocumentType.COMPANY_POLICY,),
            reason_codes=(ReasonCode.POLICY_REQUIRED,),
        ),
        _decision(actionability=Actionability.UNCLEAR),
    ]
    for decision in decisions:
        resolution = resolve_candidate_after_retrieval(
            _candidate(decision), EvidenceStatus.UNSUPPORTED
        )
        assert resolution.route is Route.DIRECT_PLAN
        assert resolution.mode == "partial"


def test_candidate_processing_filter_keeps_unclear_and_drops_only_no_action() -> None:
    assert candidate_requires_processing(_candidate(_decision(actionability=Actionability.UNCLEAR)))
    assert not candidate_requires_processing(
        _candidate(_decision(actionability=Actionability.INFORMATIONAL))
    )


def test_candidate_route_propagates_guard_and_partial_mode() -> None:
    guarded = _candidate(
        _decision(
            reason_codes=(ReasonCode.TEMPLATE_REQUIRED,),
            expected_document_types=(ExpectedDocumentType.TEMPLATE,),
        ),
    )
    forced = resolve_candidate_route(guarded)
    assert forced.route is Route.RETRIEVE_RAG
    assert forced.forced_by_guard is True

    partial = _candidate(
        _decision(
            email_is_sufficient=False,
            knowledge_gaps=(),
            retrieval_query=None,
            actionability=Actionability.ACTION_REQUIRED,
        ),
    )
    fallback = resolve_candidate_route(partial)
    assert fallback.route is Route.DIRECT_PLAN
    assert fallback.mode == "partial"
