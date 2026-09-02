from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.generation_context import _MAX_ACTIVE_SESSION_TURNS
from cowork_agent.features.ai_chat.memory_eval.probes import ProbeSet, ProbeTest, load_probe_set

from .conftest import PROBE_SET_PATHS


def _probe_set(path: Path) -> ProbeSet:
    return load_probe_set(json.loads(path.read_text(encoding="utf-8")))


def test_the_shipped_probe_set_loads() -> None:
    for path in PROBE_SET_PATHS:
        _probe_set(path)


def test_every_test_type_is_exercised() -> None:
    for path in PROBE_SET_PATHS:
        exercised = {probe.test for probe in _probe_set(path).probes}
        assert exercised == set(ProbeTest)


def test_each_scope_has_at_least_two_probes() -> None:
    for path in PROBE_SET_PATHS:
        counts = Counter(probe.targets for probe in _probe_set(path).probes)
        assert all(counts[scope] >= 2 for scope in MemoryType), counts


def test_every_restraint_probe_declares_what_it_would_be_refusing_about() -> None:
    for path in PROBE_SET_PATHS:
        probe_set = _probe_set(path)
        undeclared = [
            probe.probe_id
            for probe in probe_set.probes
            if probe.expect_refusal and not probe.refusal_about
        ]
        assert not undeclared, undeclared


def test_the_seed_fits_the_prompt_window() -> None:
    for path in PROBE_SET_PATHS:
        seed = _probe_set(path).seed
        used = len(seed.short_term) + 1
        assert used <= _MAX_ACTIVE_SESSION_TURNS, (
            f"{path.name} seeds {len(seed.short_term)} short_term turns, "
            f"and the probe turn makes {used}, over the "
            f"{_MAX_ACTIVE_SESSION_TURNS}-turn prompt window."
        )
