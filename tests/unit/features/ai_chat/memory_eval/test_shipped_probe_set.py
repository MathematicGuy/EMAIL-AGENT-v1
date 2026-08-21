from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.generation_context import _MAX_ACTIVE_SESSION_TURNS
from cowork_agent.features.ai_chat.memory_eval.probes import ProbeSet, ProbeTest, load_probe_set


def _probe_set(path: Path) -> ProbeSet:
    return load_probe_set(json.loads(path.read_text(encoding="utf-8")))


def test_the_shipped_probe_set_loads(probe_set_path: Path) -> None:
    _probe_set(probe_set_path)


def test_every_test_type_is_exercised(probe_set_path: Path) -> None:
    exercised = {probe.test for probe in _probe_set(probe_set_path).probes}
    assert exercised == set(ProbeTest)


def test_each_scope_has_at_least_two_probes(probe_set_path: Path) -> None:
    counts = Counter(probe.targets for probe in _probe_set(probe_set_path).probes)
    assert all(counts[scope] >= 2 for scope in MemoryType), counts


def test_every_restraint_probe_declares_what_it_would_be_refusing_about(
    probe_set_path: Path,
) -> None:
    # A restraint probe is graded by looking for a refusal, and the shared
    # phrase list only carries generic words for KINDS OF KNOWLEDGE. Without
    # the probe's own noun, a model that declines by naming the thing asked for
    # is graded INVENTED — which is what happened to lt_restraint_01.
    probe_set = _probe_set(probe_set_path)
    undeclared = [
        probe.probe_id
        for probe in probe_set.probes
        if probe.expect_refusal and not probe.refusal_about
    ]
    assert not undeclared, undeclared


def test_the_seed_fits_the_prompt_window(probe_set_path: Path) -> None:
    # A short_term probe deliberately keeps its seeded session (SPEC §5.3) — the
    # buffer IS the subject. So `live_runner` seeds short_term into that session,
    # then appends the episodic seed turns on top of it, and then asks the probe:
    #
    #     seed_short_term  -> len(seed.short_term) turns
    #     seed_episodic    -> len(seed.episodic) turns, appended after
    #     the probe        -> 1 turn
    #
    # `build_generation_context` keeps only the newest _MAX_ACTIVE_SESSION_TURNS
    # of that session. Overflow therefore evicts the OLDEST short_term seed line
    # — the first fact seeded — and its recall probe fails on the `full` arm and
    # is reported as a memory failure. Nothing in the report would say the
    # harness overran its own context window.
    #
    # The constant is imported, not written as a literal: if the product changes
    # the window, this bound must move with it.
    seed = _probe_set(probe_set_path).seed
    used = len(seed.short_term) + len(seed.episodic) + 1
    assert used <= _MAX_ACTIVE_SESSION_TURNS, (
        f"{probe_set_path.name} seeds {len(seed.short_term)} short_term turns + "
        f"{len(seed.episodic)} episodic turns, and the probe turn makes {used}, "
        f"over the {_MAX_ACTIVE_SESSION_TURNS}-turn prompt window. The oldest "
        f"short_term seed line will be evicted before any short_term probe is "
        f"asked, and will be reported as amnesia."
    )
