from __future__ import annotations

import json
from pathlib import Path

import pytest

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval.probes import (
    ProbeSetError,
    ProbeTest,
    find_probe_set_file,
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


def test_load_probe_set_valid_payload_and_parsing() -> None:
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
    assert probe.refusal_about == ()
    assert probe.invented_any == ()

    seed = probe_set.seed
    assert seed.short_term == ("a turn",)
    assert seed.long_term == {"language": "vi"}
    assert seed.episodic[0].request == "Create a task to renew"
    assert seed.episodic[0].approve is True
    assert seed.semantic_corpus_dir == "tests/fixtures/memory_eval/corpus"

    # Refusal and invented fields
    refusal_payload = _payload(
        probes=[
            {
                "id": "lt_restraint_01",
                "targets": "long_term",
                "test": "restraint",
                "question": "q",
                "expect_refusal": True,
                "refusal_about": ["chức danh"],
                "invented_any": ["Thu Vân"],
            }
        ]
    )
    p2 = load_probe_set(refusal_payload).probes[0]
    assert p2.refusal_about == ("chức danh",)
    assert p2.invented_any == ("Thu Vân",)


def test_load_probe_set_validation_errors() -> None:
    # No expectation
    with pytest.raises(ProbeSetError, match="expectation"):
        load_probe_set(
            _payload(
                probes=[{"id": "bad", "targets": "short_term", "test": "recall", "question": "q"}]
            )
        )

    # Duplicate probe IDs
    probe = {
        "id": "dupe",
        "targets": "short_term",
        "test": "recall",
        "question": "q",
        "expect_any": ["x"],
    }
    with pytest.raises(ProbeSetError, match="unique"):
        load_probe_set(_payload(probes=[probe, dict(probe)]))

    # Unsafe ID
    with pytest.raises(ProbeSetError, match="identifier"):
        load_probe_set(
            _payload(
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
        )

    # Unknown scope
    with pytest.raises(ProbeSetError, match="targets"):
        load_probe_set(
            _payload(
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
        )

    # Unsupported schema
    with pytest.raises(ProbeSetError, match="schema_version"):
        load_probe_set(_payload(schema_version="9.9.9"))

    # Refusal_about or invented_any without expect_refusal
    with pytest.raises(ProbeSetError, match="refusal_about"):
        load_probe_set(
            _payload(
                probes=[
                    {
                        "id": "p1",
                        "targets": "short_term",
                        "test": "recall",
                        "question": "q",
                        "expect_any": ["x"],
                        "refusal_about": ["a"],
                    }
                ]
            )
        )

    with pytest.raises(ProbeSetError, match="invented_any"):
        load_probe_set(
            _payload(
                probes=[
                    {
                        "id": "p2",
                        "targets": "short_term",
                        "test": "recall",
                        "question": "q",
                        "expect_any": ["x"],
                        "invented_any": ["b"],
                    }
                ]
            )
        )


def test_find_probe_set_file_resolution(tmp_path: Path) -> None:
    def _json(probe_set_id: str) -> str:
        return json.dumps(_payload(probe_set_id=probe_set_id))

    v2 = tmp_path / "v2-four-scopes-wide.json"
    v3 = tmp_path / "v3-50-probes.json"
    v2.write_text(_json("v2_four_scopes_wide"), encoding="utf-8")
    v3.write_text(_json("v3_50_probes"), encoding="utf-8")

    assert find_probe_set_file(tmp_path, "v2_four_scopes_wide") == v2

    with pytest.raises(ProbeSetError, match="unknown"):
        find_probe_set_file(tmp_path, "unknown")

    (tmp_path / "dup1.json").write_text(_json("dup"), encoding="utf-8")
    (tmp_path / "dup2.json").write_text(_json("dup"), encoding="utf-8")
    with pytest.raises(ProbeSetError, match="dup"):
        find_probe_set_file(tmp_path, "dup")
