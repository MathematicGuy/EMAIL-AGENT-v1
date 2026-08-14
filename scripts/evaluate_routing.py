"""Evaluate routing quality over the labeled routing fixtures.

V1-M2 exit obligation (PRD-v1 §16 Milestone 2, task T2.6): run after EVERY
classifier prompt or provider change. Measures actionability agreement,
per-Route precision/recall, and — separately, because PRD-v1 §14 flags it as
the highest-risk error — the false-negative-retrieval rate: cases labeled
RETRIEVE_RAG (the email needs company knowledge) whose resolved Route is not
RETRIEVE_RAG. The predicted route is the Route Resolver's output for each
classifier Route Decision, resolved via `resolve_route`. Live-provider-only:
skips gracefully when API keys are missing. The report stores case ids and
statistics only — never email bodies or subjects.

Regenerate: python scripts/evaluate_routing.py            (live provider)
Smoke test: python scripts/evaluate_routing.py --dry-run  (deterministic fake)
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cowork_agent.domain.target_contracts import EmailRouteDecision

REPO_ROOT = Path(__file__).resolve().parents[1]
LOADER_PATH = REPO_ROOT / "tests" / "fixtures" / "routing" / "loader.py"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "evaluations" / "baselines"

#: Route vocabulary shared with the fixture ExpectedRoute labels.
ROUTE_NAMES: tuple[str, ...] = ("NO_ACTION", "DIRECT_PLAN", "RETRIEVE_RAG")
RETRIEVE_RAG = "RETRIEVE_RAG"


@dataclass(frozen=True)
class EvalCaseResult:
    """Per-case evaluation outcome; identifiers and labels only, never text."""

    case_id: str
    expected_actionability: str
    predicted_actionability: str
    expected_route: str
    predicted_route: str


@dataclass(frozen=True)
class EvalMetrics:
    """Aggregate routing metrics over one evaluation run."""

    agreement_count: int
    actionability_accuracy: float | None
    confusion: Mapping[str, Mapping[str, int]]
    precision: Mapping[str, float | None]
    recall: Mapping[str, float | None]
    false_negative_count: int
    false_negative_rate: float | None
    false_negative_case_ids: tuple[str, ...]


def load_routing_cases():
    spec = importlib.util.spec_from_file_location("routing_fixture_loader", LOADER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load routing fixture loader from {LOADER_PATH}")
    loader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loader)
    return loader.load_routing_labels()


def build_envelopes(cases):
    from cowork_agent.domain.target_contracts import (
        BodyFormat,
        EphemeralEmailEnvelope,
        FetchStatus,
    )

    now = datetime.now(UTC)
    envelopes = {}
    for case in cases:
        envelopes[case.id] = EphemeralEmailEnvelope(
            run_id="",
            user_id="",
            gmail_message_id=case.id,
            gmail_thread_id=case.thread_id or case.id,
            gmail_url="",
            sender_name="",
            sender_email=case.sender,
            recipients=(),
            subject=case.subject,
            received_at=now,
            labels=(),
            normalized_body=case.body,
            body_format=BodyFormat.TEXT,
            attachments_present=False,
            fetch_status=FetchStatus.COMPLETE,
        )
    return envelopes


def build_live_classifier():
    """Return the configured Route Classifier, or None when keys are missing."""
    provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    from cowork_agent.config import GeminiSettings, GroqSettings

    try:
        if provider == "groq":
            from cowork_agent.integrations.llm.providers.groq import GroqRouteClassifier

            settings = GroqSettings.from_env()
            return GroqRouteClassifier(settings), provider, settings.model
        from cowork_agent.integrations.llm.providers.gemini import GeminiRouteClassifier

        settings = GeminiSettings.from_env()
        return GeminiRouteClassifier(settings), provider, settings.model
    except ValueError as exc:
        print(f"Skipping routing evaluation: {provider} is not configured ({exc}).")
        print("Set the provider API key(s) in .env and rerun to evaluate a live classifier.")
        return None


class DryRunClassifier:
    """Deterministic fake deriving Route Decisions from the fixture labels.

    Validates evaluation plumbing only: decisions are synthesized from the
    labels themselves, so a dry run scores perfect by construction and says
    nothing about real model quality.
    """

    def __init__(self, decision_by_case_id: Mapping[str, EmailRouteDecision]) -> None:
        self._decisions = decision_by_case_id
        self.call_count = 0

    async def classify(self, user_timezone, current_time, messages):
        from cowork_agent.features.email_action_plan.schemas import (
            ClassificationResult,
            ClassifiedMessage,
        )

        self.call_count += 1
        return ClassificationResult(
            tuple(
                ClassifiedMessage(
                    message.gmail_message_id, self._decisions[message.gmail_message_id]
                )
                for message in messages
            ),
            1,
        )


def build_dry_run_classifier(cases):
    """Build the deterministic fake classifier from the fixture labels.

    Each synthesized Route Decision replays the case labels (actionability,
    sufficiency, reason codes) through the Route Resolver, so the resolved
    Route matches the labeled expected Route whenever the fixture labels are
    consistent with the resolver ladder.
    """
    from cowork_agent.domain.target_contracts import (
        Actionability,
        EmailRouteDecision,
        ReasonCode,
        Route,
    )

    decisions = {}
    for case in cases:
        labels = case.labels
        gaps = ("dry-run knowledge gap",) if labels.expected_route.value == RETRIEVE_RAG else ()
        decisions[case.id] = EmailRouteDecision(
            actionability=Actionability(labels.actionability.value),
            route=Route(labels.expected_route.value.lower()),
            candidate_action_item=None,
            email_is_sufficient=labels.email_is_sufficient,
            knowledge_gaps=gaps,
            retrieval_query=None,
            expected_document_types=(),
            reason_codes=tuple(ReasonCode(code.value) for code in labels.reason_codes),
            confidence=1.0,
        )
    return DryRunClassifier(decisions)


async def evaluate(cases, classifier) -> tuple[list[EvalCaseResult], int]:
    """Classify all fixture envelopes and compare resolved Routes to labels."""
    from cowork_agent.features.email_action_plan.routing import resolve_route

    envelopes = build_envelopes(cases)
    result = await classifier.classify(
        "UTC", datetime.now(UTC), [envelopes[case.id] for case in cases]
    )
    decision_by_id = {
        classified.gmail_message_id: classified.decision for classified in result.decisions
    }
    per_case: list[EvalCaseResult] = []
    for case in cases:
        decision = decision_by_id.get(case.id)
        if decision is None:
            per_case.append(
                EvalCaseResult(
                    case_id=case.id,
                    expected_actionability=case.labels.actionability.value,
                    predicted_actionability="missing",
                    expected_route=case.labels.expected_route.value,
                    predicted_route="MISSING",
                )
            )
            continue
        per_case.append(
            EvalCaseResult(
                case_id=case.id,
                expected_actionability=case.labels.actionability.value,
                predicted_actionability=decision.actionability.value,
                expected_route=case.labels.expected_route.value,
                predicted_route=resolve_route(decision).route.name,
            )
        )
    return per_case, result.batch_count


def actionability_agreement(results: Sequence[EvalCaseResult]) -> tuple[int, float | None]:
    """Return (agreement count, overall accuracy); accuracy None when empty."""
    if not results:
        return 0, None
    agreed = sum(
        1 for result in results if result.expected_actionability == result.predicted_actionability
    )
    return agreed, round(agreed / len(results), 4)


def route_confusion(results: Sequence[EvalCaseResult]) -> dict[str, dict[str, int]]:
    """Expected × predicted counts over the three Routes (rows = expected)."""
    counts = {expected: {predicted: 0 for predicted in ROUTE_NAMES} for expected in ROUTE_NAMES}
    for result in results:
        row = counts.get(result.expected_route)
        if row is not None and result.predicted_route in row:
            row[result.predicted_route] += 1
    return counts


def route_precision_recall(
    confusion: Mapping[str, Mapping[str, int]],
) -> tuple[dict[str, float | None], dict[str, float | None]]:
    """Per-Route precision/recall from a confusion matrix; None on zero totals."""
    precision: dict[str, float | None] = {}
    recall: dict[str, float | None] = {}
    for route in ROUTE_NAMES:
        predicted_total = sum(confusion[expected][route] for expected in ROUTE_NAMES)
        expected_total = sum(confusion[route].values())
        correct = confusion[route][route]
        precision[route] = round(correct / predicted_total, 4) if predicted_total else None
        recall[route] = round(correct / expected_total, 4) if expected_total else None
    return precision, recall


def false_negative_retrieval(
    results: Sequence[EvalCaseResult],
) -> tuple[int, float | None, tuple[str, ...]]:
    """PRD-v1 §14 highest-risk error: labeled RETRIEVE_RAG but not routed there.

    Returns (missed count, missed rate over RETRIEVE_RAG-labeled cases — None
    when no case is labeled RETRIEVE_RAG —, missed case ids in fixture order).
    """
    expected = tuple(result for result in results if result.expected_route == RETRIEVE_RAG)
    missed = tuple(result.case_id for result in expected if result.predicted_route != RETRIEVE_RAG)
    rate = round(len(missed) / len(expected), 4) if expected else None
    return len(missed), rate, missed


def compute_metrics(results: Sequence[EvalCaseResult]) -> EvalMetrics:
    agreement_count, accuracy = actionability_agreement(results)
    confusion = route_confusion(results)
    precision, recall = route_precision_recall(confusion)
    fn_count, fn_rate, fn_case_ids = false_negative_retrieval(results)
    return EvalMetrics(
        agreement_count=agreement_count,
        actionability_accuracy=accuracy,
        confusion=confusion,
        precision=precision,
        recall=recall,
        false_negative_count=fn_count,
        false_negative_rate=fn_rate,
        false_negative_case_ids=fn_case_ids,
    )


def build_report(
    results: Sequence[EvalCaseResult],
    metrics: EvalMetrics,
    provider: str,
    model: str,
) -> dict[str, object]:
    return {
        "date": datetime.now(UTC).date().isoformat(),
        "provider": provider,
        "model": model,
        "case_count": len(results),
        "actionability_accuracy": {
            "agreement_count": metrics.agreement_count,
            "accuracy": metrics.actionability_accuracy,
        },
        "confusion": metrics.confusion,
        "precision": metrics.precision,
        "recall": metrics.recall,
        "false_negative_retrieval": {
            "count": metrics.false_negative_count,
            "rate": metrics.false_negative_rate,
            "case_ids": list(metrics.false_negative_case_ids),
        },
        "per_case": [
            {
                "case_id": result.case_id,
                "expected_route": result.expected_route,
                "predicted_route": result.predicted_route,
                "expected_actionability": result.expected_actionability,
                "predicted_actionability": result.predicted_actionability,
            }
            for result in results
        ],
    }


def write_report(report: Mapping[str, object], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"routing-eval-{report['date']}.json"
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return target


def print_summary(
    metrics: EvalMetrics,
    case_count: int,
    provider: str,
    model: str,
    batch_count: int,
    target: Path,
) -> None:
    print(
        f"Evaluated routing for {case_count} cases with {batch_count} classifier batch call(s): "
        f"provider={provider}, model={model}."
    )
    print(
        f"Actionability agreement: {metrics.agreement_count}/{case_count} "
        f"(accuracy {metrics.actionability_accuracy})."
    )
    for route in ROUTE_NAMES:
        print(f"  {route}: precision={metrics.precision[route]} recall={metrics.recall[route]}")
    missed = ", ".join(metrics.false_negative_case_ids) or "none"
    print(
        f"False-negative retrieval (PRD-v1 Sec. 14): {metrics.false_negative_count} missed "
        f"(rate {metrics.false_negative_rate}); cases: {missed}."
    )
    print(f"Report: {target}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate classifier routing over the labeled routing fixtures."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use a deterministic fake classifier instead of the live LLM provider.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for the evaluation report (default: {DEFAULT_OUTPUT_DIR}).",
    )
    args = parser.parse_args(argv)

    cases = load_routing_cases()
    if args.dry_run:
        classifier = build_dry_run_classifier(cases)
        provider, model = "dry-run-fake", "dry-run-fake"
    else:
        built = build_live_classifier()
        if built is None:
            return 0
        classifier, provider, model = built

    results, batch_count = asyncio.run(evaluate(cases, classifier))
    metrics = compute_metrics(results)
    report = build_report(results, metrics, provider, model)
    target = write_report(report, args.output_dir)
    print_summary(metrics, len(results), provider, model, batch_count, target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
