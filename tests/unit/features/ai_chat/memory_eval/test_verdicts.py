from __future__ import annotations

import pytest

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval.probes import Probe, ProbeTest
from cowork_agent.features.ai_chat.memory_eval.scoring import Outcome
from cowork_agent.features.ai_chat.memory_eval.verdicts import (
    Verdict,
    derive_verdict,
    verdict_rank,
)

pytestmark = pytest.mark.extended


def _probe(**overrides: object) -> Probe:
    defaults: dict[str, object] = {
        "probe_id": "p",
        "targets": MemoryType.EPISODIC,
        "test": ProbeTest.RECALL,
        "question": "q",
        "expect_any": ("x",),
    }
    defaults.update(overrides)
    return Probe(**defaults)  # type: ignore[arg-type]


def test_scope_earned_it_when_only_the_full_arm_passes() -> None:
    verdict = derive_verdict(_probe(), Outcome.PASS, Outcome.MISS, Outcome.MISS)
    assert verdict is Verdict.SCOPE_EARNED_IT


def test_scope_did_nothing_when_ablation_still_passes() -> None:
    verdict = derive_verdict(_probe(), Outcome.PASS, Outcome.PASS, Outcome.MISS)
    assert verdict is Verdict.SCOPE_DID_NOTHING


def test_control_passing_a_recall_probe_is_a_leak() -> None:
    verdict = derive_verdict(_probe(), Outcome.PASS, Outcome.MISS, Outcome.PASS)
    assert verdict is Verdict.LEAKED


def test_broken_when_the_full_arm_fails() -> None:
    verdict = derive_verdict(_probe(), Outcome.MISS, Outcome.MISS, Outcome.MISS)
    assert verdict is Verdict.BROKEN


def test_invented_anywhere_outranks_every_other_verdict() -> None:
    verdict = derive_verdict(_probe(), Outcome.INVENTED, Outcome.MISS, Outcome.MISS)
    assert verdict is Verdict.DANGEROUS


def test_stale_anywhere_is_dangerous() -> None:
    verdict = derive_verdict(_probe(), Outcome.STALE, Outcome.MISS, Outcome.MISS)
    assert verdict is Verdict.DANGEROUS


def test_dangerous_beats_leaked() -> None:
    # A control pass AND an invented answer: the invention is the headline.
    verdict = derive_verdict(_probe(), Outcome.INVENTED, Outcome.MISS, Outcome.PASS)
    assert verdict is Verdict.DANGEROUS


def test_refusal_probes_never_count_as_leaks() -> None:
    # An empty store declines every time, so a control PASS here is expected
    # and would otherwise be flagged in every run forever. SPEC §9.2.
    probe = _probe(expect_any=(), expect_refusal=True, test=ProbeTest.RESTRAINT)
    verdict = derive_verdict(probe, Outcome.PASS, Outcome.PASS, Outcome.PASS)
    assert verdict is not Verdict.LEAKED

def test_verdict_ordering_puts_dangerous_first_and_earned_last() -> None:
    ordered = sorted(
        [Verdict.SCOPE_EARNED_IT, Verdict.DANGEROUS, Verdict.LEAKED, Verdict.BROKEN],
        key=verdict_rank,
    )
    assert ordered[0] is Verdict.DANGEROUS
    assert ordered[-1] is Verdict.SCOPE_EARNED_IT


def test_an_arm_that_produced_no_answer_makes_the_whole_row_unreadable() -> None:
    # Three arms of the first Vietnamese run returned no text at all, because
    # the provider was briefly unavailable. Folding that into MISS produced a
    # `leaked` on semantic and a `scope_did_nothing` on long_term that were
    # conclusions about an outage, not about memory.
    for full, ablated, control in (
        (Outcome.NO_ANSWER, Outcome.PASS, Outcome.MISS),
        (Outcome.PASS, Outcome.NO_ANSWER, Outcome.MISS),
        (Outcome.PASS, Outcome.MISS, Outcome.NO_ANSWER),
    ):
        assert derive_verdict(_probe(), full, ablated, control) is Verdict.UNREADABLE


def test_unreadable_outranks_every_conclusion_about_behaviour() -> None:
    # A row you could not read must not sort below a row you could. It says the
    # run failed, and a failed run invalidates whatever else it printed.
    for verdict in Verdict:
        if verdict is Verdict.UNREADABLE:
            continue
        assert verdict_rank(Verdict.UNREADABLE) < verdict_rank(verdict)


def test_no_answer_is_never_read_as_dangerous_behaviour() -> None:
    # Silence is not invention. It is the absence of evidence either way.
    verdict = derive_verdict(_probe(), Outcome.NO_ANSWER, Outcome.NO_ANSWER, Outcome.NO_ANSWER)
    assert verdict is Verdict.UNREADABLE
