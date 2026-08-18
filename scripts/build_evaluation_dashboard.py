# ruff: noqa: E501
"""Build the metadata-only retrieval evaluation dashboard from retained JSON reports.

The dashboard is a decision aid, not a second evaluator. It never reads query,
chunk, email, or plan text, and it deliberately distinguishes live semantic
evidence from deterministic hashing smoke results.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = REPO_ROOT / "evaluations" / "baselines"
DEFAULT_OUTPUT = REPO_ROOT / "evaluations" / "dashboard.md"
CURRENT_CASE_COUNT = 100
CURRENT_DOCUMENT_COUNT = 17


@dataclass(frozen=True)
class RetrievalReport:
    """The stable, metadata-only subset needed for the dashboard."""

    source: Path
    generated_at: str
    embedder: str
    retriever: str
    reranker: str | None
    case_count: int
    document_count: int
    chunk_count: int
    section_mrr: float
    section_recall_at_5: float
    semantic_section_mrr: float | None
    abstention_rate: float | None
    latency_p50_ms: int | None
    latency_p95_ms: int | None

    @property
    def scope(self) -> str:
        if self.case_count >= CURRENT_CASE_COUNT and self.document_count >= CURRENT_DOCUMENT_COUNT:
            return "current"
        return "historical"

    @property
    def semantic_evidence(self) -> bool:
        return self.embedder.lower() != "hashing"


def load_reports(input_dir: Path) -> tuple[RetrievalReport, ...]:
    """Load valid retrieval reports, ignoring unrelated or malformed JSON."""
    reports: list[RetrievalReport] = []
    for path in sorted(input_dir.glob("retrieval-eval-*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            report = _parse_report(path, raw)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        reports.append(report)
    return tuple(sorted(reports, key=lambda report: (report.generated_at, report.source.name)))


def _parse_report(path: Path, raw: Any) -> RetrievalReport:
    if not isinstance(raw, dict):
        raise ValueError("report must be an object")
    corpus = _mapping(raw, "corpus")
    section = _mapping(raw, "section_level")
    by_probe = _mapping(raw, "by_probe")
    semantic = _mapping(by_probe, "semantic")
    semantic_section = _mapping(semantic, "section_level")
    abstention = _mapping(raw, "abstention")
    latency = _mapping(raw, "latency_ms")
    reranker = raw.get("reranker")
    if reranker is not None and not isinstance(reranker, str):
        raise ValueError("reranker must be a string or null")
    return RetrievalReport(
        source=path,
        generated_at=_string(raw, "generated_at"),
        embedder=_string(raw, "embedder"),
        retriever=_string(raw, "retriever"),
        reranker=reranker,
        case_count=_integer(raw, "case_count"),
        document_count=_integer(corpus, "document_count"),
        chunk_count=_integer(corpus, "chunk_count"),
        section_mrr=_number(section, "mrr"),
        section_recall_at_5=_number(section, "recall_at_5"),
        semantic_section_mrr=_optional_number(semantic_section, "mrr"),
        abstention_rate=_optional_number(abstention, "abstention_rate"),
        latency_p50_ms=_optional_integer(latency, "p50"),
        latency_p95_ms=_optional_integer(latency, "p95"),
    )


def _mapping(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return value


def _string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _integer(raw: dict[str, Any], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _optional_integer(raw: dict[str, Any], key: str) -> int | None:
    if key not in raw or raw[key] is None:
        return None
    return _integer(raw, key)


def _number(raw: dict[str, Any], key: str) -> float:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number")
    return float(value)


def _optional_number(raw: dict[str, Any], key: str) -> float | None:
    if key not in raw or raw[key] is None:
        return None
    return _number(raw, key)


def render_dashboard(reports: Sequence[RetrievalReport]) -> str:
    """Render a deterministic Markdown dashboard from report metadata."""
    current = tuple(report for report in reports if report.scope == "current")
    historical = tuple(report for report in reports if report.scope == "historical")
    lines = [
        "# Retrieval Evaluation Dashboard",
        "",
        "> Generated from metadata-only JSON under `evaluations/baselines/`. "
        "Do not use hashing runs as semantic-quality or production-latency evidence.",
        "",
        "## Decision Snapshot",
        "",
        _snapshot(current, historical),
        "",
        "## Pipeline",
        "",
        "```mermaid",
        "flowchart LR",
        '    E["Email"] --> R["Route decision"]',
        '    R --> Q["RAG request"]',
        '    Q --> A["ACL and query guard"]',
        '    A --> V["Embedding and vector search"]',
        '    A --> B["BM25 lexical search"]',
        '    V --> F["RRF fusion"]',
        '    B --> F',
        '    F --> J["Optional Jina reranker"]',
        '    J --> K["Threshold, top-k, and citations"]',
        '    K --> G["Plan generation"]',
        '    G --> P["Validation and persistence"]',
        "    style V fill:#216869,color:#fff,stroke:#216869",
        "    style B fill:#216869,color:#fff,stroke:#216869",
        "    style F fill:#8a5a00,color:#fff,stroke:#8a5a00",
        "    style J fill:#8a5a00,color:#fff,stroke:#8a5a00",
        "    style R fill:#8b1a1a,color:#fff,stroke:#8b1a1a",
        "    style G fill:#8b1a1a,color:#fff,stroke:#8b1a1a",
        "```",
        "",
        "Green: exercised by retrieval reports. Amber: exercised only as part of a combined retriever. "
        "Red: no report-local quality or latency evidence.",
        "",
        "## Current Corpus Evidence",
        "",
        _report_table(current) if current else "No current-corpus retrieval report is stored yet.",
        "",
        "## Historical Baselines",
        "",
        _report_table(historical) if historical else "No historical retrieval baseline is stored.",
        "",
        "## Component Performance Map",
        "",
        "| Pipeline component | Latency evidence | Quality evidence | Decision state |",
        "|---|---|---|---|",
        "| Route decision | Not emitted by retrieval reports | Separate routing fixture only | Instrument before blaming retrieval |",
        "| ACL and query guard | Included in retrieval total only | Unit-tested; no per-stage benchmark | Correctness covered, performance unknown |",
        "| Embedding and vector search | Included in retrieval total only | Current hashing runs are mechanical-only | Need live-provider stage timing |",
        "| BM25 lexical search | Included in hybrid total only | Historical lexical slice exists | No independent current latency |",
        "| Hybrid retrieval and RRF fusion | Included in hybrid total only | Historical semantic regression signal | Re-measure on current live corpus |",
        "| Jina reranker | Included in hybrid+rereank total only | Historical aggregate improvement, semantic trade-off | Add applied/fallback and stage timing |",
        "| Threshold and abstention | Included in retrieval total only | Current hashing abstention is 0.000 | Runtime policy remains open |",
        "| Plan generation and citation validation | Not emitted here | No grounded-plan quality evaluation | Instrument and evaluate separately |",
        "",
        "## Bottleneck Readout",
        "",
        _bottleneck_readout(current, historical),
        "",
        "## Refresh Contract",
        "",
        "1. Store every evaluator JSON under `evaluations/`; retrieval reports belong in `baselines/`.",
        "2. Run the relevant evaluator with its default output path, then run `python scripts/build_evaluation_dashboard.py`.",
        "3. Do not compare reports across different corpus/case counts as a release decision.",
        "4. Add `embedding_ms`, `dense_search_ms`, `bm25_ms`, `fusion_ms`, `rerank_ms`, "
        "`post_filter_ms`, routing, and generation timings before assigning a component bottleneck.",
        "",
    ]
    return "\n".join(lines)


def _snapshot(current: Sequence[RetrievalReport], historical: Sequence[RetrievalReport]) -> str:
    if not current:
        return "- **Current corpus:** no report found."
    latest = current[-1]
    semantics = "live semantic evidence present" if any(
        report.semantic_evidence for report in current
    ) else "mechanical-only; live semantic evidence missing"
    return (
        f"- **Current corpus:** {latest.document_count} documents / {latest.chunk_count} chunks, "
        f"{latest.case_count} cases.\n"
        f"- **Current evidence:** {semantics}.\n"
        f"- **Historical comparison reports:** {len(historical)}; retain for context only."
    )


def _report_table(reports: Sequence[RetrievalReport]) -> str:
    lines = [
        "| Report | Embedder | Retriever | Corpus | Section MRR | Semantic MRR | p50 / p95 ms | Evidence |",
        "|---|---|---|---|---:|---:|---:|---|",
    ]
    for report in reports:
        reranker = f" + {report.reranker}" if report.reranker else ""
        evidence = (
            "live semantic" if report.semantic_evidence else "mechanical-only"
        )
        lines.append(
            "| "
            f"`{report.source.name}` | {report.embedder} | {report.retriever}{reranker} | "
            f"{report.case_count} cases / {report.document_count} docs / {report.chunk_count} chunks | "
            f"{report.section_mrr:.4f} | {_decimal(report.semantic_section_mrr)} | "
            f"{_latency(report)} | {evidence} |"
        )
    return "\n".join(lines)


def _decimal(value: float | None) -> str:
    return "-" if value is None else f"{value:.4f}"


def _latency(report: RetrievalReport) -> str:
    if report.latency_p50_ms is None or report.latency_p95_ms is None:
        return "-"
    if report.latency_p50_ms == 0 and report.latency_p95_ms == 0:
        return "0 / 0 (integer-truncated)"
    return f"{report.latency_p50_ms} / {report.latency_p95_ms}"


def _bottleneck_readout(
    current: Sequence[RetrievalReport], historical: Sequence[RetrievalReport]
) -> str:
    covered_retrievers = ", ".join(sorted({report.retriever for report in current}))
    lines = [
        "- **Current bottleneck:** not identifiable from the stored reports. Per-component timing is not emitted; they measure only end-to-end retrieval latency.",
        f"- **Current retrieval coverage:** {covered_retrievers or 'none'}.",
    ]
    if current and not any(report.semantic_evidence for report in current):
        lines.append(
            "- **Current quality limit:** every current report uses hashing embeddings, so rank differences are not semantic evidence."
        )
    chunk_counts = sorted({report.chunk_count for report in current})
    if len(chunk_counts) > 1:
        lines.append(
            "- **Current comparability limit:** reports span multiple chunking cohorts "
            f"({', '.join(str(count) for count in chunk_counts)} chunks); compare only within one cohort."
        )
    live_historical = [report for report in historical if report.semantic_evidence]
    if live_historical:
        lines.append(
            "- **Historical trade-off:** live six-document reports retain useful context, but cannot select the current default."
        )
    lines.append(
        "- **Highest-value next measurement:** emit per-component timings from one current live dense/hybrid/rerank run."
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    reports = load_reports(args.input_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_dashboard(reports), encoding="utf-8")
    print(f"Dashboard: {args.output} ({len(reports)} retrieval report(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
