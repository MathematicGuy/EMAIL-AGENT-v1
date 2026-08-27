"""Metric math and dry-run plumbing for the retrieval harness (SPEC-rag §6.4)."""

import asyncio
import math
import subprocess
import sys
import types
from pathlib import Path

import pytest

from tests.unit.scripts.cli_harness import CliResult, load_script
from tests.unit.scripts.cli_harness import run_cli as harness_run_cli

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "evaluate_retrieval.py"
CORPUS_DIR = REPO_ROOT / "data" / "extracted"


def load_module() -> types.ModuleType:
    return load_script("evaluate_retrieval")


def run_cli(*argv: str) -> CliResult:
    return harness_run_cli("evaluate_retrieval", *argv)


def _result(
    module: types.ModuleType,
    case_id: str,
    *,
    probe: str = "mixed",
    expected_document_ids: tuple[str, ...] = ("doc-a",),
    expected_sections: tuple[str, ...] = ("Section A",),
    returned: tuple[tuple[str, str | None], ...] = (),
    retrieval_status: str = "success",
    latency_ms: int = 10,
    scores: tuple[float, ...] | None = None,
    configured_score_kind: str = "dense_cosine",
    observed_score_kind: str | None = None,
    reranker_requested: bool = False,
    reranker_applied: bool = False,
):
    returned_scores = scores if scores is not None else tuple(1.0 for _ in returned)
    observed_kind = observed_score_kind
    if observed_kind is None and returned_scores:
        observed_kind = configured_score_kind
    return module.CaseResult(
        case_id=case_id,
        probe=probe,
        expected_document_ids=expected_document_ids,
        expected_sections=expected_sections,
        returned_document_ids=tuple(document_id for document_id, _ in returned),
        returned_sections=tuple(section for _, section in returned),
        returned_scores=returned_scores,
        configured_score_kind=configured_score_kind,
        observed_score_kind=observed_kind,
        reranker_requested=reranker_requested,
        reranker_applied=reranker_applied,
        retrieval_status=retrieval_status,
        latency_ms=latency_ms,
    )


def _filler(count: int, start: int = 1) -> tuple[tuple[str, str | None], ...]:
    return tuple((f"doc-x{index}", f"Noise {index}") for index in range(start, start + count))


def _evaluate_fake_chunks(
    module: types.ModuleType,
    chunks: tuple[types.SimpleNamespace, ...],
    *,
    configured_score_kind: str,
    reranker_requested: bool = False,
):
    class FakeRetriever:
        async def build_index(self) -> None:
            return None

        async def retrieve(self, _request: object) -> types.SimpleNamespace:
            status = "success" if chunks else "no_results"
            return types.SimpleNamespace(
                chunks=chunks,
                retrieval_status=types.SimpleNamespace(value=status),
                latency_ms=1,
            )

    case = types.SimpleNamespace(
        id="q-001",
        probe=types.SimpleNamespace(value="mixed"),
        query="not serialized",
        expected_document_ids=("doc-a",),
        expected_sections=("Section A",),
    )
    return asyncio.run(
        module.run_evaluation(
            [case],
            FakeRetriever(),
            top_k=5,
            min_score=0.2,
            configured_score_kind=configured_score_kind,
            reranker_requested=reranker_requested,
        )
    )[0]


def test_retrieval_ranked_metrics_and_scoring_math() -> None:
    module = load_module()
    assert module.DEFAULT_OUTPUT_DIR == REPO_ROOT / "evaluations" / "RETRIEVAL" / "baselines"

    hit = _result(module, "q-001", returned=(*_filler(2), ("doc-a", "Section A")))
    miss = _result(module, "q-002", returned=_filler(3))

    assert module.rank_of_first_relevant(hit, level=module.DOCUMENT_LEVEL) == 3
    assert module.reciprocal_rank(3) == 1 / 3
    assert module.mean_reciprocal_rank([hit, miss], level=module.DOCUMENT_LEVEL) == round(
        (1 / 3) / 2, 4
    )
    assert module.hit_at_k([hit], 3, level=module.DOCUMENT_LEVEL) == 1.0
    assert module.hit_at_k([hit], 1, level=module.DOCUMENT_LEVEL) == 0.0

    # Recall at 5 over multiple documents
    multi = _result(
        module,
        "q-003",
        expected_document_ids=("doc-a", "doc-b"),
        expected_sections=("Section A", "Section B"),
        returned=(("doc-a", "Section A"), *_filler(4)),
    )
    assert module.recall_at_k([multi], 5, level=module.DOCUMENT_LEVEL) == 0.5

    # Section level is stricter
    section_miss = _result(module, "q-004", returned=(("doc-a", "Section Z"),))
    assert module.rank_of_first_relevant(section_miss, level=module.DOCUMENT_LEVEL) == 1
    assert module.rank_of_first_relevant(section_miss, level=module.SECTION_LEVEL) is None

    # Abstention & unanswerable handling
    unanswerable = _result(
        module,
        "q-029",
        probe="unanswerable",
        expected_document_ids=(),
        expected_sections=(),
        retrieval_status="no_results",
    )
    assert module.scored_cases([hit, unanswerable], level=module.DOCUMENT_LEVEL) == (hit,)
    assert module.abstention_stats([unanswerable])["abstention_rate"] == 1.0

    # Latency percentiles
    latencies = [_result(module, f"q-{i}", latency_ms=i * 10) for i in range(1, 6)]
    assert module.latency_percentiles(latencies) == {"p50": 30, "p95": 50}


def test_case_result_score_evidence_validation() -> None:
    module = load_module()
    # Parallel counts mismatch
    with pytest.raises(ValueError, match="parallel"):
        _result(module, "q-001", returned=(("doc-a", "Section A"),), scores=(0.9, 0.8))
    # Non-finite scores
    with pytest.raises(ValueError, match="finite"):
        _result(module, "q-002", returned=(("doc-a", "Section A"),), scores=(math.inf,))
    # Invalid kind
    with pytest.raises(ValueError, match="score_kind"):
        _result(
            module,
            "q-003",
            returned=(("doc-a", "Section A"),),
            scores=(0.9,),
            observed_score_kind=None,
            configured_score_kind="bad",
        )


def test_run_evaluation_provenance_and_dry_run(tmp_path: Path) -> None:
    module = load_module()
    base_chunk = types.SimpleNamespace(
        document_id="doc-a", section="Section A", relevance_score=0.61, rerank_score=None
    )
    dense = _evaluate_fake_chunks(module, (base_chunk,), configured_score_kind="dense_cosine")
    bm25 = _evaluate_fake_chunks(module, (base_chunk,), configured_score_kind="bm25")

    assert (dense.observed_score_kind, dense.returned_scores) == ("dense_cosine", (0.61,))
    assert (bm25.observed_score_kind, bm25.returned_scores) == ("bm25", (0.61,))

    # CLI help and dry-run
    help_res = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, check=False
    )
    assert help_res.returncode == 0 and "dry-run" in help_res.stdout

    run_res = run_cli("--dry-run", "--output", str(tmp_path / "out.json"))
    assert run_res.returncode == 0
