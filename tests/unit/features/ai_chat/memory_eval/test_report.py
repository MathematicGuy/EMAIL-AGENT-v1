from __future__ import annotations

import json
from datetime import UTC, datetime

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval.probes import (
    Probe,
    ProbeSet,
    ProbeTest,
    SeedSpec,
)
from cowork_agent.features.ai_chat.memory_eval.report import ProbeRow, build_report
from cowork_agent.features.ai_chat.memory_eval.scoring import Outcome

_SECRET_QUESTION = "what is the unmistakable secret deadline"


def _probe_set() -> ProbeSet:
    return ProbeSet(
        schema_version="2.0.0",
        probe_set_id="unit",
        label="unit",
        seed=SeedSpec(("a seeded sentence",), {}, (), None),
        probes=(
            Probe(
                probe_id="ep_recall_01",
                targets=MemoryType.EPISODIC,
                test=ProbeTest.RECALL,
                question=_SECRET_QUESTION,
                expect_any=("x",),
            ),
        ),
    )


def _row(**overrides: object) -> ProbeRow:
    defaults: dict[str, object] = {
        "probe_id": "ep_recall_01",
        "targets": MemoryType.EPISODIC,
        "test": ProbeTest.RECALL,
        "full": Outcome.PASS,
        "ablated": Outcome.MISS,
        "control": Outcome.MISS,
        "certain": True,
        "latency_ms": 1840,
    }
    defaults.update(overrides)
    return ProbeRow(**defaults)  # type: ignore[arg-type]


def _report(**kwargs: object) -> dict[str, object]:
    return build_report(
        _probe_set(),
        [_row()],
        provider="gemini",
        model="model-id",
        run_key="a1b2c3d4e5f6",
        ran_at=datetime(2026, 8, 18, tzinfo=UTC),
        **kwargs,  # type: ignore[arg-type]
    )


def test_report_carries_schema_version_and_provenance() -> None:
    report = _report()
    assert report["schema_version"] == "2.0.0"
    assert report["probe_set_id"] == "unit"
    assert report["provider"] == "gemini"
    assert report["model"] == "model-id"
    assert report["run_key"] == "a1b2c3d4e5f6"


def test_report_contains_no_probe_or_seed_text() -> None:
    # The rule from evaluations/HARNESS-GUIDE.md, enforced rather than trusted.
    serialized = json.dumps(_report())
    assert _SECRET_QUESTION not in serialized
    assert "a seeded sentence" not in serialized


def test_verdict_row_is_derived_from_the_three_arms() -> None:
    verdicts = _report()["verdicts"]
    assert isinstance(verdicts, list)
    assert verdicts[0]["verdict"] == "scope_earned_it"
    assert verdicts[0]["probe"] == "ep_recall_01"
    assert verdicts[0]["latency_ms"] == 1840


def test_per_scope_counts_every_scope_even_when_unprobed() -> None:
    per_scope = _report()["per_scope"]
    assert isinstance(per_scope, dict)
    assert set(per_scope) == {"short_term", "long_term", "episodic", "semantic"}
    assert per_scope["episodic"]["probes"] == 1
    assert per_scope["episodic"]["earned_it"] == 1
    assert per_scope["short_term"]["probes"] == 0


def test_leaked_probes_are_named() -> None:
    report = build_report(
        _probe_set(),
        [_row(control=Outcome.PASS)],
        provider="gemini",
        model="m",
        run_key="k",
        ran_at=datetime(2026, 8, 18, tzinfo=UTC),
    )
    assert report["leaked_probes"] == ["ep_recall_01"]


def test_needs_reading_counts_uncertain_rows() -> None:
    report = build_report(
        _probe_set(),
        [_row(certain=False)],
        provider="gemini",
        model="m",
        run_key="k",
        ran_at=datetime(2026, 8, 18, tzinfo=UTC),
    )
    assert report["needs_reading"] == 1


def test_verdicts_are_sorted_worst_first() -> None:
    probe_set = _probe_set()
    extra = Probe(
        probe_id="ep_recall_02",
        targets=MemoryType.EPISODIC,
        test=ProbeTest.RECALL,
        question="q2",
        expect_any=("x",),
    )
    probe_set = ProbeSet(
        probe_set.schema_version,
        probe_set.probe_set_id,
        probe_set.label,
        probe_set.seed,
        (*probe_set.probes, extra),
    )
    report = build_report(
        probe_set,
        [_row(), _row(probe_id="ep_recall_02", full=Outcome.STALE)],
        provider="gemini",
        model="m",
        run_key="k",
        ran_at=datetime(2026, 8, 18, tzinfo=UTC),
    )
    verdicts = report["verdicts"]
    assert isinstance(verdicts, list)
    assert verdicts[0]["verdict"] == "dangerous"
