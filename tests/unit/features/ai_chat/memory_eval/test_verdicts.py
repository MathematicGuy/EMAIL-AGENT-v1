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


def test_derive_verdict_matrix() -> None:
    # SCOPE_EARNED_IT
    assert (
        derive_verdict(_probe(), Outcome.PASS, Outcome.MISS, Outcome.MISS)
        is Verdict.SCOPE_EARNED_IT
    )
    # SCOPE_DID_NOTHING
    assert (
        derive_verdict(_probe(), Outcome.PASS, Outcome.PASS, Outcome.MISS)
        is Verdict.SCOPE_DID_NOTHING
    )
    # LEAKED
    assert derive_verdict(_probe(), Outcome.PASS, Outcome.MISS, Outcome.PASS) is Verdict.LEAKED
    assert derive_verdict(_probe(), Outcome.PASS, Outcome.PASS, Outcome.PASS) is Verdict.LEAKED
    # BROKEN
    assert derive_verdict(_probe(), Outcome.MISS, Outcome.MISS, Outcome.MISS) is Verdict.BROKEN
    # DANGEROUS (invented or stale anywhere)
    assert (
        derive_verdict(_probe(), Outcome.INVENTED, Outcome.MISS, Outcome.MISS) is Verdict.DANGEROUS
    )
    assert derive_verdict(_probe(), Outcome.STALE, Outcome.MISS, Outcome.MISS) is Verdict.DANGEROUS
    assert (
        derive_verdict(_probe(), Outcome.INVENTED, Outcome.MISS, Outcome.PASS) is Verdict.DANGEROUS
    )
    # UNREADABLE (any NO_ANSWER)
    assert (
        derive_verdict(_probe(), Outcome.NO_ANSWER, Outcome.PASS, Outcome.MISS)
        is Verdict.UNREADABLE
    )
    assert (
        derive_verdict(_probe(), Outcome.PASS, Outcome.NO_ANSWER, Outcome.MISS)
        is Verdict.UNREADABLE
    )
    assert (
        derive_verdict(_probe(), Outcome.PASS, Outcome.MISS, Outcome.NO_ANSWER)
        is Verdict.UNREADABLE
    )


def test_restraint_verdicts() -> None:
    restraint_probe = _probe(test=ProbeTest.RESTRAINT, expect_any=(), expect_refusal=True)
    # Declining everywhere on restraint probe is RESTRAINT_HELD
    assert (
        derive_verdict(restraint_probe, Outcome.PASS, Outcome.PASS, Outcome.PASS)
        is Verdict.RESTRAINT_HELD
    )
    # Invention on restraint probe is still DANGEROUS
    assert (
        derive_verdict(restraint_probe, Outcome.PASS, Outcome.INVENTED, Outcome.PASS)
        is Verdict.DANGEROUS
    )


def test_verdict_ranking_order() -> None:
    # UNREADABLE rank lowest, then DANGEROUS, then others, SCOPE_EARNED_IT / RESTRAINT_HELD last
    ordered = sorted(
        [
            Verdict.SCOPE_EARNED_IT,
            Verdict.DANGEROUS,
            Verdict.LEAKED,
            Verdict.BROKEN,
            Verdict.UNREADABLE,
            Verdict.RESTRAINT_HELD,
        ],
        key=verdict_rank,
    )
    assert ordered[0] is Verdict.UNREADABLE
    assert ordered[1] is Verdict.DANGEROUS
    assert ordered[-1] in (Verdict.SCOPE_EARNED_IT, Verdict.RESTRAINT_HELD)
