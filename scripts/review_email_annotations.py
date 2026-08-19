"""Validate private annotation proposals and compare them with production routing."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import cast

from cowork_agent.domain.target_contracts import (
    Actionability,
    EmailRouteDecision,
    ExpectedDocumentType,
    Route,
)
from cowork_agent.features.email_action_plan.routing import resolve_route

try:
    from scripts.email_evaluation_artifacts import (
        RUBRIC_VERSION,
        validate_candidate_dataset,
        validate_proposal_batch,
    )
except ModuleNotFoundError:
    from email_evaluation_artifacts import (  # type: ignore[no-redef]
        RUBRIC_VERSION,
        validate_candidate_dataset,
        validate_proposal_batch,
    )

MAX_PROPOSAL_COUNT = 70
ROUTE_VALUES = tuple(route.value for route in Route)
TARGET_ROUTE_DISTRIBUTION = {
    "no_action": 24,
    "direct_plan": 23,
    "retrieve_rag": 23,
}

_CANDIDATE_KEYS = frozenset(
    {
        "schema_version",
        "fetched_at",
        "gmail_query",
        "ordering",
        "case_count",
        "cases",
    }
)
_CANDIDATE_CASE_KEYS = frozenset(
    {
        "case_id",
        "source_message_id",
        "gmail_thread_id",
        "sender",
        "subject",
        "received_at",
        "labels",
        "gmail_content",
    }
)
_CANDIDATE_REFERENCE_KEYS = _CANDIDATE_CASE_KEYS - {"gmail_content"}


def resolver_expected_route(ground_truth: Mapping[str, object]) -> str:
    """Return the route selected by the production resolver for one proposal."""

    decision = EmailRouteDecision(
        actionability=Actionability(str(ground_truth["actionability"])),
        route=Route.RETRIEVE_RAG,
        candidate_action_item=None,
        email_is_sufficient=bool(ground_truth["email_is_sufficient"]),
        knowledge_gaps=tuple(str(item) for item in ground_truth["knowledge_gaps"]),
        retrieval_query=None,
        expected_document_types=tuple(
            ExpectedDocumentType(str(item))
            for item in ground_truth["expected_document_types"]
        ),
        reason_codes=(),
        confidence=1.0,
    )
    return resolve_route(decision).route.value


def validate_and_enrich_proposals(
    candidates: object, proposals: object
) -> dict[str, object]:
    """Validate proposals, join their IDs, and record resolver consistency.

    Candidate content is deliberately replaced with a private placeholder while
    the shared candidate validator checks the metadata contract. The returned
    object contains proposal metadata only; candidate message content never
    enters the Python result.
    """

    proposal_count = _proposal_count(proposals)
    candidate_sources = _candidate_reference_map(candidates)
    validated = validate_proposal_batch(proposals, expected_count=proposal_count)
    validated_cases = cast(list[Mapping[str, object]], validated["cases"])

    proposed_routes: Counter[str] = Counter()
    resolver_routes: Counter[str] = Counter()
    enriched_cases: list[dict[str, object]] = []
    for index, proposal_case in enumerate(validated_cases, start=1):
        case_id = str(proposal_case["case_id"])
        source_message_id = str(proposal_case["source_message_id"])
        candidate_source = candidate_sources.get(case_id)
        if candidate_source is None:
            raise ValueError(
                f"proposal case {index} case_id {case_id!r} has no candidate match"
            )
        if candidate_source != source_message_id:
            raise ValueError(
                f"proposal case {index} source_message_id does not match candidate "
                f"for case_id {case_id!r}"
            )

        ground_truth = cast(
            Mapping[str, object], proposal_case["proposed_ground_truth"]
        )
        proposed_route = str(ground_truth["expected_route"])
        resolved_route = resolver_expected_route(ground_truth)
        proposed_routes[proposed_route] += 1
        resolver_routes[resolved_route] += 1

        enriched_case = dict(proposal_case)
        enriched_case["resolver_expected_route"] = resolved_route
        enriched_case["consistency_status"] = (
            "consistent" if proposed_route == resolved_route else "needs_review"
        )
        enriched_cases.append(enriched_case)

    route_distribution = _complete_route_counts(proposed_routes)
    resolver_route_distribution = _complete_route_counts(resolver_routes)
    metadata = {
        "proposal_count": len(enriched_cases),
        "route_distribution": route_distribution,
        "resolver_route_distribution": resolver_route_distribution,
        "target_route_distribution": dict(TARGET_ROUTE_DISTRIBUTION),
        "route_shortages": {
            route: max(0, TARGET_ROUTE_DISTRIBUTION[route] - route_distribution[route])
            for route in ROUTE_VALUES
        },
    }
    return {
        "schema_version": validated["schema_version"],
        "rubric_version": RUBRIC_VERSION,
        "case_count": len(enriched_cases),
        "cases": enriched_cases,
        "metadata": metadata,
    }


def _proposal_count(value: object) -> int:
    if not isinstance(value, Mapping) or not isinstance(value.get("cases"), list):
        return MAX_PROPOSAL_COUNT
    count = len(cast(list[object], value["cases"]))
    if count > MAX_PROPOSAL_COUNT:
        raise ValueError(
            f"proposal batch contains {count} cases; maximum is {MAX_PROPOSAL_COUNT}"
        )
    return count


def _candidate_reference_map(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError("candidate dataset must be a JSON object")
    _require_exact_keys(value, _CANDIDATE_KEYS, "candidate dataset")
    raw_cases = value["cases"]
    if not isinstance(raw_cases, list):
        raise ValueError("candidate dataset.cases must be a JSON array")

    redacted_cases: list[dict[str, object]] = []
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, Mapping):
            raise ValueError(f"candidate dataset.cases[{index}] must be a JSON object")
        location = f"candidate dataset.cases[{index}]"
        _require_exact_keys(raw_case, _CANDIDATE_CASE_KEYS, location)
        redacted_case = {
            key: raw_case[key] for key in _CANDIDATE_REFERENCE_KEYS
        }
        redacted_case["gmail_content"] = "<private candidate content redacted>"
        redacted_cases.append(redacted_case)

    redacted_dataset = {
        key: value[key] for key in _CANDIDATE_KEYS if key != "cases"
    }
    redacted_dataset["cases"] = redacted_cases
    validated = validate_candidate_dataset(
        redacted_dataset, expected_count=len(redacted_cases)
    )
    references: dict[str, str] = {}
    for raw_case in cast(list[Mapping[str, object]], validated["cases"]):
        references[str(raw_case["case_id"])] = str(raw_case["source_message_id"])
    return references


def _complete_route_counts(counts: Counter[str]) -> dict[str, int]:
    return {route: counts.get(route, 0) for route in ROUTE_VALUES}


def _require_exact_keys(
    value: Mapping[str, object], expected: frozenset[str], location: str
) -> None:
    actual = set(value)
    missing = expected - actual
    if missing:
        raise ValueError(f"{location} missing required key(s): {', '.join(sorted(missing))}")
    unknown = actual - expected
    if unknown:
        raise ValueError(f"{location} has unknown key(s): {', '.join(sorted(unknown))}")
