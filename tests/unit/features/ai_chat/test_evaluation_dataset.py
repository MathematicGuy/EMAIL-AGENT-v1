"""Tests for evaluation dataset invariants and deterministic scorer."""

from __future__ import annotations

import pytest

from cowork_agent.features.ai_chat.evaluation_dataset import (
    DATASET_VERSION,
    SYNTHETIC_DATASET,
    DeterministicPairedScorer,
)

pytestmark = pytest.mark.extended


def test_dataset_version_constant() -> None:
    assert DATASET_VERSION == "1.0.0"


def test_dataset_has_entries() -> None:
    assert len(SYNTHETIC_DATASET) >= 1


def test_dataset_opaque_ids() -> None:
    for entry in SYNTHETIC_DATASET:
        assert entry.case_id
        assert len(entry.case_id) <= 64
        assert entry.case_id.replace("_", "").isalnum()


def test_dataset_no_duplicate_ids() -> None:
    ids = [entry.case_id for entry in SYNTHETIC_DATASET]
    assert len(ids) == len(set(ids))


def test_dataset_labels_are_metadata_only() -> None:
    """Labels are bool flags — no raw content, no PII."""
    for entry in SYNTHETIC_DATASET:
        assert isinstance(entry.labels.expects_episodic_context, bool)
        assert isinstance(entry.labels.expects_company_evidence, bool)
        assert isinstance(entry.labels.expects_preference_context, bool)


def test_deterministic_scorer_reproducibility() -> None:
    """Two independent instantiations produce identical scores for every case."""
    scorer_a = DeterministicPairedScorer()
    scorer_b = DeterministicPairedScorer()
    for entry in SYNTHETIC_DATASET:
        for memory_enabled in (False, True):
            scores_a = scorer_a.score(entry.case_id, memory_enabled=memory_enabled)
            scores_b = scorer_b.score(entry.case_id, memory_enabled=memory_enabled)
            assert scores_a == scores_b


def test_deterministic_scorer_scores_in_range() -> None:
    scorer = DeterministicPairedScorer()
    for entry in SYNTHETIC_DATASET:
        for memory_enabled in (False, True):
            scores = scorer.score(entry.case_id, memory_enabled=memory_enabled)
            assert 0.0 <= scores.continuity <= 1.0
            assert 0.0 <= scores.grounded <= 1.0
            assert 0.0 <= scores.citation <= 1.0


def test_deterministic_scorer_unknown_case_raises() -> None:
    scorer = DeterministicPairedScorer()
    with pytest.raises(ValueError):
        scorer.score("nonexistent_case", memory_enabled=False)
