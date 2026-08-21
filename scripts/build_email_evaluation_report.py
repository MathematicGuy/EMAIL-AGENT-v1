"""Build a metadata-only Email Intent report from one golden/run pair."""

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
    ReasonCode,
    Route,
)
from cowork_agent.features.email_action_plan.correlation import TaskCandidate
from cowork_agent.features.email_action_plan.evidence import EvidenceStatus
from cowork_agent.features.email_action_plan.routing import resolve_candidate_after_retrieval

try:
    from scripts.email_evaluation_artifacts import (
        dataset_fingerprint,
        load_json_object,
        validate_golden_dataset,
        validate_run_artifact,
    )
except ModuleNotFoundError:
    from email_evaluation_artifacts import (  # type: ignore[no-redef]
        dataset_fingerprint,
        load_json_object,
        validate_golden_dataset,
        validate_run_artifact,
    )

ACTIONABILITY_ORDER = (
    "action_required",
    "action_suggested",
    "informational",
    "irrelevant",
    "unclear",
)
ROUTE_ORDER = ("no_action", "direct_plan", "retrieve_rag")
ACTIONABILITY_MEANINGS = {
    "action_required": "the email explicitly obligates or directly asks the user to act.",
    "action_suggested": "action could benefit the user, but it is optional.",
    "informational": "useful information with no requested or necessary action.",
    "irrelevant": "unrelated, promotional, noisy, or not useful enough to create an action.",
    "unclear": "the intent or required action cannot be determined confidently from the email.",
}


def _distribution(values: Sequence[str], order: Sequence[str]) -> dict[str, int]:
    counts = Counter(values)
    return {label: counts.get(label, 0) for label in order}


def _as_mapping(value: object, location: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{location} must be an object")
    return cast(Mapping[str, object], value)


def _diagnostic_resolution(
    prediction: Mapping[str, object],
    routing: Mapping[str, object],
    evidence_status: object,
) -> tuple[str, str] | None:
    """Resolve prediction + observed evidence through the production resolver."""

    try:
        reason_codes = tuple(
            ReasonCode(str(item)) for item in cast(Sequence[object], routing["reason_codes"])
        )
        document_types = tuple(
            ExpectedDocumentType(str(item))
            for item in cast(Sequence[object], prediction["expected_document_types"])
        )
        decision = EmailRouteDecision(
            actionability=Actionability(str(prediction["actionability"])),
            route=Route(str(routing.get("resolved_route", Route.NO_ACTION.value))),
            candidate_action_item=None,
            email_is_sufficient=bool(prediction["email_is_sufficient"]),
            knowledge_gaps=(),
            retrieval_query=None,
            expected_document_types=document_types,
            reason_codes=reason_codes,
            confidence=float(prediction["confidence"]),
        )
        candidate = TaskCandidate(
            candidate_key="evaluation",
            gmail_thread_id="evaluation",
            incident_key=None,
            source_message_ids=("evaluation",),
            decisions=(("evaluation", decision),),
        )
        evidence = (
            EvidenceStatus.UNSUPPORTED
            if evidence_status is None
            else EvidenceStatus(str(evidence_status))
        )
        resolution = resolve_candidate_after_retrieval(candidate, evidence)
        return resolution.route.value, resolution.mode
    except (TypeError, ValueError):
        return None


def _truth_resolution(
    truth: Mapping[str, object], evidence_status: object
) -> tuple[str, str] | None:
    prediction = {
        "actionability": truth["actionability"],
        "email_is_sufficient": truth["email_is_sufficient"],
        "expected_document_types": truth["expected_document_types"],
        "confidence": 1.0,
    }
    return _diagnostic_resolution(prediction, {"reason_codes": []}, evidence_status)


def compare_run_to_golden(
    golden: Mapping[str, object], run: Mapping[str, object]
) -> dict[str, object]:
    """Compare one validated run to golden truth without retaining case details."""

    validated_golden = validate_golden_dataset(golden)
    validated_run = validate_run_artifact(run)
    expected_fingerprint = dataset_fingerprint(validated_golden)
    if validated_run["dataset_fingerprint"] != expected_fingerprint:
        raise ValueError("run dataset fingerprint does not match the supplied golden dataset")

    golden_cases = cast(Sequence[Mapping[str, object]], validated_golden["cases"])
    run_cases = cast(Sequence[Mapping[str, object]], validated_run["cases"])
    golden_by_id = {str(case["case_id"]): case for case in golden_cases}
    unknown_ids = {
        str(case["case_id"]) for case in run_cases if str(case["case_id"]) not in golden_by_id
    }
    if unknown_ids:
        unknown = ", ".join(sorted(unknown_ids))
        raise ValueError(f"run contains unknown golden case_id(s): {unknown}")

    actionability_correct = 0
    retrieval_correct = 0
    final_route_correct = 0
    mode_correct = 0
    consistency_consistent = 0
    truth_actionability: list[str] = []
    predicted_actionability: list[str] = []
    truth_routes: list[str] = []
    predicted_routes: list[str] = []
    evidence_statuses: list[str] = []
    rewrite_statuses: list[str] = []
    context_required_total = 0
    context_required_supported = 0
    fallback_count = 0

    for run_case in run_cases:
        golden_case = golden_by_id[str(run_case["case_id"])]
        truth = _as_mapping(golden_case["ground_truth"], "golden ground_truth")
        prediction = _as_mapping(run_case["prediction"], "run prediction")
        retrieval = _as_mapping(run_case["retrieval"], "run retrieval")
        routing = _as_mapping(run_case["routing"], "run routing")
        truth_label = str(truth["actionability"])
        predicted_label = str(prediction["actionability"])
        attempted = bool(retrieval["attempted"])
        retrieval_correct += attempted == bool(truth["retrieval_expected"])
        evidence_status = retrieval["evidence_status"]
        expected_resolution = _truth_resolution(truth, evidence_status)
        truth_route = expected_resolution[0] if expected_resolution else "invalid"
        truth_mode = expected_resolution[1] if expected_resolution else "invalid"
        predicted_route = str(routing["resolved_route"])
        truth_actionability.append(truth_label)
        predicted_actionability.append(predicted_label)
        truth_routes.append(truth_route)
        predicted_routes.append(predicted_route)
        actionability_correct += predicted_label == truth_label
        final_route_correct += predicted_route == truth_route
        mode_correct += str(routing["mode"]) == truth_mode
        consistency_consistent += _diagnostic_resolution(prediction, routing, evidence_status) == (
            predicted_route,
            str(routing["mode"]),
        )
        if isinstance(evidence_status, str):
            evidence_statuses.append(evidence_status)
        rewrite_status = retrieval["query_rewrite_status"]
        if isinstance(rewrite_status, str):
            rewrite_statuses.append(rewrite_status)
        if bool(truth["company_context_required"]):
            context_required_total += 1
            context_required_supported += evidence_status == "supported"
        fallback_count += prediction["source_status"] == "classifier_fallback"

    total = len(run_cases)
    model_prediction_count = total - fallback_count
    consistency = {
        "consistent": consistency_consistent,
        "inconsistent": total - consistency_consistent,
        "total": total,
    }
    return {
        "coverage": {
            "golden_cases": len(golden_cases),
            "run_cases": total,
            "matched_cases": total,
            "model_predictions": model_prediction_count,
            "fallback_cases": fallback_count,
        },
        "fallback_cases": {"count": fallback_count, "total": total},
        "actionability_accuracy": {"correct": actionability_correct, "total": total},
        "retrieval_compliance": {"correct": retrieval_correct, "total": total},
        "final_route_accuracy": {"correct": final_route_correct, "total": total},
        "mode_accuracy": {"correct": mode_correct, "total": total},
        "required_context_support": {
            "supported": context_required_supported,
            "total": context_required_total,
        },
        "actionability_distribution": {
            "golden": _distribution(truth_actionability, ACTIONABILITY_ORDER),
            "predicted": _distribution(predicted_actionability, ACTIONABILITY_ORDER),
        },
        "route_distribution": {
            "golden": _distribution(truth_routes, ROUTE_ORDER),
            "predicted": _distribution(predicted_routes, ROUTE_ORDER),
        },
        "evidence_distribution": _distribution(
            evidence_statuses, ("supported", "unsupported", "unavailable")
        ),
        "query_rewrite_distribution": _distribution(
            rewrite_statuses, ("classifier_query", "rewritten", "fallback")
        ),
        "consistency": consistency,
        "shard": dict(_as_mapping(validated_run["shard"], "run shard")),
        "run_id": str(validated_run["run_id"]),
        "created_at": str(validated_run["created_at"]),
        "provider": str(validated_run["provider"]),
        "model": str(validated_run["model"]),
        "prompt_version": str(validated_run["prompt_version"]),
        "pipeline_version": str(validated_run["pipeline_version"]),
        "quality_gate": dict(_as_mapping(validated_run["quality_gate"], "quality gate")),
        "rubric_version": str(validated_run["rubric_version"]),
        "dataset_fingerprint": str(validated_run["dataset_fingerprint"]),
    }


def _percentage(count: int, total: int) -> str:
    return "0.0%" if total == 0 else f"{count / total:.1%}"


def _accuracy_line(label: str, accuracy: Mapping[str, object]) -> str:
    correct = int(accuracy["correct"])
    total = int(accuracy["total"])
    return f"| {label} | {correct}/{total} | {_percentage(correct, total)} |"


def _distribution_rows(distribution: Mapping[str, object], order: Sequence[str]) -> list[str]:
    golden = _as_mapping(distribution["golden"], "golden distribution")
    predicted = _as_mapping(distribution["predicted"], "predicted distribution")
    total = sum(int(golden[label]) for label in order)
    return [
        f"| `{label}` | {int(golden[label])} ({_percentage(int(golden[label]), total)}) "
        f"| {int(predicted[label])} ({_percentage(int(predicted[label]), total)}) |"
        for label in order
    ]


def render_report(metrics: Mapping[str, object]) -> str:
    """Render only allowlisted aggregate metadata and metrics as Markdown."""

    coverage = _as_mapping(metrics["coverage"], "coverage")
    fallback = _as_mapping(metrics["fallback_cases"], "fallback cases")
    actionability_accuracy = _as_mapping(
        metrics["actionability_accuracy"], "actionability accuracy"
    )
    retrieval_compliance = _as_mapping(metrics["retrieval_compliance"], "retrieval compliance")
    final_route_accuracy = _as_mapping(metrics["final_route_accuracy"], "final route accuracy")
    mode_accuracy = _as_mapping(metrics["mode_accuracy"], "mode accuracy")
    required_context = _as_mapping(metrics["required_context_support"], "required context support")
    quality_gate = _as_mapping(metrics["quality_gate"], "quality gate")
    consistency = _as_mapping(metrics["consistency"], "consistency")
    shard = _as_mapping(metrics["shard"], "shard")
    shard_index = int(shard["index"])
    shard_count = int(shard["count"])
    shard_cases = int(shard["case_count"])

    lines = [
        "# Email Pipeline Evaluation Report",
        "",
        "## Run metadata",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Run ID | `{metrics['run_id']}` |",
        f"| Created at | `{metrics['created_at']}` |",
        f"| Provider | `{metrics['provider']}` |",
        f"| Model | `{metrics['model']}` |",
        f"| Prompt version | `{metrics['prompt_version']}` |",
        f"| Pipeline version | `{metrics['pipeline_version']}` |",
        f"| Evidence gate | `{quality_gate['version']}` |",
        f"| Minimum rerank score | `{quality_gate['min_rerank_score']}` |",
        f"| Relative cutoff ratio | `{quality_gate['relative_cutoff_ratio']}` |",
        f"| Rubric version | `{metrics['rubric_version']}` |",
        f"| Dataset fingerprint | `{metrics['dataset_fingerprint']}` |",
        f"| Shard | `{shard_index}/{shard_count}` ({shard_cases} cases) |",
        "",
        "## Coverage",
        "",
        "| Measure | Count | Share of run |",
        "|---|---:|---:|",
        f"| Compared cases | {int(coverage['matched_cases'])}/{int(coverage['golden_cases'])} | "
        f"{_percentage(int(coverage['matched_cases']), int(coverage['golden_cases']))} |",
        f"| Model predictions | {int(coverage['model_predictions'])}/"
        f"{int(coverage['run_cases'])} | "
        f"{_percentage(int(coverage['model_predictions']), int(coverage['run_cases']))} |",
        f"| Classifier fallbacks | {int(fallback['count'])}/{int(fallback['total'])} | "
        f"{_percentage(int(fallback['count']), int(fallback['total']))} |",
        "",
        "## Accuracy",
        "",
        "| Measure | Correct | Accuracy |",
        "|---|---:|---:|",
        _accuracy_line("Actionability", actionability_accuracy),
        _accuracy_line("Retrieve-first compliance", retrieval_compliance),
        _accuracy_line("Final route", final_route_accuracy),
        _accuracy_line("Plan mode", mode_accuracy),
        f"| Required company context supported | {int(required_context['supported'])}/"
        f"{int(required_context['total'])} | "
        f"{_percentage(int(required_context['supported']), int(required_context['total']))} |",
        "",
        "## Actionability distribution",
        "",
        "| Label | Meaning | Golden | Predicted |",
        "|---|---|---:|---:|",
    ]
    actionability_distribution = _as_mapping(
        metrics["actionability_distribution"], "actionability distribution"
    )
    golden_distribution = _as_mapping(
        actionability_distribution["golden"], "golden actionability distribution"
    )
    predicted_distribution = _as_mapping(
        actionability_distribution["predicted"], "predicted actionability distribution"
    )
    total = sum(int(golden_distribution[label]) for label in ACTIONABILITY_ORDER)
    lines.extend(
        f"| `{label}` | {ACTIONABILITY_MEANINGS[label]} | "
        f"{int(golden_distribution[label])} "
        f"({_percentage(int(golden_distribution[label]), total)}) | "
        f"{int(predicted_distribution[label])} "
        f"({_percentage(int(predicted_distribution[label]), total)}) |"
        for label in ACTIONABILITY_ORDER
    )
    route_distribution = _as_mapping(metrics["route_distribution"], "route distribution")
    evidence_distribution = _as_mapping(metrics["evidence_distribution"], "evidence distribution")
    rewrite_distribution = _as_mapping(
        metrics["query_rewrite_distribution"], "query rewrite distribution"
    )
    lines.extend(
        [
            "",
            "## Route distribution",
            "",
            "| Route | Golden | Predicted |",
            "|---|---:|---:|",
            *_distribution_rows(route_distribution, ROUTE_ORDER),
            "",
            "## Evidence gate distribution",
            "",
            "| Evidence status | Count |",
            "|---|---:|",
            *(
                f"| `{status}` | {int(evidence_distribution[status])} |"
                for status in ("supported", "unsupported", "unavailable")
            ),
            "",
            "## Retrieval search source distribution",
            "",
            "| Search source | Count |",
            "|---|---:|",
            *(
                f"| `{label}` | {int(rewrite_distribution[status])} |"
                for status, label in (
                    ("classifier_query", "classifier-supplied"),
                    ("rewritten", "rewritten"),
                    ("fallback", "fallback"),
                )
            ),
            "",
            "## Diagnostic post-retrieval route consistency",
            "",
            "| Consistency | Count |",
            "|---|---:|",
            f"| Consistent | {int(consistency['consistent'])} |",
            f"| Inconsistent | {int(consistency['inconsistent'])} |",
            f"| Total | {int(consistency['total'])} |",
            "",
            "> This report is derived from one compatible golden/run pair at report time. "
            "Per-case comparisons are not persisted.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a metadata-only Email Intent evaluation report."
    )
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        golden = validate_golden_dataset(load_json_object(args.golden))
        run = validate_run_artifact(load_json_object(args.run))
        metrics = compare_run_to_golden(golden, run)
        report = render_report(metrics)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
    except (OSError, ValueError, TypeError) as exc:
        print(f"Email evaluation report failed: {exc}", file=sys.stderr)
        return 2

    print(f"Wrote Email Intent evaluation report to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
