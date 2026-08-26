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
    expected_needs_tool: bool
    predicted_needs_tool: bool
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
    tool_recall: float
    tool_precision: float
    missed_tool_case_ids: tuple[str, ...]
    false_tool_case_ids: tuple[str, ...]
    rag_tool_downgraded_case_ids: tuple[str, ...]

    @property
    def passed(self) -> bool:
        """The launch gate.

        The tool axis is gated on precision only, and deliberately so. A false
        tool positive is a turn that never asked for an action being routed at
        one, which is the direction that writes to a real calendar; a missed
        tool case costs a plain chat reply. `missed_tool_case_ids` is reported
        so a regression there is still visible, but two labelled tool cases is
        too small a denominator to gate a recall ratio on -- one borderline
        classification would red the whole benchmark, most of which is about
        retrieval. Tighten this when the tool block grows.
        """

        return (
            self.retrieval_recall >= 0.95
            and self.retrieval_precision >= 0.75
            and self.missed_rag_rate <= 0.05
            and self.classifier_p95_ms <= 1500
            and self.tool_precision >= 1.0
        )


def compute_chat_routing_metrics(
    results: Sequence[ChatRoutingEvalResult],
) -> ChatRoutingMetrics:
    if not results:
        raise ValueError("chat routing evaluation requires at least one result")
    downgraded = tuple(
        result.case_id for result in results if _is_rag_tool_downgrade(result)
    )
    # A case labelled as needing both retrieval and a tool is routed to `TOOL`
    # with the retrieval half dropped -- that is `finalize_route`'s deliberate
    # RAG_TOOL downgrade (PROGRESS.md F1), not a classifier miss. Scoring it as
    # a missed retrieval would put a permanent entry in `missed_case_ids` that
    # every reader has to learn to ignore. It is excluded from the retrieval
    # metrics and named in its own field instead, so the gap stays counted.
    scored = tuple(result for result in results if not _is_rag_tool_downgrade(result))
    expected_rag = tuple(result for result in scored if result.expected_needs_rag)
    predicted_rag = tuple(result for result in scored if result.predicted_needs_rag)
    true_positive = sum(result.expected_needs_rag for result in predicted_rag)
    missed = tuple(result.case_id for result in expected_rag if not result.predicted_needs_rag)
    latencies = sorted(result.latency_ms for result in results)
    p95_index = max(0, ceil(len(latencies) * 0.95) - 1)
    recall = true_positive / len(expected_rag) if expected_rag else 1.0
    precision = true_positive / len(predicted_rag) if predicted_rag else 1.0
    expected_tool = tuple(result for result in results if result.expected_needs_tool)
    predicted_tool = tuple(result for result in results if result.predicted_needs_tool)
    tool_true_positive = sum(result.expected_needs_tool for result in predicted_tool)
    return ChatRoutingMetrics(
        retrieval_recall=round(recall, 4),
        retrieval_precision=round(precision, 4),
        missed_rag_rate=round(len(missed) / len(expected_rag), 4) if expected_rag else 0,
        classifier_p95_ms=latencies[p95_index],
        missed_case_ids=missed,
        tool_recall=round(
            tool_true_positive / len(expected_tool) if expected_tool else 1.0, 4
        ),
        tool_precision=round(
            tool_true_positive / len(predicted_tool) if predicted_tool else 1.0, 4
        ),
        missed_tool_case_ids=tuple(
            result.case_id for result in expected_tool if not result.predicted_needs_tool
        ),
        false_tool_case_ids=tuple(
            result.case_id for result in predicted_tool if not result.expected_needs_tool
        ),
        rag_tool_downgraded_case_ids=downgraded,
    )


def _is_rag_tool_downgrade(result: ChatRoutingEvalResult) -> bool:
    """Was retrieval dropped because the router kept the tool half?

    Derived from the labels rather than a reason code: the router emits none
    for this path today, and the fixture is the thing that knows the request
    genuinely wanted both.
    """

    return (
        result.expected_needs_rag
        and result.expected_needs_tool
        and result.predicted_needs_tool
        and not result.predicted_needs_rag
    )
