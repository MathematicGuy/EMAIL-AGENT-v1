"""Capture the combined-extractor baseline over the labeled routing fixtures.

Phase 0 (P0-C): pins today's combined classify+plan extraction quality,
latency, and call count so the split-call migration (V1-M2) has a regression
gate. Live-provider-only: skips gracefully when API keys are missing. The
report stores agreement statistics only — never email bodies or subjects.

Regenerate: python scripts/capture_baseline.py            (live provider)
Smoke test: python scripts/capture_baseline.py --dry-run  (deterministic fake)
"""

import argparse
import asyncio
import importlib.util
import json
import os
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LOADER_PATH = REPO_ROOT / "tests" / "fixtures" / "routing" / "loader.py"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "baselines"

# Current combined extractor classifications → PRD-v1 actionability labels.
ACTIONABILITY_BY_CLASSIFICATION = {
    "actionable": {"action_required", "action_suggested", "unclear"},
    "informational": {"informational"},
    "newsletter": {"irrelevant"},
    "automated_no_action": {"irrelevant"},
}


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    expected_actionability: str
    predicted_classification: str
    agreement: bool
    latency_ms: int


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
            tenant_id="",
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


def build_live_extractor():
    """Return the configured combined extractor, or None when keys are missing."""
    provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    from cowork_agent.config import GeminiSettings, GroqSettings

    try:
        if provider == "groq":
            from cowork_agent.integrations.llm.providers.groq import GroqActionExtractor

            return GroqActionExtractor(GroqSettings.from_env()), provider
        from cowork_agent.integrations.llm.providers.gemini import GeminiActionExtractor

        return GeminiActionExtractor(GeminiSettings.from_env()), provider
    except ValueError as exc:
        print(f"Skipping baseline capture: {provider} is not configured ({exc}).")
        print("Set the provider API key(s) in .env and rerun to capture a live baseline.")
        return None


class DryRunExtractor:
    """Deterministic fake deriving classifications from the labels themselves."""

    def __init__(self, classification_by_case_id: dict[str, str]) -> None:
        self._classification = classification_by_case_id
        self.call_count = 0

    async def extract(self, user_timezone, current_time, messages):
        from cowork_agent.features.email_action_plan.schemas import (
            EmailExtraction,
            ExtractionBatch,
        )

        self.call_count += 1
        return ExtractionBatch(
            tuple(
                EmailExtraction(
                    message.gmail_message_id,
                    self._classification[message.gmail_message_id],
                    "dry-run",
                    (),
                )
                for message in messages
            )
        )


def build_dry_run_extractor(cases):
    classification = {}
    for case in cases:
        if case.labels.actionability.value in {"action_required", "action_suggested", "unclear"}:
            classification[case.id] = "actionable"
        elif case.labels.actionability.value == "informational":
            classification[case.id] = "informational"
        else:
            classification[case.id] = "newsletter"
    return DryRunExtractor(classification), "dry-run-fake"


async def capture(cases, extractor) -> tuple[list[CaseResult], int]:
    envelopes = build_envelopes(cases)
    results: list[CaseResult] = []
    call_count = 0
    for case in cases:
        started = time.perf_counter()
        batch = await extractor.extract("UTC", datetime.now(UTC), [envelopes[case.id]])
        latency_ms = int((time.perf_counter() - started) * 1000)
        call_count += 1
        classification = batch.emails[0].classification if batch.emails else "missing"
        expected = case.labels.actionability.value
        agreement = expected in ACTIONABILITY_BY_CLASSIFICATION.get(classification, set())
        results.append(CaseResult(case.id, expected, classification, agreement, latency_ms))
    return results, call_count


def write_report(results: Sequence[CaseResult], provider: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    agreements = sum(1 for result in results if result.agreement)
    report = {
        "baseline": "combined-extractor",
        "provider": provider,
        "captured_at": datetime.now(UTC).isoformat(),
        "summary": {
            "case_count": len(results),
            "agreement_count": agreements,
            "agreement_rate": round(agreements / len(results), 4) if results else None,
            "call_count": len(results),
            "total_latency_ms": sum(result.latency_ms for result in results),
        },
        "cases": [
            {
                "id": result.case_id,
                "expected_actionability": result.expected_actionability,
                "predicted_classification": result.predicted_classification,
                "agreement": result.agreement,
                "latency_ms": result.latency_ms,
            }
            for result in results
        ],
    }
    target = output_dir / f"combined-extractor-baseline-{datetime.now(UTC):%Y-%m-%d}.json"
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return target


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture the combined-extractor baseline over routing fixtures."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use a deterministic fake extractor instead of the live LLM provider.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for the baseline report (default: {DEFAULT_OUTPUT_DIR}).",
    )
    args = parser.parse_args(argv)

    cases = load_routing_cases()
    if args.dry_run:
        extractor, provider = build_dry_run_extractor(cases)
    else:
        built = build_live_extractor()
        if built is None:
            return 0
        extractor, provider = built

    results, call_count = asyncio.run(capture(cases, extractor))
    target = write_report(results, provider, args.output_dir)
    summary = (
        f"Captured baseline for {len(results)} cases with {call_count} extractor call(s): "
        f"{sum(1 for result in results if result.agreement)} agreements. Report: {target}"
    )
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
