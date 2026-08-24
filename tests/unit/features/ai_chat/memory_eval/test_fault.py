from __future__ import annotations

from cowork_agent.features.ai_chat.memory_eval.fault import (
    TRIAGE_WORTHY,
    FaultClass,
    classify,
)
from cowork_agent.features.ai_chat.memory_eval.scoring import Outcome
from cowork_agent.features.ai_chat.memory_eval.verdicts import Verdict


def test_invention_only_the_full_arm_made_is_a_prompt_fault() -> None:
    # The 2026-08-21 `ep_restraint_02` shape: memory handed generation a
    # neighbouring episode and generation attached it to the wrong task.
    fault = classify(Verdict.DANGEROUS, Outcome.INVENTED, Outcome.PASS, Outcome.PASS)
    assert fault is FaultClass.PROMPT_FAULT


def test_stale_answer_only_the_full_arm_gave_is_a_prompt_fault() -> None:
    fault = classify(Verdict.DANGEROUS, Outcome.STALE, Outcome.MISS, Outcome.MISS)
    assert fault is FaultClass.PROMPT_FAULT


def test_invention_a_blind_arm_also_made_is_not_attributable() -> None:
    fault = classify(Verdict.DANGEROUS, Outcome.INVENTED, Outcome.INVENTED, Outcome.PASS)
    assert fault is FaultClass.NOT_ATTRIBUTABLE


def test_invention_the_control_arm_also_made_is_not_attributable() -> None:
    fault = classify(Verdict.DANGEROUS, Outcome.INVENTED, Outcome.PASS, Outcome.STALE)
    assert fault is FaultClass.NOT_ATTRIBUTABLE


def test_a_miss_in_every_arm_is_a_memory_fault() -> None:
    # The 2026-08-21 `ep_recall_01` shape. No prompt text conjures a fact that
    # never reached the model.
    fault = classify(Verdict.BROKEN, Outcome.MISS, Outcome.MISS, Outcome.MISS)
    assert fault is FaultClass.MEMORY_FAULT


def test_a_leak_is_a_memory_fault() -> None:
    fault = classify(Verdict.LEAKED, Outcome.PASS, Outcome.MISS, Outcome.PASS)
    assert fault is FaultClass.MEMORY_FAULT


def test_scope_did_nothing_is_not_a_prompt_worklist_item() -> None:
    fault = classify(Verdict.SCOPE_DID_NOTHING, Outcome.PASS, Outcome.PASS, Outcome.MISS)
    assert fault is FaultClass.NOT_ATTRIBUTABLE


def test_a_failed_run_is_a_rerun_not_a_reading() -> None:
    fault = classify(Verdict.UNREADABLE, Outcome.NO_ANSWER, Outcome.PASS, Outcome.PASS)
    assert fault is FaultClass.RUN_FAILED


def test_earned_it_and_restraint_held_are_healthy() -> None:
    earned = classify(Verdict.SCOPE_EARNED_IT, Outcome.PASS, Outcome.MISS, Outcome.MISS)
    held = classify(Verdict.RESTRAINT_HELD, Outcome.PASS, Outcome.PASS, Outcome.PASS)
    assert earned is FaultClass.HEALTHY
    assert held is FaultClass.HEALTHY


def test_every_verdict_has_a_fault_class() -> None:
    # A new verdict must not fall through to HEALTHY by accident, which is the
    # one wrong answer nobody would notice in a report.
    for verdict in Verdict:
        assert isinstance(classify(verdict, Outcome.PASS, Outcome.PASS, Outcome.PASS), FaultClass)


def test_triage_covers_the_two_classes_whose_cause_is_still_open() -> None:
    assert TRIAGE_WORTHY == {FaultClass.PROMPT_FAULT, FaultClass.NOT_ATTRIBUTABLE}


def test_a_failed_run_never_reaches_the_triage_queue() -> None:
    # Four of five issues on the 2026-08-23 mistral run were dropouts. An
    # agent reading timeouts is an agent not reading the one real defect.
    assert FaultClass.RUN_FAILED not in TRIAGE_WORTHY
