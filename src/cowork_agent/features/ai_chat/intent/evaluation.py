"""Metadata-only evaluation metrics and launch gate for V3-M4 routing."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil


@dataclass(frozen=True, slots=True)
class ChatRoutingEvalResult:
    case_id: str
    expected_needs_rag: bool
    predicted_needs_rag: bool
    expected_route: str
    predicted_route: str
    latency_ms: int
    reason_codes: tuple[str, ...] = ()
    classifier_retried: bool = False
    fallback_used: bool = False


@dataclass(frozen=True, slots=True)
class ChatRoutingMetrics:
    retrieval_recall: float
    retrieval_precision: float
    missed_rag_rate: float
    classifier_p95_ms: int
    missed_case_ids: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return (
            self.retrieval_recall >= 0.95
            and self.retrieval_precision >= 0.75
            and self.missed_rag_rate <= 0.05
            and self.classifier_p95_ms <= 1500
        )


def compute_chat_routing_metrics(
    results: Sequence[ChatRoutingEvalResult],
) -> ChatRoutingMetrics:
    if not results:
        raise ValueError("chat routing evaluation requires at least one result")
    expected_rag = tuple(result for result in results if result.expected_needs_rag)
    predicted_rag = tuple(result for result in results if result.predicted_needs_rag)
    true_positive = sum(result.expected_needs_rag for result in predicted_rag)
    missed = tuple(result.case_id for result in expected_rag if not result.predicted_needs_rag)
    latencies = sorted(result.latency_ms for result in results)
    p95_index = max(0, ceil(len(latencies) * 0.95) - 1)
    recall = true_positive / len(expected_rag) if expected_rag else 1.0
    precision = true_positive / len(predicted_rag) if predicted_rag else 1.0
    return ChatRoutingMetrics(
        retrieval_recall=round(recall, 4),
        retrieval_precision=round(precision, 4),
        missed_rag_rate=round(len(missed) / len(expected_rag), 4) if expected_rag else 0,
        classifier_p95_ms=latencies[p95_index],
        missed_case_ids=missed,
    )
