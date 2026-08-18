"""Evaluate the current production Email Intent Router on Gmail candidates."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from cowork_agent.domain.target_contracts import BodyFormat, EphemeralEmailEnvelope, FetchStatus
from cowork_agent.features.email_action_plan.routing import resolve_route

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = REPO_ROOT / "evaluations" / "EMAIL" / "gmail_candidates.json"
DEFAULT_REPORT = REPO_ROOT / "evaluations" / "EMAIL" / "EMAIL-EVALUATION_REPORT.md"
DEFAULT_GOLDEN_DATASET = REPO_ROOT / "evaluations" / "EMAIL" / "golden_dataset.json"
MAX_CASES = 50
ROUTES = ("NO_ACTION", "DIRECT_PLAN", "RETRIEVE_RAG")
ACTIONABILITY_EXPLANATIONS = {
    "action_required": "The email explicitly requires the user to do something.",
    "action_suggested": "An action may be useful, but it is optional rather than required.",
    "informational": "The email provides information and does not request an action.",
    "irrelevant": "The email is not relevant enough to create an action or plan.",
    "unclear": "The email's intent or required action cannot be determined confidently.",
}


def load_envelopes(path: Path) -> list[EphemeralEmailEnvelope]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("email dataset must be a JSON array")
    return load_envelopes_from_records(raw)


def load_envelopes_from_records(
    records: Sequence[Mapping[str, object]],
) -> list[EphemeralEmailEnvelope]:
    envelopes = []
    for record in records:
        sender_name, sender_email = parseaddr(str(record["sender"]))
        envelopes.append(
            EphemeralEmailEnvelope(
                run_id="email-evaluation",
                user_id="",
                gmail_message_id=str(record["gmail_message_id"]),
                gmail_thread_id=str(record.get("gmail_thread_id", record["gmail_message_id"])),
                gmail_url="",
                sender_name=sender_name,
                sender_email=sender_email or str(record["sender"]),
                recipients=(),
                subject=str(record["subject"]),
                received_at=datetime.fromisoformat(str(record["received_at"])),
                labels=tuple(str(label) for label in record.get("labels", ())),
                normalized_body=str(record.get("snippet", "")),
                body_format=BodyFormat.TEXT,
                attachments_present=False,
                fetch_status=FetchStatus.COMPLETE,
            )
        )
    return envelopes


def _decision_dict(decision: Any) -> dict[str, object]:
    return {
        "actionability": decision.actionability.value,
        "candidate_action_item": decision.candidate_action_item,
        "email_is_sufficient": decision.email_is_sufficient,
        "knowledge_gaps": list(decision.knowledge_gaps),
        "retrieval_query": decision.retrieval_query,
        "expected_document_types": [item.value for item in decision.expected_document_types],
        "confidence": decision.confidence,
        "resolved_route": resolve_route(decision).route.value,
        "reason_codes": [code.value for code in decision.reason_codes],
    }


async def evaluate(
    messages: Sequence[EphemeralEmailEnvelope], classifier: Any, current_time: datetime
) -> dict[str, object]:
    classify = classifier.classify
    unwrapped = getattr(classify, "__wrapped__", None)
    if unwrapped is not None:
        # Raw email must not be exported from this batch evaluation.
        for method_name in ("_generate", "_complete"):
            method = getattr(classifier, method_name, None)
            inner = getattr(method, "__wrapped__", None)
            if inner is not None:
                setattr(classifier, method_name, inner.__get__(classifier, type(classifier)))
        result = await unwrapped(classifier, "UTC", current_time, messages)
    else:
        result = await classify("UTC", current_time, messages)

    decisions = {item.gmail_message_id: item.decision for item in result.decisions}
    fallback_ids = {item.gmail_message_id for item in result.decisions if item.is_fallback}
    route_counts = Counter({route: 0 for route in ROUTES})
    actionability_counts: Counter[str] = Counter()
    confidences: list[float] = []
    missing_ids: list[str] = []
    results: list[dict[str, object]] = []
    for message in messages:
        decision = decisions.get(message.gmail_message_id)
        if decision is None:
            missing_ids.append(message.gmail_message_id)
            results.append({"gmail_message_id": message.gmail_message_id, "prediction": None})
            continue
        if message.gmail_message_id in fallback_ids:
            missing_ids.append(message.gmail_message_id)
        prediction = _decision_dict(decision)
        prediction["source_status"] = (
            "classifier_fallback"
            if message.gmail_message_id in fallback_ids
            else "model_prediction"
        )
        route_counts[str(prediction["resolved_route"]).upper()] += 1
        actionability_counts[decision.actionability.value] += 1
        confidences.append(decision.confidence)
        results.append({"gmail_message_id": message.gmail_message_id, "prediction": prediction})
    return {
        "case_count": len(messages),
        "classified_count": len(decisions),
        "missing_count": len(missing_ids),
        "missing_ids": missing_ids,
        "batch_count": result.batch_count,
        "fallback_count": len(fallback_ids),
        "fallback_ids": sorted(fallback_ids),
        "route_counts": dict(route_counts),
        "actionability_counts": dict(sorted(actionability_counts.items())),
        "confidence": {
            "mean": round(sum(confidences) / len(confidences), 4) if confidences else None,
            "minimum": min(confidences) if confidences else None,
            "maximum": max(confidences) if confidences else None,
        },
        "filtered_summary": result.filtered_summary,
        "results": results,
    }


def _share(count: int, total: int) -> str:
    return f"{count / total:.1%}" if total else "0.0%"


def _metric_text(value: Sequence[object]) -> str:
    numerator, denominator = int(value[0]), int(value[1])
    return (
        f"{numerator}/{denominator} ({numerator / denominator:.1%})"
        if denominator
        else "not available"
    )


def render_report(
    summary: Mapping[str, object],
    reviewed_metrics: Mapping[str, Sequence[object]] | None = None,
    *,
    dataset_name: str,
    golden_dataset_name: str = "golden_dataset.json",
    run_date: str,
    provider: str = "unknown",
    model: str = "unknown",
) -> str:
    total = int(summary["case_count"])
    routes, actions, confidence = (
        summary["route_counts"],
        summary["actionability_counts"],
        summary["confidence"],
    )
    assert (
        isinstance(routes, Mapping)
        and isinstance(actions, Mapping)
        and isinstance(confidence, Mapping)
    )
    metrics = reviewed_metrics or {
        "reviewed_route_accuracy": (0, 0),
        "reviewed_actionability_accuracy": (0, 0),
    }
    route_rows = "\n".join(
        f"| {route} | {int(routes.get(route, 0))} | {_share(int(routes.get(route, 0)), total)} |"
        for route in ROUTES
    )
    action_rows = "\n".join(
        f"| `{name}` | {ACTIONABILITY_EXPLANATIONS.get(name, 'No description available.')} | "
        f"{int(count)} | {_share(int(count), total)} |"
        for name, count in actions.items()
    )
    fallback_count = int(summary.get("fallback_count", 0))
    confidence_text = f"{confidence['mean']} / {confidence['minimum']} / {confidence['maximum']}"
    fallback_note = (
        f"⚠️ **{fallback_count}/{total} decisions used classifier fallback.** "
        "Those cases are persisted for audit but are not model-quality evidence."
        if fallback_count
        else "No classifier fallbacks were used."
    )
    return f"""# Email Intent Routing Evaluation Report

> **Evaluation target**: Current production Email Intent Router
> **Dataset**: {total} Gmail candidate messages from `{dataset_name}`
> **Golden artifact**: `{golden_dataset_name}`
> **Provider/model**: `{provider}` / `{model}`
> **Run date**: {run_date}

## Executive Summary

The current production classifier prompt was evaluated once over {total} messages.
Predictions are compared only with human-reviewed ground truth.

### Coverage and accuracy

- Predictions persisted: **{int(summary["classified_count"])}/{total}**
- Model-missing/fallback IDs: **{int(summary["missing_count"])}**
- Classifier fallbacks: **{fallback_count}/{total}**
- Reviewed route accuracy: **{_metric_text(metrics["reviewed_route_accuracy"])}**
- Reviewed actionability accuracy: **{_metric_text(metrics["reviewed_actionability_accuracy"])}**
- Classifier batch calls: **{int(summary["batch_count"])}**
- Confidence mean/min/max: **{confidence_text}**

{fallback_note}

### Resolved route distribution

| Route | Count | Share |
|---|---:|---:|
{route_rows}

### Actionability distribution

| Actionability | Meaning | Count | Share |
|---|---|---:|---:|
{action_rows}

## Methodology and limitations

1. Each Gmail snippet was converted into the project's ephemeral email contract.
2. The configured live classifier used the current production prompt.
3. The deterministic Route Resolver computed the final route.
4. Accuracy includes only records with human-reviewed ground truth.
5. Evaluation artifacts exclude raw email snippets and telemetry export is disabled.

## Reproduce

```powershell
uv run python scripts/evaluate_email_golden.py --limit {total}
```

Artifacts: `{dataset_name}`, `{golden_dataset_name}`, and this report.
"""


def write_report(report: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")


def build_live_classifier() -> tuple[Any, str, str]:
    from cowork_agent.config import load_runtime_environment

    load_runtime_environment()
    os.environ["LANGFUSE_TRACING_ENABLED"] = "false"
    provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    if provider == "gemini":
        from cowork_agent.config import GeminiSettings
        from cowork_agent.integrations.llm.providers.gemini import GeminiRouteClassifier

        settings = GeminiSettings.from_env()
        return (
            GeminiRouteClassifier(settings, include_filtered_summary=False),
            provider,
            settings.model,
        )
    if provider == "groq":
        from cowork_agent.config import GroqSettings
        from cowork_agent.integrations.llm.providers.groq import GroqRouteClassifier

        settings = GroqSettings.from_env()
        return GroqRouteClassifier(settings), provider, settings.model
    if provider == "faucet":
        from cowork_agent.config import FaucetSettings
        from cowork_agent.integrations.llm.providers.faucet import FaucetRouteClassifier

        settings = FaucetSettings.from_env()
        return FaucetRouteClassifier(settings), provider, settings.model
    if provider == "openrouter":
        from cowork_agent.config import OpenRouterSettings
        from cowork_agent.integrations.llm.providers.openrouter import OpenRouterRouteClassifier

        settings = OpenRouterSettings.from_env()
        return OpenRouterRouteClassifier(settings), provider, settings.model
    raise ValueError(f"unsupported LLM_PROVIDER for email evaluation: {provider}")


def _compare_predictions(
    prediction: Mapping[str, object] | None, reference: Mapping[str, object] | None
) -> dict[str, object]:
    if prediction is None or reference is None:
        return {"route_match": None, "actionability_match": None, "status": "awaiting_reference"}
    return {
        "route_match": prediction.get("resolved_route") == reference.get("expected_route"),
        "actionability_match": prediction.get("actionability") == reference.get("actionability"),
        "status": "computed",
    }


def _summary_results(summary: Mapping[str, object]) -> dict[str, Mapping[str, object] | None]:
    raw = summary.get("results", ())
    if not isinstance(raw, Sequence):
        return {}
    return {
        str(item["gmail_message_id"]): (
            item.get("prediction") if isinstance(item.get("prediction"), Mapping) else None
        )
        for item in raw
        if isinstance(item, Mapping) and "gmail_message_id" in item
    }


def merge_golden_dataset(
    existing: Sequence[Mapping[str, object]],
    candidates: Sequence[Mapping[str, object]],
    summary: Mapping[str, object],
    *,
    provider: str,
    model: str,
    run_at: str,
) -> list[dict[str, object]]:
    results = _summary_results(summary)
    candidates_by_id = {str(record["gmail_message_id"]): record for record in candidates}
    ordered_ids = [str(record["gmail_message_id"]) for record in existing]
    for message_id in candidates_by_id:
        if message_id not in ordered_ids:
            ordered_ids.append(message_id)
    existing_by_id = {str(record["gmail_message_id"]): dict(record) for record in existing}
    merged = []
    for message_id in ordered_ids:
        record = existing_by_id.get(message_id)
        if record is None:
            candidate = candidates_by_id.get(message_id)
            if candidate is None:
                raise ValueError(f"existing golden record has no candidate metadata: {message_id}")
            record = dict(candidate)
        candidate = candidates_by_id.get(message_id)
        if candidate is not None:
            for key in ("gmail_thread_id", "sender", "subject", "received_at", "labels"):
                if key in candidate:
                    record[key] = candidate[key]
        record.pop("snippet", None)
        record.pop("ground_truth_proposal", None)
        record.pop("proposal_comparison", None)
        record.setdefault("ground_truth", None)
        record["ground_truth_status"] = (
            "reviewed" if isinstance(record.get("ground_truth"), Mapping) else "unreviewed"
        )
        if message_id in results:
            prediction = results[message_id]
            record["llm_prediction"] = prediction
            truth = record.get("ground_truth")
            record["eval_result"] = _compare_predictions(
                prediction if isinstance(prediction, Mapping) else None,
                truth if isinstance(truth, Mapping) else None,
            )
            record["latest_evaluation"] = {
                "provider": provider,
                "model": model,
                "run_at": run_at,
                "prompt_version": "current",
            }
        merged.append(record)
    return merged


def _reviewed_metrics(
    merged: Sequence[Mapping[str, object]], candidate_ids: set[str]
) -> dict[str, Sequence[object]]:
    results = [
        record["eval_result"]
        for record in merged
        if str(record.get("gmail_message_id")) in candidate_ids
        and isinstance(record.get("eval_result"), Mapping)
        and record["eval_result"].get("status") == "computed"
    ]
    return {
        "reviewed_route_accuracy": (
            sum(item.get("route_match") is True for item in results),
            len(results),
        ),
        "reviewed_actionability_accuracy": (
            sum(item.get("actionability_match") is True for item in results),
            len(results),
        ),
    }


def write_json(value: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the current Email Intent Router.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--golden-dataset", type=Path, default=DEFAULT_GOLDEN_DATASET)
    parser.add_argument("--limit", type=int, default=MAX_CASES)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= MAX_CASES:
        parser.error(f"--limit must be between 1 and {MAX_CASES}")
    try:
        records = json.loads(args.dataset.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            raise ValueError("email dataset must be a JSON array")
        if len(records) < args.limit:
            raise ValueError(f"dataset contains {len(records)} messages; {args.limit} required")
        selected = records[: args.limit]
        run_at = datetime.now(UTC)
        classifier, provider, model = build_live_classifier()
        summary = asyncio.run(evaluate(load_envelopes_from_records(selected), classifier, run_at))
        existing: list[Mapping[str, object]] = []
        if args.golden_dataset.exists():
            value = json.loads(args.golden_dataset.read_text(encoding="utf-8"))
            if not isinstance(value, list):
                raise ValueError("golden dataset must be a JSON array")
            existing = value
        merged = merge_golden_dataset(
            existing, selected, summary, provider=provider, model=model, run_at=run_at.isoformat()
        )
        write_json(merged, args.golden_dataset)
        ids = {str(record["gmail_message_id"]) for record in selected}
        report = render_report(
            summary,
            _reviewed_metrics(merged, ids),
            dataset_name=args.dataset.name,
            golden_dataset_name=args.golden_dataset.name,
            run_date=run_at.date().isoformat(),
            provider=provider,
            model=model,
        )
        write_report(report, args.report)
    except (OSError, ValueError) as exc:
        print(f"Email evaluation failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"Evaluated {args.limit} messages with the current prompt; "
        f"golden_dataset={args.golden_dataset}; report={args.report}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
