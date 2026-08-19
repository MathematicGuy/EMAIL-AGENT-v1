"""Validate private annotation proposals and promote reviewed truth safely."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
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
        atomic_write_json,
        load_json_object,
        validate_candidate_dataset,
        validate_golden_dataset,
        validate_proposal_batch,
        validate_review_export,
    )
except ModuleNotFoundError:
    from email_evaluation_artifacts import (  # type: ignore[no-redef]
        RUBRIC_VERSION,
        atomic_write_json,
        load_json_object,
        validate_candidate_dataset,
        validate_golden_dataset,
        validate_proposal_batch,
        validate_review_export,
    )

MAX_PROPOSAL_COUNT = 70
REQUIRED_REVIEW_COUNT = 70
MINIMUM_UNCHANGED_RATE = 0.90
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


def promotion_metrics(reviewed: Mapping[str, object]) -> dict[str, object]:
    """Measure proposal agreement and final resolver consistency."""

    validated = validate_review_export(reviewed, expected_count=None)
    return _promotion_metrics_from_validated(validated)


def _promotion_metrics_from_validated(
    reviewed: Mapping[str, object],
) -> dict[str, object]:
    cases = cast(list[Mapping[str, object]], reviewed["cases"])
    unchanged_actionability = 0
    unchanged_route = 0
    corrected_case_ids: list[str] = []
    final_resolver_conflicts: list[str] = []

    for case in cases:
        proposal = cast(Mapping[str, object], case["proposal"])
        final = cast(Mapping[str, object], case["final"])
        case_id = str(case["case_id"])
        if proposal["actionability"] == final["actionability"]:
            unchanged_actionability += 1
        if proposal["expected_route"] == final["expected_route"]:
            unchanged_route += 1
        if case["review_status"] == "corrected":
            corrected_case_ids.append(case_id)
        if final["expected_route"] != resolver_expected_route(final):
            final_resolver_conflicts.append(case_id)

    total = len(cases)
    actionability_rate = unchanged_actionability / total if total else 0.0
    route_rate = unchanged_route / total if total else 0.0
    return {
        "review_count": total,
        "actionability_agreement": {
            "unchanged": unchanged_actionability,
            "total": total,
            "rate": actionability_rate,
        },
        "route_agreement": {
            "unchanged": unchanged_route,
            "total": total,
            "rate": route_rate,
        },
        "corrected_case_ids": corrected_case_ids,
        "final_resolver_conflict_case_ids": final_resolver_conflicts,
        "systematic_errors_resolved": reviewed["systematic_errors_resolved"],
    }


def promote_reviewed_annotations(
    reviewed: Mapping[str, object],
    second_pass: object | None = None,
    *,
    reviewed_at: str | None = None,
) -> dict[str, object]:
    """Apply the promotion gate and return a truth-only golden dataset."""

    validated = validate_review_export(
        reviewed, expected_count=REQUIRED_REVIEW_COUNT
    )
    second_pass_ids = _second_pass_case_ids(second_pass)
    metrics = _promotion_metrics_from_validated(validated)
    actionability = cast(Mapping[str, object], metrics["actionability_agreement"])
    route = cast(Mapping[str, object], metrics["route_agreement"])

    actionability_rate = float(actionability["rate"])
    if actionability_rate < MINIMUM_UNCHANGED_RATE:
        raise ValueError(
            f"actionability agreement {actionability_rate:.1%} is below "
            f"{MINIMUM_UNCHANGED_RATE:.1%}"
        )
    route_rate = float(route["rate"])
    if route_rate < MINIMUM_UNCHANGED_RATE:
        raise ValueError(
            f"route agreement {route_rate:.1%} is below "
            f"{MINIMUM_UNCHANGED_RATE:.1%}"
        )
    if validated["systematic_errors_resolved"] is not True:
        raise ValueError("systematic errors remain unresolved")

    conflict_ids = cast(
        list[str], metrics["final_resolver_conflict_case_ids"]
    )
    if conflict_ids:
        raise ValueError(
            "final resolver conflicts: " + ", ".join(conflict_ids)
        )

    corrected_ids = frozenset(cast(list[str], metrics["corrected_case_ids"]))
    missing_ids = sorted(corrected_ids - second_pass_ids)
    if missing_ids:
        raise ValueError("missing second-pass cases: " + ", ".join(missing_ids))
    extra_ids = sorted(second_pass_ids - corrected_ids)
    if extra_ids:
        raise ValueError("unexpected second-pass cases: " + ", ".join(extra_ids))

    annotation_timestamp: object = (
        validated["reviewed_at"] if reviewed_at is None else reviewed_at
    )
    golden_cases: list[dict[str, object]] = []
    for case in cast(list[Mapping[str, object]], validated["cases"]):
        golden_cases.append(
            {
                "case_id": case["case_id"],
                "source_message_id": case["source_message_id"],
                "ground_truth": dict(
                    cast(Mapping[str, object], case["final"])
                ),
                "annotation": {
                    "source": "human_reviewed",
                    "rubric_version": RUBRIC_VERSION,
                    "reviewed_at": annotation_timestamp,
                },
            }
        )

    golden = {
        "schema_version": 1,
        "rubric_version": RUBRIC_VERSION,
        "case_count": len(golden_cases),
        "cases": golden_cases,
    }
    return validate_golden_dataset(
        golden, expected_count=REQUIRED_REVIEW_COUNT
    )


def _second_pass_case_ids(value: object | None) -> frozenset[str]:
    if value is None:
        return frozenset()
    _reject_private_content(value, "second-pass")

    if isinstance(value, Mapping):
        if "case_ids" in value and "cases" in value:
            raise ValueError("second-pass must contain case_ids or cases, not both")
        if "case_ids" in value:
            raw_ids = value["case_ids"]
        elif "cases" in value:
            raw_cases = value["cases"]
            if not isinstance(raw_cases, list):
                raise ValueError("second-pass.cases must be a JSON array")
            raw_ids = []
            for index, raw_case in enumerate(raw_cases):
                if isinstance(raw_case, Mapping):
                    if "case_id" not in raw_case:
                        raise ValueError(
                            f"second-pass.cases[{index}] missing required key: case_id"
                        )
                    raw_ids.append(raw_case["case_id"])
                else:
                    raw_ids.append(raw_case)
        elif not value:
            return frozenset()
        else:
            raise ValueError("second-pass must contain case_ids or cases")
        if "case_count" in value and value["case_count"] != len(raw_ids):
            raise ValueError("second-pass.case_count does not match its cases")
        if "rubric_version" in value and value["rubric_version"] != RUBRIC_VERSION:
            raise ValueError(f"second-pass.rubric_version must be '{RUBRIC_VERSION}'")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        raw_ids = list(value)
    else:
        raise ValueError("second-pass must be a JSON object or array")

    if not isinstance(raw_ids, list):
        raise ValueError("second-pass.case_ids must be a JSON array")
    if any(not isinstance(case_id, str) or not case_id.strip() for case_id in raw_ids):
        raise ValueError("second-pass case IDs must be non-empty strings")
    case_ids = [str(case_id) for case_id in raw_ids]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("second-pass contains duplicate case IDs")
    return frozenset(case_ids)


def _reject_private_content(value: object, location: str) -> None:
    blocked_keys = frozenset({"gmail_content", "snippet", "normalized_body"})
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if key in blocked_keys:
                raise ValueError(f"{location}.{key} is private content")
            _reject_private_content(nested, f"{location}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, nested in enumerate(value):
            _reject_private_content(nested, f"{location}[{index}]")


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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate Email annotation proposals and promote reviewed truth."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser(
        "validate-proposals", help="validate and enrich proposal metadata"
    )
    validate_parser.add_argument("--candidates", type=Path, required=True)
    validate_parser.add_argument("--proposals", type=Path, required=True)

    promote_parser = subparsers.add_parser(
        "promote", help="apply the promotion gate and write golden truth"
    )
    promote_parser.add_argument("--reviewed", type=Path, required=True)
    promote_parser.add_argument("--second-pass", type=Path)
    promote_parser.add_argument("--output", type=Path, required=True)
    promote_parser.add_argument("--replace", action="store_true")
    promote_parser.add_argument("--reviewed-at")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "validate-proposals":
            candidates = load_json_object(args.candidates)
            proposals = load_json_object(args.proposals)
            enriched = validate_and_enrich_proposals(candidates, proposals)
            print(f"Validated {enriched['case_count']} proposals")
            return 0

        reviewed = load_json_object(args.reviewed)
        second_pass = (
            load_json_object(args.second_pass)
            if args.second_pass is not None
            else None
        )
        golden = promote_reviewed_annotations(
            reviewed,
            second_pass,
            reviewed_at=args.reviewed_at,
        )
        if args.output.exists() and not args.replace:
            raise ValueError(
                f"output already exists: {args.output}; pass --replace to overwrite"
            )
        atomic_write_json(golden, args.output)
        print(f"Promoted {golden['case_count']} reviewed cases to {args.output}")
        return 0
    except (OSError, TypeError, ValueError) as exc:
        print(f"Email annotation command failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
