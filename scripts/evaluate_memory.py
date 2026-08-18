#!/usr/bin/env python3
"""Memory evaluation harness CLI. See evaluations/MEMORIES/SPEC.md.

Exit codes:
  0 - the run completed and a report was written
  1 - a seed failure made the run unscorable
  2 - the probe set could not be loaded

Exit code 0 does NOT mean the memory system is good. It means the harness ran.
Verdicts are read by a human; this harness reports, it does not gate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from cowork_agent.features.ai_chat.memory_eval.arms import Arm
from cowork_agent.features.ai_chat.memory_eval.probes import Probe, load_probe_set
from cowork_agent.features.ai_chat.memory_eval.runner import run_probe_set

_DEFAULT_PROBE_SET = Path("evaluations/MEMORIES/probes/v1-four-scopes.json")
_DEFAULT_OUTPUT_DIR = Path("evaluations/MEMORIES/baselines")


def _scripted_ask(probe: Probe, arm: Arm, masked: object) -> tuple[str, int]:
    """A deterministic stand-in reply, for --dry-run only.

    It answers correctly under FULL and declines otherwise, which exercises the
    scoring, verdict and report paths without a model. It measures NOTHING
    about the real system and must never be used to make a decision.
    """

    del masked
    if arm is Arm.FULL:
        if probe.expect_refusal:
            return ("I don't have that information.", 0)
        return (" ".join(probe.expect_any or probe.expect_all), 0)
    return ("I don't have that information.", 0)


async def _dry_run(probe_set: object) -> dict[str, object]:
    async def ask(probe: Probe, arm: Arm, masked: object) -> tuple[str, int]:
        return _scripted_ask(probe, arm, masked)

    return await run_probe_set(
        probe_set,  # type: ignore[arg-type]
        ask,
        provider="dry-run",
        model="scripted",
        ran_at=datetime.now(UTC),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-set", type=Path, default=_DEFAULT_PROBE_SET)
    parser.add_argument("--output", type=Path, help="Report path; defaults under baselines/")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scripted replies. Validates harness mechanics only - never a result.",
    )
    args = parser.parse_args(argv)

    try:
        payload = json.loads(args.probe_set.read_text(encoding="utf-8"))
        probe_set = load_probe_set(payload)
    except (OSError, ValueError) as error:
        # ProbeSetError subclasses ValueError, so this catches both a missing
        # file and an unloadable probe set. Listing it separately would be a
        # redundant handler (ruff B014).
        print(f"ERROR: cannot load probe set: {error}", file=sys.stderr)
        return 2

    if not args.dry_run:
        print(
            "ERROR: the live tier is not implemented yet; run with --dry-run",
            file=sys.stderr,
        )
        return 2

    report = asyncio.run(_dry_run(probe_set))

    output = args.output
    if output is None:
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
        output = _DEFAULT_OUTPUT_DIR / f"{stamp}-{probe_set.probe_set_id}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
