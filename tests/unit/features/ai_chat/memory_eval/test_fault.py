from __future__ import annotations

from cowork_agent.features.ai_chat.memory_eval.fault import (
    TRIAGE_WORTHY,
    FaultClass,
    classify,
)
from cowork_agent.features.ai_chat.memory_eval.scoring import Outcome
from cowork_agent.features.ai_chat.memory_eval.verdicts import Verdict


def test_classify_fault_matrix() -> None:
    # Prompt faults (full arm only invented or stale)
    assert (
        classify(Verdict.DANGEROUS, Outcome.INVENTED, Outcome.PASS, Outcome.PASS)
        is FaultClass.PROMPT_FAULT
    )
    assert (
        classify(Verdict.DANGEROUS, Outcome.STALE, Outcome.MISS, Outcome.MISS)
        is FaultClass.PROMPT_FAULT
    )

    # Not attributable (blind or control arm also invented/stale or scope did nothing)
    assert (
        classify(Verdict.DANGEROUS, Outcome.INVENTED, Outcome.INVENTED, Outcome.PASS)
        is FaultClass.NOT_ATTRIBUTABLE
    )
    assert (
        classify(Verdict.DANGEROUS, Outcome.INVENTED, Outcome.PASS, Outcome.STALE)
        is FaultClass.NOT_ATTRIBUTABLE
    )
    assert (
        classify(Verdict.SCOPE_DID_NOTHING, Outcome.PASS, Outcome.PASS, Outcome.MISS)
        is FaultClass.NOT_ATTRIBUTABLE
    )

    # Memory faults (broken or leaked)
    assert (
        classify(Verdict.BROKEN, Outcome.MISS, Outcome.MISS, Outcome.MISS)
        is FaultClass.MEMORY_FAULT
    )
    assert (
        classify(Verdict.LEAKED, Outcome.PASS, Outcome.MISS, Outcome.PASS)
        is FaultClass.MEMORY_FAULT
    )

    # Run failed & Healthy
    assert (
        classify(Verdict.UNREADABLE, Outcome.NO_ANSWER, Outcome.PASS, Outcome.PASS)
        is FaultClass.RUN_FAILED
    )
    assert (
        classify(Verdict.SCOPE_EARNED_IT, Outcome.PASS, Outcome.MISS, Outcome.MISS)
        is FaultClass.HEALTHY
    )
    assert (
        classify(Verdict.RESTRAINT_HELD, Outcome.PASS, Outcome.PASS, Outcome.PASS)
        is FaultClass.HEALTHY
    )

    # Every verdict classifies into a FaultClass
    for verdict in Verdict:
        assert isinstance(classify(verdict, Outcome.PASS, Outcome.PASS, Outcome.PASS), FaultClass)


def test_triage_worthy_set() -> None:
    assert TRIAGE_WORTHY == {FaultClass.PROMPT_FAULT, FaultClass.NOT_ATTRIBUTABLE}
    assert FaultClass.RUN_FAILED not in TRIAGE_WORTHY
