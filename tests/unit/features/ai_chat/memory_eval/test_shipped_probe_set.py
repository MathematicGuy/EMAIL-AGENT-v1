from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval.probes import ProbeTest, load_probe_set

pytestmark = pytest.mark.extended

_PATH = Path("evaluations/MEMORIES/probes/v1-four-scopes.json")


def test_the_shipped_probe_set_loads() -> None:
    load_probe_set(json.loads(_PATH.read_text(encoding="utf-8")))

def test_every_test_type_is_exercised() -> None:
    probe_set = load_probe_set(json.loads(_PATH.read_text(encoding="utf-8")))
    exercised = {probe.test for probe in probe_set.probes}
    assert exercised == set(ProbeTest)


def test_each_scope_has_at_least_two_probes() -> None:
    probe_set = load_probe_set(json.loads(_PATH.read_text(encoding="utf-8")))
    counts = Counter(probe.targets for probe in probe_set.probes)
    assert all(counts[scope] >= 2 for scope in MemoryType), counts



def test_every_restraint_probe_declares_what_it_would_be_refusing_about() -> None:
    # A restraint probe is graded by looking for a refusal, and the shared
    # phrase list only carries generic words for KINDS OF KNOWLEDGE. Without
    # the probe's own noun, a model that declines by naming the thing asked for
    # is graded INVENTED — which is what happened to lt_restraint_01.
    probe_set = load_probe_set(json.loads(_PATH.read_text(encoding="utf-8")))
    undeclared = [
        probe.probe_id
        for probe in probe_set.probes
        if probe.expect_refusal and not probe.refusal_about
    ]
    assert not undeclared, undeclared
