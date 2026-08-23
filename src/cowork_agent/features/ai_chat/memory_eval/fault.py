"""Attributing a verdict to the part of the system that produced it.

`verdicts.derive_verdict` says WHAT happened to a probe. This says WHERE to
look: at retrieval, or at the prompt. The signal is already in the three arms
and was only ever read by hand — a `dangerous` full arm whose ablated and
control arms both refuse means memory delivered and generation misused it,
while a miss in all three means retrieval never delivered anything a prompt
could have used.

Reading it by hand each time is how a report gets triaged into "rewrite the
prompt" when the fix is retrieval. Deriving it makes the attribution checkable
against the same rule every run.
"""

from __future__ import annotations

from enum import StrEnum

from .scoring import Outcome
from .verdicts import Verdict

# The same pair `verdicts` calls dangerous. Named again here because this
# module asks a different question of it: not "is this bad" but "did the arm
# that was supposed to be blind answer wrongly anyway".
_DANGEROUS_OUTCOMES = frozenset({Outcome.STALE, Outcome.INVENTED})


class FaultClass(StrEnum):
    PROMPT_FAULT = "prompt_fault"
    MEMORY_FAULT = "memory_fault"
    # A run that failed here and a probe that cannot attribute its result are
    # both "we do not know", but they ask for opposite work: one is a rerun, the
    # other is a reading. Held together, provider dropouts flood the triage
    # queue and bury the probes that need a person.
    RUN_FAILED = "run_failed"
    NOT_ATTRIBUTABLE = "not_attributable"
    HEALTHY = "healthy"


def classify(verdict: Verdict, full: Outcome, ablated: Outcome, control: Outcome) -> FaultClass:
    """Attribute one probe's result to prompt, memory, or neither."""

    if verdict is Verdict.DANGEROUS:
        # Only the full arm may see the scope. If a blind arm produced the same
        # wrong answer, the scope was not the source of it and nothing here is
        # attributable — including to the prompt, which both arms also used.
        if ablated in _DANGEROUS_OUTCOMES or control in _DANGEROUS_OUTCOMES:
            return FaultClass.NOT_ATTRIBUTABLE
        if full in _DANGEROUS_OUTCOMES:
            return FaultClass.PROMPT_FAULT
        return FaultClass.NOT_ATTRIBUTABLE
    if verdict is Verdict.BROKEN:
        # The full arm did not answer. No instruction makes a model state a
        # fact that never reached it.
        return FaultClass.MEMORY_FAULT
    if verdict is Verdict.LEAKED:
        # A clean-store arm answered a recall probe: the store is not isolated.
        return FaultClass.MEMORY_FAULT
    if verdict is Verdict.SCOPE_DID_NOTHING:
        # The answer was reachable without the scope, so the run says nothing
        # about either part. Usually a probe-design question.
        return FaultClass.NOT_ATTRIBUTABLE
    if verdict is Verdict.UNREADABLE:
        # The run failed for this probe — a dropout, a timeout, an exhausted
        # key. There is no reading to do; there is a run to repeat.
        return FaultClass.RUN_FAILED
    return FaultClass.HEALTHY


# Which classes are worth a coding agent's time. `memory_fault` is already
# attributed and needs retrieval work rather than a reading; `healthy` has
# nothing to explain; `run_failed` is a rerun, and reading a timeout teaches
# nobody anything. Triage exists to answer "why", and these are the two classes
# where "why" is still open and still answerable.
TRIAGE_WORTHY: frozenset[FaultClass] = frozenset(
    {FaultClass.PROMPT_FAULT, FaultClass.NOT_ATTRIBUTABLE}
)
