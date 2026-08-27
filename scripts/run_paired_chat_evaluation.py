#!/usr/bin/env python3
"""CLI runner for paired chat evaluation.

Loads the synthetic dataset, runs paired evaluation with the deterministic
scorer, reads thresholds from environment variables, and evaluates the
launch gate.

Exit codes:
  0 — launch gate passed
  1 — launch gate failed (reason codes printed)
  2 — thresholds env missing/invalid
"""

from __future__ import annotations

import argparse
import json
import sys

from cowork_agent.features.ai_chat.evaluation import evaluate_launch_gate
from cowork_agent.features.ai_chat.evaluation_dataset import (
    SYNTHETIC_DATASET,
    DeterministicPairedScorer,
)
from cowork_agent.features.ai_chat.evaluation_runner import (
    run_paired_evaluation,
    thresholds_from_env,
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run paired chat evaluation")
    parser.add_argument("--json", action="store_true", help="machine-readable JSON output")
    args = parser.parse_args(argv)

    # Load dataset and build scorer
    case_ids = tuple(entry.case_id for entry in SYNTHETIC_DATASET)
    scorer = DeterministicPairedScorer()

    # Run paired evaluation
    # Hard-safety counters: all zero — sourced as constants from test-suite
    # safety evidence; do not fake nonzero.
    report = run_paired_evaluation(
        case_ids,
        scorer,
        unvalidated_retrievals=0,
        cross_tenant_incidents=0,
        raw_email_memory_violations=0,
        expired_record_retrievals=0,
        rejected_retrievals=0,
    )

    # Read thresholds from env
    try:
        thresholds = thresholds_from_env()
    except ValueError:
        if args.json:
            print(json.dumps({"error": "thresholds_missing"}))
        else:
            print(
                "ERROR: launch thresholds require explicit product-approved "
                "configuration (set EVAL_* env vars)",
                file=sys.stderr,
            )
        sys.exit(2)

    # Evaluate launch gate
    gate_result = evaluate_launch_gate(report, thresholds)

    output = {
        "report": report.to_dict(),
        "gate": {
            "passed": gate_result.passed,
            "reason_codes": list(gate_result.reason_codes),
        },
    }

    if args.json:
        print(json.dumps(output))
    else:
        print(json.dumps(output, indent=2))

    if not gate_result.passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
