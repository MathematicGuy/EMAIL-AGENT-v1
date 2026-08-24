#!/usr/bin/env python3
"""Turn a finished memory-evaluation run into triage issues a coding agent can work.

The report says what happened. This says, per failing probe, WHERE to look and
hands over the evidence needed to answer WHY: the probe's own expectation, the
state that was seeded, and what all three arms actually replied.

Only probes whose cause is still open get an issue — `prompt_fault` (memory
delivered, generation misused it) and `not_attributable`. A `memory_fault` is
already attributed and needs retrieval work rather than a reading; a
`run_failed` probe is named in the index but gets no issue, because a dropout
is repeated, not diagnosed.

Output lands under `evaluations/MEMORIES/runs/`, which is gitignored: an issue
carries reply and seed text, and RUNBOOK rule 5 keeps that out of commits.

    python scripts/triage_memory_evaluation.py
    python scripts/triage_memory_evaluation.py --baseline <path> --detail <path>
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from cowork_agent.features.ai_chat.memory_eval.fault import TRIAGE_WORTHY, FaultClass, classify
from cowork_agent.features.ai_chat.memory_eval.probes import (
    Probe,
    ProbeSet,
    ProbeSetError,
    find_probe_set_file,
    load_probe_set,
)
from cowork_agent.features.ai_chat.memory_eval.scoring import Outcome
from cowork_agent.features.ai_chat.memory_eval.verdicts import Verdict

_BASELINES_DIR = Path("evaluations/MEMORIES/baselines")
_RUNS_DIR = Path("evaluations/MEMORIES/runs")
_PROBES_DIR = Path("evaluations/MEMORIES/probes")
_ARMS = ("full", "ablated", "control")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _latest_baseline(baselines_dir: Path) -> Path:
    files = [path for path in baselines_dir.glob("*.json") if path.is_file()]
    if not files:
        raise SystemExit(f"no baseline JSON under {baselines_dir}")
    return max(files, key=lambda path: path.stat().st_mtime)


def _matching_detail(runs_dir: Path, baseline: Mapping[str, Any]) -> Path:
    """The detail file for this baseline, matched on nonce then run_key.

    Never falls back to "the newest detail file". A triage issue that pairs one
    run's verdicts with another run's replies reads as evidence and is not.
    """

    nonce = baseline.get("nonce")
    run_key = baseline.get("run_key")
    details = sorted(runs_dir.glob("*-detail.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in details:
        try:
            content = _read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if nonce and content.get("nonce") == nonce:
            return path
        if run_key and content.get("run_key") == run_key:
            return path
    raise SystemExit(f"no detail file in {runs_dir} matches run_key={run_key!r} nonce={nonce!r}")


def _probe_set(probes_dir: Path, probe_set_id: str) -> ProbeSet:
    path = find_probe_set_file(probes_dir, probe_set_id)
    return load_probe_set(_read_json(path))


def _replies(detail: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    return {(str(row["probe"]), str(row["arm"])): row for row in detail.get("arms", [])}


def _seed_block(probe_set: ProbeSet, scope: str) -> str:
    seed = probe_set.seed
    if scope == "short_term":
        return "\n".join(f"- {line}" for line in seed.short_term) or "- (none)"
    if scope == "long_term":
        pairs = "\n".join(f"- {key}: `{value}`" for key, value in seed.long_term.items())
        return pairs or "- (none)"
    if scope == "episodic":
        return (
            "\n".join(
                f"- {episode.request} (approve: {episode.approve})" for episode in seed.episodic
            )
            or "- (none)"
        )
    return f"- corpus: `{seed.semantic_corpus_dir or '(none)'}`"


def _expectation_block(probe: Probe) -> str:
    lines: list[str] = []
    if probe.expect_any:
        lines.append(f"- **expect_any**: {', '.join(f'`{item}`' for item in probe.expect_any)}")
    if probe.stale_any:
        lines.append(
            f"- **stale_any** (superseded, must NOT appear): "
            f"{', '.join(f'`{item}`' for item in probe.stale_any)}"
        )
    if probe.expect_refusal:
        lines.append("- **expect_refusal**: yes")
    if probe.refusal_about:
        lines.append(
            f"- **refusal_about**: {', '.join(f'`{item}`' for item in probe.refusal_about)}"
        )
    if probe.invented_any:
        lines.append(
            f"- **invented_any** (near-miss the probe deliberately baits): "
            f"{', '.join(f'`{item}`' for item in probe.invented_any)}"
        )
    if probe.note:
        lines.append(f"- **purpose**: {probe.note}")
    return "\n".join(lines)


def _arm_block(probe_id: str, replies: Mapping[tuple[str, str], Mapping[str, Any]]) -> str:
    blocks: list[str] = []
    for arm in _ARMS:
        row = replies.get((probe_id, arm))
        if row is None:
            blocks.append(f"### `{arm}` arm\n\n_no record in the detail artifact._")
            continue
        masked = row.get("masked") or "-"
        blocks.append(
            f"### `{arm}` arm\n\n"
            f"- outcome: `{row.get('outcome')}` (why: `{row.get('why')}`), "
            f"masked scope: `{masked}`, latency: {row.get('latency_ms')}ms\n"
            f"- stream_errors: `{row.get('stream_errors') or []}`\n\n"
            f"> {str(row.get('reply', '')).strip() or '(empty reply)'}"
        )
    return "\n\n".join(blocks)


_TRIAGE_TEMPLATE = """## Triage record — fill this in

Every claim below cites the probe expectation, a seed line, or an arm reply above.
An uncited mechanism is not triage.

```yaml
agrees_with_router:   # yes | no — and why, if no
mechanism:            # what the evidence shows went wrong
evidence:             # seed line / expectation / arm reply this rests on
prompt_change_idea:   # the instruction that would have prevented it, or "none"
confidence:           # high | medium | low
produced_by:          # agent name + model id
```

Rules: the deterministic verdict stands even if you disagree with it — record the
disagreement, do not restate the score. Propose prompt text; do not edit production
code, probe JSON, or the grader (RUNBOOK rule 4).
"""


def _issue_markdown(
    *,
    probe: Probe,
    row: Mapping[str, Any],
    fault: FaultClass,
    run_key: str,
    model: str,
    replies: Mapping[tuple[str, str], Mapping[str, Any]],
    probe_set: ProbeSet,
) -> str:
    scope = str(row.get("targets"))
    arms = " / ".join(f"{arm} `{row.get(arm)}`" for arm in _ARMS)
    return f"""# {probe.probe_id} — {fault.value}

- **run**: `{run_key}` — model `{model}`, probe set `{probe_set.probe_set_id}`
- **verdict**: `{row.get("verdict")}` (certain: `{row.get("certain")}`)
- **arms**: {arms}
- **scope**: `{scope}` — **test**: `{row.get("test")}`

## Question

> {probe.question}

## What a correct answer had to contain

{_expectation_block(probe)}

## What was seeded into `{scope}`

{_seed_block(probe_set, scope)}

## What each arm replied

{_arm_block(probe.probe_id, replies)}

{_TRIAGE_TEMPLATE}"""


def _index_markdown(
    *,
    run_key: str,
    model: str,
    probe_set_id: str,
    issues: Sequence[tuple[str, FaultClass, Verdict]],
    skipped: Sequence[tuple[str, FaultClass]],
) -> str:
    rows = "\n".join(
        f"| `{probe_id}` | `{verdict}` | `{fault.value}` |"
        f" [{probe_id}.md](./{probe_id}.md) | open |"
        for probe_id, fault, verdict in issues
    )
    memory_faults = [probe_id for probe_id, fault in skipped if fault is FaultClass.MEMORY_FAULT]
    memory_line = ", ".join(f"`{probe_id}`" for probe_id in memory_faults) or "none"
    run_failures = [probe_id for probe_id, fault in skipped if fault is FaultClass.RUN_FAILED]
    run_failed_line = ", ".join(f"`{probe_id}`" for probe_id in run_failures) or "none"
    return f"""# Triage issues — {run_key}

Model `{model}`, probe set `{probe_set_id}`. One issue per probe whose cause is
still open. Work them top to bottom; mark the status column `done` when the
triage record in the file is filled.

| Probe | Verdict | Fault class | Issue | Status |
|---|---|---|---|---|
{rows}

**Rerun, do not diagnose.** `run_failed` — the provider dropped these, so they
support no claim in any direction and no reading will change that:
{run_failed_line}. If this list is long, the run is the problem; repeat it
before drawing anything from the rest.

**Not issues here.** `memory_fault` probes are already attributed — they need
retrieval work, not a reading: {memory_line}. Healthy probes are omitted.

Regenerate with `python scripts/triage_memory_evaluation.py`.
"""


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, help="baseline JSON (default: newest)")
    parser.add_argument("--detail", type=Path, help="detail JSON (default: the matching one)")
    parser.add_argument("--probes", type=Path, help="probe set JSON (default: by probe_set_id)")
    parser.add_argument("--out", type=Path, help="output dir (default: runs/triage/<run_key>)")
    parser.add_argument(
        "--all",
        action="store_true",
        help="also write issues for memory_fault probes",
    )
    args = parser.parse_args(argv)

    baseline_path = args.baseline or _latest_baseline(_BASELINES_DIR)
    baseline = _read_json(baseline_path)
    # An unusable baseline used to yield "issues: 0" and an empty index, which
    # reads exactly like a clean run. Say which one it is.
    if baseline.get("aborted"):
        raise SystemExit(f"{baseline_path}: aborted run — rerun it, do not triage it")
    if not baseline.get("verdicts"):
        raise SystemExit(f"{baseline_path}: no verdicts — nothing was scored")
    detail_path = args.detail or _matching_detail(_RUNS_DIR, baseline)
    detail = _read_json(detail_path)

    probe_set_id = str(baseline.get("probe_set_id", ""))
    try:
        probe_set = (
            load_probe_set(_read_json(args.probes))
            if args.probes
            else _probe_set(_PROBES_DIR, probe_set_id)
        )
    except ProbeSetError as error:
        raise SystemExit(f"probe set {probe_set_id!r}: {error}") from error

    run_key = str(baseline.get("run_key", "unknown-run"))
    model = f"{baseline.get('provider')}/{baseline.get('model')}"
    # `run_key` is the probe set and model, so two runs of the same pair share
    # it and the second would silently overwrite the first's issues. The nonce
    # is what tells them apart — the same reason the baseline carries it.
    nonce = str(baseline.get("nonce") or "")
    out_dir = args.out or _RUNS_DIR / "triage" / (f"{run_key}-{nonce}" if nonce else run_key)
    out_dir.mkdir(parents=True, exist_ok=True)

    by_id = {probe.probe_id: probe for probe in probe_set.probes}
    replies = _replies(detail)
    wanted = set(TRIAGE_WORTHY) | ({FaultClass.MEMORY_FAULT} if args.all else set())

    issues: list[tuple[str, FaultClass, Verdict]] = []
    skipped: list[tuple[str, FaultClass]] = []
    for row in baseline.get("verdicts", []):
        probe_id = str(row["probe"])
        verdict = Verdict(str(row["verdict"]))
        fault = classify(
            verdict,
            Outcome(str(row["full"])),
            Outcome(str(row["ablated"])),
            Outcome(str(row["control"])),
        )
        probe = by_id.get(probe_id)
        if fault not in wanted or probe is None:
            skipped.append((probe_id, fault))
            continue
        (out_dir / f"{probe_id}.md").write_text(
            _issue_markdown(
                probe=probe,
                row=row,
                fault=fault,
                run_key=run_key,
                model=model,
                replies=replies,
                probe_set=probe_set,
            ),
            encoding="utf-8",
        )
        issues.append((probe_id, fault, verdict))

    index = out_dir / "ISSUES.md"
    index.write_text(
        _index_markdown(
            run_key=run_key,
            model=model,
            probe_set_id=probe_set.probe_set_id,
            issues=issues,
            skipped=skipped,
        ),
        encoding="utf-8",
    )

    print(f"baseline: {baseline_path}")
    print(f"detail:   {detail_path}")
    print(f"issues:   {len(issues)} written to {out_dir}")
    run_failed = sum(1 for _, fault in skipped if fault is FaultClass.RUN_FAILED)
    print(f"run_failed: {run_failed} (rerun these; they are not diagnosable)")
    print(f"index:    {index}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
