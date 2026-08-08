"""Evaluate retrieval quality over the labeled RAG golden set.

SPEC-rag §6 obligation: run after EVERY change to chunking, embeddings, or
the retriever stack. Measures Hit@1, Hit@3, MRR and Recall@5 at two
granularities — document level (primary, stable across re-chunking) and
section level (the discriminating metric that gates) — plus abstention over
the `unanswerable` probes and p50/p95 latency.

Every metric is emitted overall AND sliced by probe type. That slice is the
deliverable: with six topically disjoint documents, document-level Hit@1
saturates for any retriever, so an aggregate alone cannot tell dense, BM25
and hybrid apart. An aggregate that improves while `semantic` regresses is a
failure, and only the sliced report can see it.

All three retrievers — dense, bm25 and hybrid — flow through one measurement
path, so their reports are directly comparable.

Runs offline and deterministically with the HashingEmbedder; skips
gracefully when the live embedder has no API key. The report stores case ids,
document ids, section counts and statistics only — never query text or chunk
text.

Regenerate: python scripts/evaluate_retrieval.py --embedder gemini --retriever hybrid
Smoke test: python scripts/evaluate_retrieval.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import math
import os
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

if TYPE_CHECKING:
    from cowork_agent.domain.target_contracts import (
        SemanticRetrievalRequest,
        SemanticRetrievalResponse,
    )
    from cowork_agent.integrations.rag.embeddings import EmbeddingPort
    from cowork_agent.integrations.rag.jina_reranker import RerankerPort
    from cowork_agent.integrations.rag.knowledge_base import KnowledgeChunk, KnowledgeDocument

REPO_ROOT = Path(__file__).resolve().parents[1]
LOADER_PATH = REPO_ROOT / "tests" / "fixtures" / "rag" / "loader.py"
DEFAULT_FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "rag" / "retrieval_golden.json"
DEFAULT_CORPUS_DIR = REPO_ROOT / "data" / "extracted"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "baselines"

#: Tenant stamp for the corpus and the retrieval filter (identity.LOCAL_TENANT_ID).
TENANT_ID = "local"

#: Relevance granularities (SPEC §6.2).
DOCUMENT_LEVEL = "document"
SECTION_LEVEL = "section"
LEVELS: tuple[str, ...] = (DOCUMENT_LEVEL, SECTION_LEVEL)

#: Retrieval stacks under measurement (SPEC §4).
DENSE = "dense"
BM25 = "bm25"
HYBRID = "hybrid"
RETRIEVERS: tuple[str, ...] = (DENSE, BM25, HYBRID)

#: RetrievalStatus.NO_RESULTS, duplicated so the metric layer imports nothing.
NO_RESULTS_STATUS = "no_results"


# --------------------------------------------------------------------------
# Pure: no corpus, no embedder, no filesystem. Unit-tested directly.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseResult:
    """One case's retrieval outcome; identifiers and metrics only, never text."""

    case_id: str
    probe: str
    expected_document_ids: tuple[str, ...]
    expected_sections: tuple[str, ...]
    returned_document_ids: tuple[str, ...]
    returned_sections: tuple[str | None, ...]
    retrieval_status: str
    latency_ms: int


def rank_of_first_relevant(result: CaseResult, *, level: str) -> int | None:
    """1-based rank of the first relevant result, or None when there is none.

    At document level a result is relevant when its document id is expected.
    At section level the section must match too, which is strictly stronger:
    the right document under the wrong heading is a document-level hit and a
    section-level miss.
    """
    _check_level(level)
    for position, (document_id, section) in enumerate(_ranked_pairs(result)):
        if document_id not in result.expected_document_ids:
            continue
        if level == SECTION_LEVEL and (section is None or section not in result.expected_sections):
            continue
        return position + 1
    return None


def reciprocal_rank(rank: int | None) -> float:
    """1/rank, contributing 0.0 for a miss rather than being dropped."""
    return 0.0 if rank is None else 1.0 / rank


def hit_at_k(results: Sequence[CaseResult], k: int, *, level: str) -> float:
    """Fraction of scored cases with at least one relevant result in the top k."""
    scored = scored_cases(results, level=level)
    if not scored:
        return 0.0
    hits = sum(
        1
        for result in scored
        if (rank := rank_of_first_relevant(result, level=level)) is not None and rank <= k
    )
    return round(hits / len(scored), 4)


def mean_reciprocal_rank(results: Sequence[CaseResult], *, level: str) -> float:
    """Mean of 1/rank over scored cases; a miss contributes 0.0."""
    scored = scored_cases(results, level=level)
    if not scored:
        return 0.0
    total = sum(
        reciprocal_rank(rank_of_first_relevant(result, level=level)) for result in scored
    )
    return round(total / len(scored), 4)


def recall_at_k(results: Sequence[CaseResult], k: int, *, level: str) -> float:
    """Mean fraction of a case's expected labels present in the top k."""
    scored = scored_cases(results, level=level)
    if not scored:
        return 0.0
    total = 0.0
    for result in scored:
        expected = _expected_labels(result, level=level)
        total += len(_found_labels(result, k, level=level)) / len(expected)
    return round(total / len(scored), 4)


def scored_cases(results: Sequence[CaseResult], *, level: str) -> tuple[CaseResult, ...]:
    """Cases that participate in ranked metrics at this level.

    `unanswerable` cases carry no expected documents and are never scored
    here — they are covered by `abstention_stats`. Section level additionally
    drops cases with no `expected_sections`; the report publishes how many
    (`excluded_case_count`) so the number is never silently different.
    """
    _check_level(level)
    answerable = tuple(result for result in results if result.expected_document_ids)
    if level == SECTION_LEVEL:
        return tuple(result for result in answerable if result.expected_sections)
    return answerable


def excluded_case_count(results: Sequence[CaseResult]) -> int:
    """Answerable cases dropped from section-level metrics for lacking labels."""
    return len(scored_cases(results, level=DOCUMENT_LEVEL)) - len(
        scored_cases(results, level=SECTION_LEVEL)
    )


def level_metrics(results: Sequence[CaseResult], *, level: str) -> dict[str, float | int]:
    """The four SPEC §6.2 metrics at one granularity."""
    metrics: dict[str, float | int] = {
        "hit_at_1": hit_at_k(results, 1, level=level),
        "hit_at_3": hit_at_k(results, 3, level=level),
        "mrr": mean_reciprocal_rank(results, level=level),
        "recall_at_5": recall_at_k(results, 5, level=level),
    }
    if level == SECTION_LEVEL:
        metrics["excluded_case_count"] = excluded_case_count(results)
    return metrics


def aggregate(results: Sequence[CaseResult]) -> dict[str, Any]:
    """Overall metrics plus the by-probe and by-document slices."""
    answerable = scored_cases(results, level=DOCUMENT_LEVEL)
    by_probe: dict[str, Any] = {}
    for probe in sorted({result.probe for result in answerable}):
        subset = [result for result in answerable if result.probe == probe]
        by_probe[probe] = {
            "case_count": len(subset),
            "document_level": level_metrics(subset, level=DOCUMENT_LEVEL),
            "section_level": level_metrics(subset, level=SECTION_LEVEL),
        }
    by_document: dict[str, Any] = {}
    expected_ids = {
        document_id for result in answerable for document_id in result.expected_document_ids
    }
    for document_id in sorted(expected_ids):
        subset = [
            result for result in answerable if document_id in result.expected_document_ids
        ]
        by_document[document_id] = {
            "case_count": len(subset),
            "mrr": mean_reciprocal_rank(subset, level=DOCUMENT_LEVEL),
            "section_mrr": mean_reciprocal_rank(subset, level=SECTION_LEVEL),
        }
    return {
        "document_level": level_metrics(results, level=DOCUMENT_LEVEL),
        "section_level": level_metrics(results, level=SECTION_LEVEL),
        "by_probe": by_probe,
        "by_document": by_document,
    }


def abstention_stats(results: Sequence[CaseResult]) -> dict[str, Any]:
    """Abstention over `unanswerable` cases only (SPEC §6.2).

    A case abstains when the status is NO_RESULTS **or** no chunk came back;
    both are correct behaviour. Only confidently returning a chunk for a
    query the corpus cannot answer is a failure, and those cases are named.
    """
    unanswerable = [result for result in results if not result.expected_document_ids]
    abstained = [result for result in unanswerable if _abstained(result)]
    false_answers = [result.case_id for result in unanswerable if not _abstained(result)]
    rate = round(len(abstained) / len(unanswerable), 4) if unanswerable else None
    return {
        "case_count": len(unanswerable),
        "abstention_rate": rate,
        "false_answer_case_ids": false_answers,
    }


def latency_percentiles(results: Sequence[CaseResult]) -> dict[str, int]:
    """p50/p95 of per-case latency. Reported, never gated."""
    values = sorted(result.latency_ms for result in results)
    if not values:
        return {"p50": 0, "p95": 0}
    return {"p50": _percentile(values, 50), "p95": _percentile(values, 95)}


def miss_report(results: Sequence[CaseResult]) -> list[dict[str, Any]]:
    """Document-level misses — ids only, no text. The debugging affordance."""
    return [
        {
            "case_id": result.case_id,
            "probe": result.probe,
            "expected_document_ids": list(result.expected_document_ids),
            "returned_document_ids": list(result.returned_document_ids),
        }
        for result in scored_cases(results, level=DOCUMENT_LEVEL)
        if rank_of_first_relevant(result, level=DOCUMENT_LEVEL) is None
    ]


def _ranked_pairs(result: CaseResult) -> list[tuple[str, str | None]]:
    return list(
        zip(result.returned_document_ids, result.returned_sections, strict=True)
    )


def _expected_labels(result: CaseResult, *, level: str) -> tuple[str, ...]:
    return (
        result.expected_sections if level == SECTION_LEVEL else result.expected_document_ids
    )


def _found_labels(result: CaseResult, k: int, *, level: str) -> set[str]:
    found: set[str] = set()
    for document_id, section in _ranked_pairs(result)[:k]:
        if document_id not in result.expected_document_ids:
            continue
        if level == DOCUMENT_LEVEL:
            found.add(document_id)
        elif section is not None and section in result.expected_sections:
            found.add(section)
    return found


def _abstained(result: CaseResult) -> bool:
    return result.retrieval_status == NO_RESULTS_STATUS or not result.returned_document_ids


def _percentile(sorted_values: Sequence[int], percentile: int) -> int:
    position = math.ceil(percentile / 100 * len(sorted_values)) - 1
    return sorted_values[min(max(position, 0), len(sorted_values) - 1)]


def _check_level(level: str) -> None:
    if level not in LEVELS:
        raise ValueError(f"unknown relevance level {level!r}; allowed: {', '.join(LEVELS)}")


# --------------------------------------------------------------------------
# Impure: corpus, embedder, report.
# --------------------------------------------------------------------------


def load_golden_cases(fixture_path: Path, corpus_dir: Path) -> tuple[Any, ...]:
    """Load and corpus-validate the golden set through the fixture loader."""
    spec = importlib.util.spec_from_file_location("retrieval_fixture_loader", LOADER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load retrieval fixture loader from {LOADER_PATH}")
    loader = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loader  # dataclasses resolve annotations via sys.modules
    spec.loader.exec_module(loader)
    return tuple(loader.load_retrieval_golden(fixture_path, corpus_dir=corpus_dir))


def load_documents(corpus_dir: Path) -> tuple[KnowledgeDocument, ...]:
    from cowork_agent.integrations.rag.knowledge_base import load_corpus

    return load_corpus(corpus_dir, tenant_id=TENANT_ID)


def build_embedder(name: str) -> EmbeddingPort | None:
    """Return the requested embedder, or None when its key is missing."""
    if name == "hashing":
        from cowork_agent.integrations.rag.fakes import HashingEmbedder

        return HashingEmbedder()
    from cowork_agent.config import GeminiSettings
    from cowork_agent.integrations.rag.embeddings import GeminiEmbeddingAdapter

    try:
        return GeminiEmbeddingAdapter(GeminiSettings.from_env())
    except ValueError as exc:
        print(f"Skipping retrieval evaluation: gemini embeddings are not configured ({exc}).")
        print("Set GEMINI_API_KEY_1 in .env and rerun, or use --dry-run for the offline run.")
        return None


def build_reranker(*, enabled: bool) -> RerankerPort | None:
    """Return the Jina reranker when --rerank is passed, else None.

    Without the flag nothing is constructed, so the reranker is inert by
    default. The adapter itself already degrades to the untouched candidate
    order when the key is missing; the warning exists so a keyless run is
    not mistaken for a measurement of reranking.
    """
    if not enabled:
        return None
    from cowork_agent.integrations.rag.jina_reranker import JinaRerankerAdapter

    api_key = os.getenv("JINA_API_KEY") or None
    if api_key is None:
        print(
            "--rerank was passed but JINA_API_KEY is unset: the adapter will pass "
            "candidates through unchanged, so this run measures hybrid without reranking."
        )
    return JinaRerankerAdapter(api_key=api_key)


class Bm25OnlyRetriever:
    """Lexical-only retriever wearing the SemanticMemoryPort surface.

    ``BM25SearchAdapter.search`` returns ``(chunk_id, score)`` pairs rather
    than a ``SemanticRetrievalResponse``, so this shim rehydrates the chunks
    and synthesises the response. That keeps dense, bm25 and hybrid on one
    measurement path instead of three.

    It lives in the harness rather than in ``src/`` on purpose: production
    never runs BM25 alone. It is an evaluation baseline — one leg of the
    hybrid retriever, measured in isolation so the leg can be shown to earn
    its place.
    """

    def __init__(
        self, documents: Sequence[KnowledgeDocument], *, top_k_default: int = 5
    ) -> None:
        from cowork_agent.integrations.rag.bm25 import BM25SearchAdapter

        self._chunks_by_id: dict[str, KnowledgeChunk] = {
            chunk.chunk_id: chunk for document in documents for chunk in document.chunks
        }
        if not self._chunks_by_id:
            raise ValueError("Bm25OnlyRetriever requires a non-empty corpus")
        self._bm25 = BM25SearchAdapter(tuple(self._chunks_by_id.values()))
        self._top_k_default = top_k_default

    async def build_index(self) -> None:
        """No-op: BM25 statistics are computed in the adapter constructor."""

    async def retrieve(self, request: SemanticRetrievalRequest) -> SemanticRetrievalResponse:
        from cowork_agent.domain.target_contracts import (
            RetrievalStatus,
            SemanticChunk,
            SemanticRetrievalResponse,
        )

        started = time.monotonic()
        top_k = request.limits.top_k if request.limits.top_k > 0 else self._top_k_default
        query = "\n".join(part for part in (request.query, *request.knowledge_gaps) if part)
        # ponytail: --min-score is a cosine floor and does not transfer to
        # unbounded BM25 scores, so this leg ignores it. The adapter already
        # drops non-positive scores, which is the lexical equivalent.
        ranked = self._bm25.search(
            query, tenant_id=request.filters.tenant_scope, top_k=top_k
        )
        chunks = tuple(
            SemanticChunk(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                document_title=chunk.document_title,
                section=chunk.section,
                text=chunk.text,
                source_url=chunk.source_url,
                document_version=None,
                relevance_score=score,
                rerank_score=None,
            )
            for chunk, score in (
                (self._chunks_by_id[chunk_id], score) for chunk_id, score in ranked
            )
        )
        return SemanticRetrievalResponse(
            query_id=f"q_{uuid4().hex}",
            tenant_id=request.tenant_id,
            chunks=chunks,
            retrieval_status=(
                RetrievalStatus.SUCCESS if chunks else RetrievalStatus.NO_RESULTS
            ),
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
        )


class Retriever(Protocol):
    """The slice of SemanticMemoryPort this harness drives."""

    async def build_index(self) -> None: ...

    async def retrieve(self, request: SemanticRetrievalRequest) -> SemanticRetrievalResponse: ...


def build_retriever(
    name: str,
    documents: Sequence[KnowledgeDocument],
    embedder: EmbeddingPort,
    *,
    top_k: int,
    min_score: float,
    reranker: RerankerPort | None = None,
) -> Retriever:
    """Construct the named retrieval stack behind a single interface."""
    if name == BM25:
        return Bm25OnlyRetriever(documents, top_k_default=top_k)
    if name == HYBRID:
        from cowork_agent.integrations.rag.hybrid import HybridSemanticMemory

        return HybridSemanticMemory(
            documents,
            embedder,
            reranker=reranker,
            top_k_default=top_k,
            min_score_default=min_score,
        )
    from cowork_agent.integrations.rag.memory import InRepoSemanticMemory

    return InRepoSemanticMemory(
        documents, embedder, top_k_default=top_k, min_score_default=min_score
    )


async def run_evaluation(
    cases: Sequence[Any],
    retriever: Retriever,
    *,
    top_k: int,
    min_score: float,
    timeout_ms: int = 8_000,
) -> list[CaseResult]:
    """Index the corpus once, then retrieve for every case.

    `build_index()` runs exactly once for the whole run: the corpus is one
    embedding batch, so a live run costs that batch plus one query embedding
    per case.
    """
    from cowork_agent.domain.target_contracts import (
        RetrievalFilters,
        RetrievalLimits,
        SemanticRetrievalRequest,
    )

    await retriever.build_index()
    results: list[CaseResult] = []
    for case in cases:
        request = SemanticRetrievalRequest(
            run_id="retrieval-eval",
            tenant_id=TENANT_ID,
            user_id="retrieval-eval",
            query=case.query,
            knowledge_gaps=(),
            filters=RetrievalFilters(tenant_scope=TENANT_ID, document_status=()),
            limits=RetrievalLimits(top_k=top_k, min_score=min_score, timeout_ms=timeout_ms),
        )
        response = await retriever.retrieve(request)
        results.append(
            CaseResult(
                case_id=case.id,
                probe=case.probe.value,
                expected_document_ids=tuple(case.expected_document_ids),
                expected_sections=tuple(case.expected_sections),
                returned_document_ids=tuple(chunk.document_id for chunk in response.chunks),
                returned_sections=tuple(chunk.section for chunk in response.chunks),
                retrieval_status=response.retrieval_status.value,
                latency_ms=response.latency_ms,
            )
        )
    return results


def build_report(
    results: Sequence[CaseResult],
    *,
    embedder: str,
    retriever: str,
    reranker: str | None = None,
    corpus_dir: Path,
    document_count: int,
    chunk_count: int,
    top_k: int,
    min_score: float,
) -> dict[str, Any]:
    """Assemble the SPEC §6.3 report. Identifiers and metrics only."""
    aggregated = aggregate(results)
    return {
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "embedder": embedder,
        "retriever": retriever,
        "reranker": reranker,
        "corpus": {
            "document_count": document_count,
            "chunk_count": chunk_count,
            "corpus_dir": _relative_to_repo(corpus_dir),
        },
        "limits": {"top_k": top_k, "min_score": min_score},
        "case_count": len(results),
        "document_level": aggregated["document_level"],
        "section_level": aggregated["section_level"],
        "by_probe": aggregated["by_probe"],
        "by_document": aggregated["by_document"],
        "abstention": abstention_stats(results),
        "latency_ms": latency_percentiles(results),
        "misses": miss_report(results),
    }


def default_output_path(embedder: str, retriever: str, *, reranked: bool = False) -> Path:
    """SPEC §6.3 filename; reranked runs get their own so both can coexist."""
    date = datetime.now(UTC).date().isoformat()
    suffix = "-rerank" if reranked else ""
    return DEFAULT_OUTPUT_DIR / f"retrieval-eval-{date}-{embedder}-{retriever}{suffix}.json"


def write_report(report: dict[str, Any], target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return target


def print_summary(report: dict[str, Any], target: Path) -> None:
    corpus = report["corpus"]
    print(
        f"Evaluated retrieval for {report['case_count']} cases over "
        f"{corpus['document_count']} documents / {corpus['chunk_count']} chunks: "
        f"embedder={report['embedder']}, retriever={report['retriever']}, "
        f"reranker={report['reranker'] or 'none'}."
    )
    for level in ("document_level", "section_level"):
        metrics = report[level]
        print(
            f"  {level}: hit@1={metrics['hit_at_1']} hit@3={metrics['hit_at_3']} "
            f"mrr={metrics['mrr']} recall@5={metrics['recall_at_5']}"
        )
    for probe, slice_metrics in report["by_probe"].items():
        section = slice_metrics["section_level"]
        print(
            f"  probe {probe} (n={slice_metrics['case_count']}): "
            f"section hit@1={section['hit_at_1']} mrr={section['mrr']}"
        )
    abstention = report["abstention"]
    false_answers = ", ".join(abstention["false_answer_case_ids"]) or "none"
    print(
        f"Abstention over {abstention['case_count']} unanswerable case(s): "
        f"rate={abstention['abstention_rate']}; false answers: {false_answers}."
    )
    print(f"Latency ms: p50={report['latency_ms']['p50']} p95={report['latency_ms']['p95']}")
    print(f"Report: {target}")


def _relative_to_repo(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.name


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate retrieval quality over the labeled RAG golden set."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Force the deterministic HashingEmbedder; CI-safe, needs no API key.",
    )
    parser.add_argument(
        "--embedder",
        choices=("hashing", "gemini"),
        default="hashing",
        help="Embedding backend (default: hashing).",
    )
    parser.add_argument(
        "--retriever",
        choices=RETRIEVERS,
        default=DENSE,
        help="Retrieval stack (default: dense).",
    )
    parser.add_argument(
        "--rerank",
        action="store_true",
        help="Rerank hybrid candidates with JinaRerankerAdapter (needs JINA_API_KEY).",
    )
    parser.add_argument("--top-k", type=int, default=5, help="Chunks per query (default: 5).")
    parser.add_argument(
        "--min-score", type=float, default=0.2, help="Relevance floor (default: 0.2)."
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=DEFAULT_FIXTURE_PATH,
        help=f"Golden set JSON (default: {DEFAULT_FIXTURE_PATH}).",
    )
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=DEFAULT_CORPUS_DIR,
        help=f"Knowledge corpus directory (default: {DEFAULT_CORPUS_DIR}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            f"Report path (default: {DEFAULT_OUTPUT_DIR}/"
            "retrieval-eval-<date>-<embedder>-<retriever>.json)."
        ),
    )
    parser.add_argument(
        "--fail-under-mrr",
        type=float,
        default=None,
        help="Exit non-zero when section-level MRR falls below this value. For CI gating.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.rerank and args.retriever != HYBRID:
        print(
            f"--rerank only applies to --retriever {HYBRID}; "
            f"{args.retriever} has no rerank stage.",
            file=sys.stderr,
        )
        return 2

    embedder_name = "hashing" if args.dry_run else args.embedder
    embedder = build_embedder(embedder_name)
    if embedder is None:
        return 0

    documents = load_documents(args.corpus_dir)
    cases = load_golden_cases(args.fixture, args.corpus_dir)
    retriever = build_retriever(
        args.retriever,
        documents,
        embedder,
        top_k=args.top_k,
        min_score=args.min_score,
        reranker=build_reranker(enabled=args.rerank),
    )
    results = asyncio.run(
        run_evaluation(cases, retriever, top_k=args.top_k, min_score=args.min_score)
    )
    report = build_report(
        results,
        embedder=embedder_name,
        retriever=args.retriever,
        reranker="jina" if args.rerank else None,
        corpus_dir=args.corpus_dir,
        document_count=len(documents),
        chunk_count=sum(len(document.chunks) for document in documents),
        top_k=args.top_k,
        min_score=args.min_score,
    )
    target = write_report(
        report,
        args.output
        or default_output_path(embedder_name, args.retriever, reranked=args.rerank),
    )
    print_summary(report, target)

    if args.fail_under_mrr is not None:
        section_mrr = report["section_level"]["mrr"]
        if section_mrr < args.fail_under_mrr:
            print(
                f"FAIL: section-level MRR {section_mrr} is below "
                f"--fail-under-mrr {args.fail_under_mrr}.",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
