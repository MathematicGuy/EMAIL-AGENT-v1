"""Tests for V2-M6 paired evaluation runner.

Production module: cowork_agent.features.ai_chat.evaluation_runner
"""

from __future__ import annotations

import pytest

from cowork_agent.features.ai_chat.evaluation import (
    LaunchThresholds,
    PairedEvaluationCase,
    PairedEvaluationReport,
    evaluate_launch_gate,
)
from cowork_agent.features.ai_chat.evaluation_runner import (
    PairedCaseScores,
    run_paired_evaluation,
)

pytestmark = pytest.mark.extended

# ---------------------------------------------------------------------------
# Thresholds fixture
# ---------------------------------------------------------------------------

# PRODUCT-OWNER APPROVAL REQUIRED — placeholder values, not committed policy
_PLACEHOLDER_THRESHOLDS = LaunchThresholds(
    minimum_continuity_delta=0.0,
    minimum_grounded_delta=0.0,
    minimum_citation_delta=0.0,
    minimum_continuity_score=0.0,
    minimum_grounded_score=0.0,
    minimum_citation_score=0.0,
    maximum_degradation_rate=1.0,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _DeterministicScorer:
    """Two-case deterministic scorer for hand-computed assertions."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[PairedCaseScores, PairedCaseScores]] = {
            "alpha": (
                PairedCaseScores(0.4, 0.5, 0.6),
                PairedCaseScores(0.6, 0.8, 0.9),
            ),
            "beta": (
                PairedCaseScores(0.6, 0.7, 0.8),
                PairedCaseScores(0.7, 0.9, 1.0),
            ),
        }

    def score(self, case_id: str, *, memory_enabled: bool) -> PairedCaseScores:
        disabled, enabled = self._data[case_id]
        return enabled if memory_enabled else disabled


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_duplicate_case_ids_rejected_by_from_cases() -> None:
    case_a = PairedEvaluationCase("a", 0.5, 0.6, 0.5, 0.6, 0.5, 0.6)
    case_a_dup = PairedEvaluationCase("a", 0.4, 0.7, 0.4, 0.7, 0.4, 0.7)
    with pytest.raises(ValueError):
        PairedEvaluationReport.from_cases((case_a, case_a_dup))


def test_runner_returns_sorted_ids_and_exact_means() -> None:
    scorer = _DeterministicScorer()
    report = run_paired_evaluation(("beta", "alpha"), scorer)

    # case_ids sorted
    assert report.case_ids == ("alpha", "beta")

    # Hand-computed deltas:
    # alpha: continuity_delta=0.2, grounded=0.3, citation=0.3
    # beta:  continuity_delta=0.1, grounded=0.2, citation=0.2
    # mean:  continuity=0.15,      grounded=0.25, citation=0.25
    assert report.mean_continuity_delta == pytest.approx(0.15)
    assert report.mean_grounded_delta == pytest.approx(0.25)
    assert report.mean_citation_delta == pytest.approx(0.25)

    # Enabled means:
    # alpha: continuity=0.6, grounded=0.8, citation=0.9
    # beta:  continuity=0.7, grounded=0.9, citation=1.0
    # mean:  continuity=0.65, grounded=0.85, citation=0.95
    assert report.mean_continuity_enabled == pytest.approx(0.65)
    assert report.mean_grounded_enabled == pytest.approx(0.85)
    assert report.mean_citation_enabled == pytest.approx(0.95)


def test_scorer_mismatched_case_set_raises() -> None:
    class _WrongIdScorer:
        """Scorer that only knows about 'real_case' — raises for others."""

        def score(
            self, case_id: str, *, memory_enabled: bool
        ) -> PairedCaseScores:
            if case_id != "real_case":
                raise ValueError(f"unknown case_id: {case_id}")
            return PairedCaseScores(0.5, 0.5, 0.5)

    with pytest.raises(ValueError):
        run_paired_evaluation(("missing_case",), _WrongIdScorer())


@pytest.mark.parametrize(
    ("safety_kwarg", "expected_code"),
    [
        ("unvalidated_retrievals", "hard_safety_unvalidated_retrieval"),
        ("cross_tenant_incidents", "hard_safety_cross_tenant"),
        ("raw_email_memory_violations", "hard_safety_raw_email"),
        ("expired_record_retrievals", "hard_safety_expired_record"),
        ("rejected_retrievals", "hard_safety_rejected_retrieval"),
    ],
)
def test_hard_safety_counters_fail_closed(
    safety_kwarg: str, expected_code: str
) -> None:
    scorer = _DeterministicScorer()
    report = run_paired_evaluation(
        ("alpha", "beta"), scorer, **{safety_kwarg: 1}
    )
    result = evaluate_launch_gate(report, _PLACEHOLDER_THRESHOLDS)
    assert result.passed is False
    assert expected_code in result.reason_codes


def test_fail_closed_continuity_delta_threshold() -> None:
    # Thresholds with minimum_continuity_delta=0.5 vs zero deltas
    strict = LaunchThresholds(
        minimum_continuity_delta=0.5,
        minimum_grounded_delta=0.0,
        minimum_citation_delta=0.0,
        minimum_continuity_score=0.0,
        minimum_grounded_score=0.0,
        minimum_citation_score=0.0,
        maximum_degradation_rate=1.0,
    )

    class _ZeroDeltaScorer:
        def score(self, case_id: str, *, memory_enabled: bool) -> PairedCaseScores:
            return PairedCaseScores(0.5, 0.5, 0.5)

    report = run_paired_evaluation(("a",), _ZeroDeltaScorer())
    result = evaluate_launch_gate(report, strict)
    assert result.passed is False
    assert "continuity_delta" in result.reason_codes


def test_reproducibility_two_runs_identical() -> None:
    scorer = _DeterministicScorer()
    report_a = run_paired_evaluation(("alpha", "beta"), scorer)
    report_b = run_paired_evaluation(("alpha", "beta"), scorer)
    assert report_a.to_dict() == report_b.to_dict()
