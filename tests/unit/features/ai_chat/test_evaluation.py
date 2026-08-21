import math

import pytest

from cowork_agent.features.ai_chat.evaluation import (
    LaunchThresholds,
    PairedEvaluationCase,
    PairedEvaluationReport,
    evaluate_launch_gate,
)


def _case(case_id: str, disabled: float, enabled: float) -> PairedEvaluationCase:
    return PairedEvaluationCase(
        case_id=case_id,
        memory_disabled=disabled,
        memory_enabled=enabled,
        grounded_disabled=disabled,
        grounded_enabled=enabled,
        citation_disabled=disabled,
        citation_enabled=enabled,
    )


def _thresholds() -> LaunchThresholds:
    return LaunchThresholds(0.01, 0.01, 0.01, 0.5, 0.5, 0.5, 0.25)


def test_paired_report_is_stable_and_gate_accepts_improvement() -> None:
    report = PairedEvaluationReport.from_cases((_case("b", 0.6, 0.8), _case("a", 0.7, 0.9)))
    result = evaluate_launch_gate(report, _thresholds())

    assert report.case_ids == ("a", "b")
    assert report.mean_continuity_delta == pytest.approx(0.2)
    assert result.passed is True
    assert result.reason_codes == ()


def test_gate_rejects_regression_and_hard_safety_incidents() -> None:
    report = PairedEvaluationReport.from_cases(
        (_case("a", 0.8, 0.6),), unvalidated_retrievals=1
    )
    result = evaluate_launch_gate(report, _thresholds())

    assert result.passed is False
    assert "continuity_delta" in result.reason_codes
    assert "hard_safety_unvalidated_retrieval" in result.reason_codes


def test_grounding_regression_and_every_hard_safety_counter_fail_closed() -> None:
    case = PairedEvaluationCase("a", 0.5, 0.7, 0.8, 0.6, 0.5, 0.7)
    report = PairedEvaluationReport.from_cases(
        (case,), unvalidated_retrievals=1, rejected_retrievals=1,
        cross_tenant_incidents=1, raw_email_memory_violations=1, expired_record_retrievals=1,
    )
    result = evaluate_launch_gate(report, _thresholds())
    assert report.degradation_rate == 1
    assert set(result.reason_codes) >= {
        "hard_safety_unvalidated_retrieval", "hard_safety_rejected_retrieval",
        "hard_safety_cross_tenant", "hard_safety_raw_email", "hard_safety_expired_record",
    }


def test_contract_rejects_unpaired_duplicate_nonfinite_and_content_fields() -> None:
    with pytest.raises(ValueError):
        PairedEvaluationReport.from_cases((_case("a", 0.5, 0.6), _case("a", 0.5, 0.6)))
    with pytest.raises(ValueError):
        _case("a", math.nan, 0.5)
    with pytest.raises(ValueError):
        _case("a", True, 0.5)
    with pytest.raises(ValueError):
        PairedEvaluationReport.from_cases(())
    with pytest.raises(ValueError):
        LaunchThresholds(-0.1, 0, 0, 0, 0, 0, 0)
    serialized = PairedEvaluationReport.from_cases((_case("a", 0.5, 0.6),)).to_dict()
    assert {"prompt", "user_message", "tenant_id", "session_id", "url"}.isdisjoint(serialized)
