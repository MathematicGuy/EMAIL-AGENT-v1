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

# 2.1.0 adds `nonce`. Additive: a 2.0.0 reader that ignores unknown keys still
# reads a 2.1.0 report, and every field it knew about means what it did.
#
# 2.2.0 is NOT additive, which is why it is a version and not a new key. Two
# fields change meaning: `per_scope.did_nothing` sheds its restraint rows to
# `restraint_held` (SPEC §7.1), and `needs_reading` sheds the restraint rows
# whose probe declared `invented_any` (§6.3). Both counts fall against a 2.1.0
# baseline without anything about the product having changed, so §12.2 rule 5
# does not hold across the bump.
REPORT_SCHEMA_VERSION = "2.2.0"

_VERDICT_COUNT_KEYS: dict[Verdict, str] = {
    Verdict.UNREADABLE: "unreadable",
    Verdict.DANGEROUS: "dangerous",
    Verdict.BROKEN: "broken",
    Verdict.LEAKED: "leaked",
    Verdict.SCOPE_DID_NOTHING: "did_nothing",
    Verdict.SCOPE_EARNED_IT: "earned_it",
    Verdict.RESTRAINT_HELD: "restraint_held",
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
    counts = {"probes": 0, "pass": 0, "stale": 0, "invented": 0, "miss": 0, "no_answer": 0}
    counts.update({key: 0 for key in _VERDICT_COUNT_KEYS.values()})
    return counts


def build_report(
    probe_set: ProbeSet,
    rows: Sequence[ProbeRow],
    *,
    provider: str,
    model: str,
    run_key: str,
    ran_at: datetime,
    seed_failures: Sequence[str] = (),
    nonce: str = "",
) -> dict[str, object]:
    by_id = {probe.probe_id: probe for probe in probe_set.probes}
    per_scope: dict[str, dict[str, int]] = {
        member.value: _empty_scope_counts() for member in MemoryType
    }

    entries: list[tuple[int, dict[str, object]]] = []
    leaked: list[str] = []
    needs_reading = 0

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
            needs_reading += 1
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
        "ran_at": ran_at.isoformat(),
        "run_key": run_key,
        # Two runs of the same probe set and model share a `run_key` by design,
        # so on its own it cannot say which of two overlapping runs wrote this
        # file. The nonce is what the run's stores were named after, and it is
        # the only field that distinguishes them. Empty when the caller did not
        # build an identity — the offline and dry-run paths.
        "nonce": nonce,
        "per_scope": per_scope,
        "verdicts": [entry for _, entry in entries],
        "leaked_probes": sorted(leaked),
        "needs_reading": needs_reading,
        "seed_failures": sorted(seed_failures),
    }
