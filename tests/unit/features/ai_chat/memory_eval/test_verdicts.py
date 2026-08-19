from __future__ import annotations

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval.probes import Probe, ProbeTest
from cowork_agent.features.ai_chat.memory_eval.scoring import Outcome
from cowork_agent.features.ai_chat.memory_eval.verdicts import (
    Verdict,
    derive_verdict,
    verdict_rank,
)


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
