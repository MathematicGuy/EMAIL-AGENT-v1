"""Metadata-only report assembly (SPEC §10).

The committed artifact carries case ids, counts, verdicts, timings and model
identifiers — and nothing else. No probe question, no reply, no seed text. A
report that leaks the corpus it was scored against cannot be committed, and
"we were careful" is not an enforcement mechanism, so a test asserts it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from cowork_agent.domain.chat_contracts import MemoryType

from .probes import ProbeSet, ProbeTest
from .scoring import Outcome
from .verdicts import Verdict, derive_verdict, verdict_rank

REPORT_SCHEMA_VERSION = "1.0.0"

_VERDICT_COUNT_KEYS: dict[Verdict, str] = {
    Verdict.DANGEROUS: "dangerous",
    Verdict.BROKEN: "broken",
    Verdict.LEAKED: "leaked",
    Verdict.SCOPE_DID_NOTHING: "did_nothing",
    Verdict.SCOPE_EARNED_IT: "earned_it",
}


@dataclass(frozen=True, slots=True)
class ProbeRow:
    """One probe's outcomes across all three arms."""

    probe_id: str
    targets: MemoryType
    test: ProbeTest
    full: Outcome
    ablated: Outcome
    control: Outcome
    certain: bool
    latency_ms: int


def _empty_scope_counts() -> dict[str, int]:
    counts = {"probes": 0, "pass": 0, "stale": 0, "invented": 0, "miss": 0}
    counts.update({key: 0 for key in _VERDICT_COUNT_KEYS.values()})
    return counts


def build_report(
    probe_set: ProbeSet,
    rows: Sequence[ProbeRow],
    *,
    provider: str,
    model: str,
    judge_model: str | None,
    run_key: str,
    ran_at: datetime,
    seed_failures: Sequence[str] = (),
    unscorable_probes: Sequence[str] = (),
    degraded_sources_seen: Sequence[str] = (),
) -> dict[str, object]:
    by_id = {probe.probe_id: probe for probe in probe_set.probes}
    per_scope: dict[str, dict[str, int]] = {
        member.value: _empty_scope_counts() for member in MemoryType
    }

    entries: list[tuple[int, dict[str, object]]] = []
    leaked: list[str] = []
    needs_judge = 0

    for row in rows:
        probe = by_id.get(row.probe_id)
        if probe is None:
            raise ValueError(f"row references unknown probe {row.probe_id!r}")
        verdict = derive_verdict(probe, row.full, row.ablated, row.control)
        bucket = per_scope[row.targets.value]
        bucket["probes"] += 1
        bucket[row.full.value] += 1
        bucket[_VERDICT_COUNT_KEYS[verdict]] += 1
        if verdict is Verdict.LEAKED:
            leaked.append(row.probe_id)
        if not row.certain:
            needs_judge += 1
        entries.append(
            (
                verdict_rank(verdict),
                {
                    "probe": row.probe_id,
                    "targets": row.targets.value,
                    "test": row.test.value,
                    "full": row.full.value,
                    "ablated": row.ablated.value,
                    "control": row.control.value,
                    "verdict": verdict.value,
                    "certain": row.certain,
                    "latency_ms": row.latency_ms,
                },
            )
        )

    entries.sort(key=lambda item: (item[0], str(item[1]["probe"])))

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "probe_set_id": probe_set.probe_set_id,
        "probe_count": len(probe_set.probes),
        "provider": provider,
        "model": model,
        "judge_model": judge_model,
        "ran_at": ran_at.isoformat(),
        "run_key": run_key,
        "per_scope": per_scope,
        "verdicts": [entry for _, entry in entries],
        "leaked_probes": sorted(leaked),
        "unscorable_probes": sorted(unscorable_probes),
        "needs_judge": needs_judge,
        "seed_failures": sorted(seed_failures),
        "degraded_sources_seen": sorted(set(degraded_sources_seen)),
    }
