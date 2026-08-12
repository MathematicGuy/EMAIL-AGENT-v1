"""Metadata-only paired memory evaluation and fail-closed launch gate."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


def _score(value: float, name: str) -> None:
    if (
        isinstance(value, bool) or not isinstance(value, (int, float))
        or not isfinite(value) or not 0 <= value <= 1
    ):
        raise ValueError(f"{name} must be a finite score in [0, 1]")


@dataclass(frozen=True, slots=True)
class PairedEvaluationCase:
    case_id: str
    memory_disabled: float
    memory_enabled: float
    grounded_disabled: float
    grounded_enabled: float
    citation_disabled: float
    citation_enabled: float

    def __post_init__(self) -> None:
        if (
            not self.case_id or len(self.case_id) > 64
            or not self.case_id.replace("_", "").isalnum()
        ):
            raise ValueError("case_id must be a safe opaque identifier")
        for name in (
            "memory_disabled", "memory_enabled", "grounded_disabled", "grounded_enabled",
            "citation_disabled", "citation_enabled",
        ):
            _score(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class LaunchThresholds:
    minimum_continuity_delta: float
    minimum_grounded_delta: float
    minimum_citation_delta: float
    minimum_continuity_score: float
    minimum_grounded_score: float
    minimum_citation_score: float
    maximum_degradation_rate: float

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            _score(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class PairedEvaluationReport:
    case_ids: tuple[str, ...]
    mean_continuity_delta: float
    mean_grounded_delta: float
    mean_citation_delta: float
    mean_continuity_enabled: float
    mean_grounded_enabled: float
    mean_citation_enabled: float
    degradation_rate: float
    unvalidated_retrievals: int = 0
    cross_tenant_incidents: int = 0
    raw_email_memory_violations: int = 0
    expired_record_retrievals: int = 0
    rejected_retrievals: int = 0

    def __post_init__(self) -> None:
        if (
            not self.case_ids or tuple(sorted(self.case_ids)) != self.case_ids
            or len(set(self.case_ids)) != len(self.case_ids)
        ):
            raise ValueError("case_ids must be nonempty, sorted, and unique")
        for case_id in self.case_ids:
            if not case_id or len(case_id) > 64 or not case_id.replace("_", "").isalnum():
                raise ValueError("case_id must be a safe opaque identifier")
        for name in (
            "mean_continuity_enabled", "mean_grounded_enabled",
            "mean_citation_enabled", "degradation_rate",
        ):
            _score(getattr(self, name), name)
        for name in (
            "mean_continuity_delta", "mean_grounded_delta", "mean_citation_delta"
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not isfinite(value) or not -1 <= value <= 1
            ):
                raise ValueError(f"{name} must be a finite delta in [-1, 1]")
        for name in (
            "unvalidated_retrievals", "cross_tenant_incidents",
            "raw_email_memory_violations", "expired_record_retrievals",
            "rejected_retrievals",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("invalid metadata-only safety count")

    @classmethod
    def from_cases(
        cls, cases: tuple[PairedEvaluationCase, ...], **safety: int
    ) -> PairedEvaluationReport:
        if not cases:
            raise ValueError("paired evaluation requires at least one case")
        ordered = tuple(sorted(cases, key=lambda case: case.case_id))
        if len({case.case_id for case in ordered}) != len(ordered):
            raise ValueError("duplicate case_id")
        allowed_safety = {
            "unvalidated_retrievals", "cross_tenant_incidents",
            "raw_email_memory_violations", "expired_record_retrievals", "rejected_retrievals",
        }
        for name, value in safety.items():
            if name not in allowed_safety or not isinstance(value, int) or value < 0:
                raise ValueError("invalid metadata-only safety count")
        count = len(ordered)
        def mean(values: list[float]) -> float:
            return sum(values) / count
        return cls(
            tuple(case.case_id for case in ordered),
            mean([case.memory_enabled - case.memory_disabled for case in ordered]),
            mean([case.grounded_enabled - case.grounded_disabled for case in ordered]),
            mean([case.citation_enabled - case.citation_disabled for case in ordered]),
            mean([case.memory_enabled for case in ordered]),
            mean([case.grounded_enabled for case in ordered]),
            mean([case.citation_enabled for case in ordered]),
            sum(
                case.memory_enabled < case.memory_disabled
                or case.grounded_enabled < case.grounded_disabled
                or case.citation_enabled < case.citation_disabled
                for case in ordered
            ) / count,
            **safety,
        )

    def to_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class LaunchGateResult:
    passed: bool
    reason_codes: tuple[str, ...]


def evaluate_launch_gate(
    report: PairedEvaluationReport, thresholds: LaunchThresholds
) -> LaunchGateResult:
    checks = (
        (report.mean_continuity_delta < thresholds.minimum_continuity_delta, "continuity_delta"),
        (report.mean_grounded_delta < thresholds.minimum_grounded_delta, "grounded_delta"),
        (report.mean_citation_delta < thresholds.minimum_citation_delta, "citation_delta"),
        (report.mean_continuity_enabled < thresholds.minimum_continuity_score, "continuity_score"),
        (report.mean_grounded_enabled < thresholds.minimum_grounded_score, "grounded_score"),
        (report.mean_citation_enabled < thresholds.minimum_citation_score, "citation_score"),
        (report.degradation_rate > thresholds.maximum_degradation_rate, "degradation_rate"),
        (report.unvalidated_retrievals != 0, "hard_safety_unvalidated_retrieval"),
        (report.cross_tenant_incidents != 0, "hard_safety_cross_tenant"),
        (report.raw_email_memory_violations != 0, "hard_safety_raw_email"),
        (report.expired_record_retrievals != 0, "hard_safety_expired_record"),
        (report.rejected_retrievals != 0, "hard_safety_rejected_retrieval"),
    )
    return LaunchGateResult(
        not any(failed for failed, _ in checks),
        tuple(code for failed, code in checks if failed),
    )
