"""Turning three outcomes into one readable conclusion (SPEC §9).

The scoreboard is sorted worst-behaviour-first: a system that invents ranks
below one that misses, so the interesting column is never buried under a wall
of passes.
"""

from __future__ import annotations

from enum import StrEnum

from .probes import Probe
from .scoring import Outcome


class Verdict(StrEnum):
    DANGEROUS = "dangerous"
    BROKEN = "broken"
    LEAKED = "leaked"
    SCOPE_DID_NOTHING = "scope_did_nothing"
    SCOPE_EARNED_IT = "scope_earned_it"


# Worst first. This is the read order for a human scanning a run.
VERDICT_ORDER: tuple[Verdict, ...] = (
    Verdict.DANGEROUS,
    Verdict.BROKEN,
    Verdict.LEAKED,
    Verdict.SCOPE_DID_NOTHING,
    Verdict.SCOPE_EARNED_IT,
)

_DANGEROUS_OUTCOMES = frozenset({Outcome.STALE, Outcome.INVENTED})


def verdict_rank(verdict: Verdict) -> int:
    return VERDICT_ORDER.index(verdict)


def asserts_recall(probe: Probe) -> bool:
    """Whether a control PASS on this probe means anything.

    Only probes that assert RECALLED CONTENT can leak. A refusal probe is
    passed by declining, and a store with nothing in it declines every time —
    flagging those would mark them in every run, forever, and mean nothing.
    """

    return bool(probe.expect_any or probe.expect_all) and not probe.expect_refusal


def derive_verdict(probe: Probe, full: Outcome, ablated: Outcome, control: Outcome) -> Verdict:
    """Collapse one probe's three arm outcomes into a single conclusion."""

    if full in _DANGEROUS_OUTCOMES or ablated in _DANGEROUS_OUTCOMES:
        return Verdict.DANGEROUS
    if control in _DANGEROUS_OUTCOMES:
        return Verdict.DANGEROUS
    if control is Outcome.PASS and asserts_recall(probe):
        return Verdict.LEAKED
    if full is not Outcome.PASS:
        return Verdict.BROKEN
    if ablated is Outcome.PASS:
        return Verdict.SCOPE_DID_NOTHING
    return Verdict.SCOPE_EARNED_IT
