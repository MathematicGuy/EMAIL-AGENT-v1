"""Paired evaluation runner: scores both variants per case and builds report.

Provides the production ``run_paired_evaluation`` entry point and the
``thresholds_from_env`` loader that reads product-owner-approved launch
thresholds from environment variables with NO numeric defaults.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from .evaluation import LaunchThresholds, PairedEvaluationCase, PairedEvaluationReport


@dataclass(frozen=True)
class PairedCaseScores:
    """Per-axis scores for a single case variant."""

    continuity: float
    grounded: float
    citation: float


class PairedScorer(Protocol):
    """Protocol for scoring a case under both memory variants."""

    def score(self, case_id: str, *, memory_enabled: bool) -> PairedCaseScores: ...


def run_paired_evaluation(
    case_ids: tuple[str, ...],
    scorer: PairedScorer,
    **safety: int,
) -> PairedEvaluationReport:
    """Score both variants per case and build the paired evaluation report.

    The scorer is expected to raise ``ValueError`` for unknown case ids.
    Cases are passed to ``PairedEvaluationReport.from_cases`` which handles
    sorting and deduplication.
    """
    cases: list[PairedEvaluationCase] = []
    for cid in case_ids:
        disabled = scorer.score(cid, memory_enabled=False)
        enabled = scorer.score(cid, memory_enabled=True)
        cases.append(
            PairedEvaluationCase(
                case_id=cid,
                memory_disabled=disabled.continuity,
                memory_enabled=enabled.continuity,
                grounded_disabled=disabled.grounded,
                grounded_enabled=enabled.grounded,
                citation_disabled=disabled.citation,
                citation_enabled=enabled.citation,
            )
        )
    return PairedEvaluationReport.from_cases(tuple(cases), **safety)


_ENV_VAR_NAMES: tuple[str, ...] = (
    "EVAL_MIN_CONTINUITY_DELTA",
    "EVAL_MIN_GROUNDED_DELTA",
    "EVAL_MIN_CITATION_DELTA",
    "EVAL_MIN_CONTINUITY_SCORE",
    "EVAL_MIN_GROUNDED_SCORE",
    "EVAL_MIN_CITATION_SCORE",
    "EVAL_MAX_DEGRADATION_RATE",
)

_THRESHOLD_FIELD_NAMES: tuple[str, ...] = (
    "minimum_continuity_delta",
    "minimum_grounded_delta",
    "minimum_citation_delta",
    "minimum_continuity_score",
    "minimum_grounded_score",
    "minimum_citation_score",
    "maximum_degradation_rate",
)


def thresholds_from_env(
    environ: Mapping[str, str] = os.environ,
) -> LaunchThresholds:
    """Read launch thresholds from environment variables.

    ANY missing or unparsable variable raises ``ValueError``.
    NO numeric defaults are provided — product-owner approval is required
    before enabling launch gates.
    """
    values: dict[str, float] = {}
    for env_name, field_name in zip(_ENV_VAR_NAMES, _THRESHOLD_FIELD_NAMES, strict=True):
        raw = environ.get(env_name)
        if raw is None:
            raise ValueError(
                "launch thresholds require explicit product-approved configuration"
            )
        try:
            values[field_name] = float(raw)
        except (TypeError, ValueError):
            raise ValueError(
                "launch thresholds require explicit product-approved configuration"
            ) from None
    return LaunchThresholds(**values)
