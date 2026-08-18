from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval.probes import ProbeTest, load_probe_set

_PATH = Path("evaluations/MEMORIES/probes/v1-four-scopes.json")


def test_the_shipped_probe_set_loads() -> None:
    load_probe_set(json.loads(_PATH.read_text(encoding="utf-8")))


def test_every_scope_is_covered() -> None:
    probe_set = load_probe_set(json.loads(_PATH.read_text(encoding="utf-8")))
    covered = {probe.targets for probe in probe_set.probes}
    assert covered == set(MemoryType)


def test_every_test_type_is_exercised() -> None:
    probe_set = load_probe_set(json.loads(_PATH.read_text(encoding="utf-8")))
    exercised = {probe.test for probe in probe_set.probes}
    assert exercised == set(ProbeTest)


def test_each_scope_has_at_least_two_probes() -> None:
    probe_set = load_probe_set(json.loads(_PATH.read_text(encoding="utf-8")))
    counts = Counter(probe.targets for probe in probe_set.probes)
    assert all(counts[scope] >= 2 for scope in MemoryType), counts


def test_isolation_probes_target_a_scope_that_actually_partitions() -> None:
    # The company RAG corpus has no tenant field: KnowledgeChunk carries none,
    # allowed_chunk_indices filters only document/year/month, and load_corpus
    # accepts tenant_id without using it. A semantic isolation probe would
    # therefore report a leak that describes the store's design, not a
    # regression. long_term and episodic isolation is enforced in SQL.
    probe_set = load_probe_set(json.loads(_PATH.read_text(encoding="utf-8")))
    isolation = [probe for probe in probe_set.probes if probe.test is ProbeTest.ISOLATION]
    assert isolation, "the probe set must still exercise the isolation test type"
    assert all(
        probe.targets in {MemoryType.LONG_TERM, MemoryType.EPISODIC} for probe in isolation
    )


def test_every_foreign_seed_probe_is_an_isolation_probe() -> None:
    probe_set = load_probe_set(json.loads(_PATH.read_text(encoding="utf-8")))
    for probe in probe_set.probes:
        if probe.foreign_seed:
            assert probe.test is ProbeTest.ISOLATION
