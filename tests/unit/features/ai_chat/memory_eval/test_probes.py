from __future__ import annotations

import pytest

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval.probes import (
    ProbeSetError,
    ProbeTest,
    load_probe_set,
)


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schema_version": "2.0.0",
        "probe_set_id": "unit",
        "label": "unit probe set",
        "seed": {
            "short_term": ["a turn"],
            "long_term": {"language": "vi"},
            "episodic": [{"request": "Create a task to renew", "approve": True}],
            "semantic": {"corpus_dir": "tests/fixtures/memory_eval/corpus"},
        },
        "probes": [
            {
                "id": "st_recall_01",
                "targets": "short_term",
                "test": "recall",
                "question": "what did I say?",
                "expect_any": ["a turn"],
            }
        ],
    }
    base.update(overrides)
    return base


def test_loads_a_minimal_probe_set() -> None:
    probe_set = load_probe_set(_payload())
    assert probe_set.probe_set_id == "unit"
    assert len(probe_set.probes) == 1
    probe = probe_set.probes[0]
    assert probe.probe_id == "st_recall_01"
    assert probe.targets is MemoryType.SHORT_TERM
    assert probe.test is ProbeTest.RECALL
    assert probe.expect_any == ("a turn",)
    assert probe.stale_any == ()
    assert probe.expect_refusal is False


def test_seed_is_parsed_into_typed_fields() -> None:
    seed = load_probe_set(_payload()).seed
    assert seed.short_term == ("a turn",)
    assert seed.long_term == {"language": "vi"}
    assert seed.episodic[0].request == "Create a task to renew"
    assert seed.episodic[0].approve is True
    assert seed.semantic_corpus_dir == "tests/fixtures/memory_eval/corpus"


def test_probe_with_no_expectation_is_rejected() -> None:
    payload = _payload(
        probes=[
            {"id": "bad", "targets": "short_term", "test": "recall", "question": "q"}
        ]
    )
    with pytest.raises(ProbeSetError, match="expectation"):
        load_probe_set(payload)


def test_duplicate_probe_ids_are_rejected() -> None:
    probe = {
        "id": "dupe",
        "targets": "short_term",
        "test": "recall",
        "question": "q",
        "expect_any": ["x"],
    }
    with pytest.raises(ProbeSetError, match="unique"):
        load_probe_set(_payload(probes=[probe, dict(probe)]))


def test_unsafe_probe_id_is_rejected() -> None:
    payload = _payload(
        probes=[
            {
                "id": "bad id!",
                "targets": "short_term",
                "test": "recall",
                "question": "q",
                "expect_any": ["x"],
            }
        ]
    )
    with pytest.raises(ProbeSetError, match="identifier"):
        load_probe_set(payload)


def test_unknown_scope_is_rejected() -> None:
    payload = _payload(
        probes=[
            {
                "id": "p",
                "targets": "procedural",
                "test": "recall",
                "question": "q",
                "expect_any": ["x"],
            }
        ]
    )
    with pytest.raises(ProbeSetError, match="targets"):
        load_probe_set(payload)


def test_unsupported_schema_version_is_rejected() -> None:
    with pytest.raises(ProbeSetError, match="schema_version"):
        load_probe_set(_payload(schema_version="9.9.9"))


