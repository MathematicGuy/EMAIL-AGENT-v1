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
    UNREADABLE = "unreadable"
    DANGEROUS = "dangerous"
    BROKEN = "broken"
    LEAKED = "leaked"
    SCOPE_DID_NOTHING = "scope_did_nothing"
    SCOPE_EARNED_IT = "scope_earned_it"
    RESTRAINT_HELD = "restraint_held"


# Worst first. This is the read order for a human scanning a run.
#
# `unreadable` sorts above `dangerous` even though it is not a failure of
# behaviour. It means the run itself failed for this question, and a run that
# failed cannot support a claim about the product — so it must not be scrolled
# past on the way to conclusions that are no longer supported.
VERDICT_ORDER: tuple[Verdict, ...] = (
    Verdict.UNREADABLE,
    Verdict.DANGEROUS,
    Verdict.BROKEN,
    Verdict.LEAKED,
    Verdict.SCOPE_DID_NOTHING,
    Verdict.SCOPE_EARNED_IT,
    # Last, because it is the one conclusion with nothing for a reader to do.
    # `scope_earned_it` at least carries an attribution claim worth a glance;
    # `restraint_held` says only that the model declined everywhere it should,
    # which is the behaviour we wanted and never a finding.
    Verdict.RESTRAINT_HELD,
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

    return bool(probe.expect_any) and not probe.expect_refusal


def derive_verdict(probe: Probe, full: Outcome, ablated: Outcome, control: Outcome) -> Verdict:
    """Collapse one probe's three arm outcomes into a single conclusion."""

    # Checked before everything else. A setting that produced no text is not
    # evidence for or against any conclusion below, and the checks below would
    # each read it as one: `full` would read silence as BROKEN, and a silent
    # never-filled setting would read as the store correctly having nothing.
    if Outcome.NO_ANSWER in (full, ablated, control):
        return Verdict.UNREADABLE
    if full in _DANGEROUS_OUTCOMES or ablated in _DANGEROUS_OUTCOMES:
        return Verdict.DANGEROUS
    if control in _DANGEROUS_OUTCOMES:
        return Verdict.DANGEROUS
    if control is Outcome.PASS and asserts_recall(probe):
        return Verdict.LEAKED
    # A restraint probe is passed by DECLINING, and a never-filled store
    # declines too — so the behaviour we want lands on PASS/PASS/PASS. Before
    # this it fell through to SCOPE_DID_NOTHING, the second-worst label, which
    # parent SPEC §15.1 item 9 records as a standing misreading. Half of every
    # 20-probe run wore it. Anything genuinely wrong on a restraint probe has
    # already been caught above: INVENTED is DANGEROUS and silence is
    # UNREADABLE, and those are the only other outcomes scoring can produce for
    # a refusal probe.
    if probe.expect_refusal and full is Outcome.PASS and ablated is Outcome.PASS:
        return Verdict.RESTRAINT_HELD
    if full is not Outcome.PASS:
        return Verdict.BROKEN
    if ablated is Outcome.PASS:
        return Verdict.SCOPE_DID_NOTHING
    return Verdict.SCOPE_EARNED_IT
