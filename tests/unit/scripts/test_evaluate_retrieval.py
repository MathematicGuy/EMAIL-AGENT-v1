"""Metric math and dry-run plumbing for the retrieval harness (SPEC-rag §6.4).

The metric tests build CaseResults by hand: no corpus, no embedder, no
filesystem. Only the two subprocess tests touch the real corpus.
"""

import asyncio
import importlib.util
import json
import math
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "evaluate_retrieval.py"
CORPUS_DIR = REPO_ROOT / "data" / "extracted"


def load_module() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("evaluate_retrieval_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations via sys.modules
    spec.loader.exec_module(module)
    return module


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
    """`count` irrelevant results, so ranks can be positioned exactly."""
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


def test_help_runs_without_provider_keys() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0
    assert "dry-run" in result.stdout
    assert "fail-under-mrr" in result.stdout


def test_reciprocal_rank_is_one_over_rank() -> None:
    module = load_module()
    # Relevant document at rank 3: two irrelevant results ahead of it.
    result = _result(module, "q-001", returned=(*_filler(2), ("doc-a", "Section A")))
    assert module.rank_of_first_relevant(result, level=module.DOCUMENT_LEVEL) == 3
    assert module.reciprocal_rank(3) == 1 / 3
    assert module.mean_reciprocal_rank([result], level=module.DOCUMENT_LEVEL) == round(1 / 3, 4)


def test_a_miss_contributes_zero_and_is_not_dropped() -> None:
    module = load_module()
    hit = _result(module, "q-001", returned=(("doc-a", "Section A"),))
    miss = _result(module, "q-002", returned=_filler(3))
    assert module.rank_of_first_relevant(miss, level=module.DOCUMENT_LEVEL) is None
    assert module.reciprocal_rank(None) == 0.0
    # Averaged over BOTH cases, not just the one that hit.
    assert module.mean_reciprocal_rank([hit, miss], level=module.DOCUMENT_LEVEL) == 0.5
    assert module.miss_report([hit, miss]) == [
        {
            "case_id": "q-002",
            "probe": "mixed",
            "expected_document_ids": ["doc-a"],
            "returned_document_ids": ["doc-x1", "doc-x2", "doc-x3"],
        }
    ]


def test_hit_at_3_boundary_between_rank_three_and_four() -> None:
    module = load_module()
    at_three = _result(module, "q-001", returned=(*_filler(2), ("doc-a", "Section A")))
    at_four = _result(module, "q-002", returned=(*_filler(3), ("doc-a", "Section A")))
    assert module.rank_of_first_relevant(at_four, level=module.DOCUMENT_LEVEL) == 4
    assert module.hit_at_k([at_three], 3, level=module.DOCUMENT_LEVEL) == 1.0
    assert module.hit_at_k([at_four], 3, level=module.DOCUMENT_LEVEL) == 0.0
    assert module.hit_at_k([at_three, at_four], 3, level=module.DOCUMENT_LEVEL) == 0.5
    assert module.hit_at_k([at_three], 1, level=module.DOCUMENT_LEVEL) == 0.0


def test_recall_at_5_over_multiple_expected_documents() -> None:
    module = load_module()
    result = _result(
        module,
        "q-001",
        expected_document_ids=("doc-a", "doc-b"),
        expected_sections=("Section A", "Section B"),
        returned=(("doc-a", "Section A"), *_filler(4)),
    )
    # One of two expected documents present in the top 5.
    assert module.recall_at_k([result], 5, level=module.DOCUMENT_LEVEL) == 0.5
    assert module.recall_at_k([result], 5, level=module.SECTION_LEVEL) == 0.5


def test_section_level_is_stricter_than_document_level() -> None:
    module = load_module()
    # Right document, wrong heading: a document hit and a section miss.
    result = _result(module, "q-001", returned=(("doc-a", "Section Z"),))
    assert module.rank_of_first_relevant(result, level=module.DOCUMENT_LEVEL) == 1
    assert module.rank_of_first_relevant(result, level=module.SECTION_LEVEL) is None
    assert module.hit_at_k([result], 1, level=module.DOCUMENT_LEVEL) == 1.0
    assert module.hit_at_k([result], 1, level=module.SECTION_LEVEL) == 0.0
    assert module.recall_at_k([result], 5, level=module.SECTION_LEVEL) == 0.0


def test_section_level_skips_past_a_wrong_section_to_a_later_right_one() -> None:
    module = load_module()
    result = _result(
        module, "q-001", returned=(("doc-a", "Section Z"), ("doc-a", "Section A"))
    )
    assert module.rank_of_first_relevant(result, level=module.DOCUMENT_LEVEL) == 1
    assert module.rank_of_first_relevant(result, level=module.SECTION_LEVEL) == 2


def test_empty_expected_sections_are_excluded_and_counted() -> None:
    module = load_module()
    labeled = _result(module, "q-001", returned=(("doc-a", "Section A"),))
    unlabeled = _result(
        module, "q-002", expected_sections=(), returned=(("doc-a", "Section A"),)
    )
    results = [labeled, unlabeled]
    assert len(module.scored_cases(results, level=module.DOCUMENT_LEVEL)) == 2
    assert len(module.scored_cases(results, level=module.SECTION_LEVEL)) == 1
    assert module.excluded_case_count(results) == 1
    section = module.level_metrics(results, level=module.SECTION_LEVEL)
    assert section["excluded_case_count"] == 1
    # The excluded case must not drag the section score toward either extreme.
    assert section["hit_at_1"] == 1.0
    assert "excluded_case_count" not in module.level_metrics(
        results, level=module.DOCUMENT_LEVEL
    )


def test_unanswerable_cases_are_excluded_from_ranked_metrics() -> None:
    module = load_module()
    answerable = _result(module, "q-001", returned=(("doc-a", "Section A"),))
    unanswerable = _result(
        module,
        "q-029",
        probe="unanswerable",
        expected_document_ids=(),
        expected_sections=(),
        retrieval_status="no_results",
    )
    results = [answerable, unanswerable]
    assert module.scored_cases(results, level=module.DOCUMENT_LEVEL) == (answerable,)
    assert module.hit_at_k(results, 1, level=module.DOCUMENT_LEVEL) == 1.0
    assert module.aggregate(results)["by_probe"].keys() == {"mixed"}


def test_abstention_counts_no_results_and_zero_chunks_alike() -> None:
    module = load_module()
    no_results = _result(
        module,
        "q-029",
        probe="unanswerable",
        expected_document_ids=(),
        expected_sections=(),
        retrieval_status="no_results",
    )
    zero_chunks = _result(
        module,
        "q-030",
        probe="unanswerable",
        expected_document_ids=(),
        expected_sections=(),
        retrieval_status="success",
        returned=(),
    )
    false_answer = _result(
        module,
        "q-031",
        probe="unanswerable",
        expected_document_ids=(),
        expected_sections=(),
        retrieval_status="success",
        returned=(("doc-a", "Section A"),),
    )
    stats = module.abstention_stats([no_results, zero_chunks, false_answer])
    assert stats == {
        "case_count": 3,
        "abstention_rate": round(2 / 3, 4),
        "false_answer_case_ids": ["q-031"],
    }
    assert module.abstention_stats([]) == {
        "case_count": 0,
        "abstention_rate": None,
        "false_answer_case_ids": [],
    }


def test_aggregate_slices_by_probe_and_document() -> None:
    module = load_module()
    results = [
        _result(module, "q-001", probe="lexical", returned=(("doc-a", "Section A"),)),
        _result(
            module,
            "q-002",
            probe="semantic",
            expected_document_ids=("doc-b",),
            expected_sections=("Section B",),
            returned=(*_filler(1), ("doc-b", "Section B")),
        ),
    ]
    aggregated = module.aggregate(results)
    assert aggregated["by_probe"]["lexical"]["case_count"] == 1
    assert aggregated["by_probe"]["lexical"]["section_level"]["hit_at_1"] == 1.0
    assert aggregated["by_probe"]["semantic"]["section_level"]["hit_at_1"] == 0.0
    assert aggregated["by_document"]["doc-b"] == {
        "case_count": 1,
        "mrr": 0.5,
        "section_mrr": 0.5,
    }
    assert aggregated["document_level"]["mrr"] == 0.75


def test_latency_percentiles() -> None:
    module = load_module()
    results = [
        _result(module, f"q-{index:03d}", latency_ms=latency)
        for index, latency in enumerate((10, 20, 30, 40, 100), start=1)
    ]
    assert module.latency_percentiles(results) == {"p50": 30, "p95": 100}
    assert module.latency_percentiles([]) == {"p50": 0, "p95": 0}


def test_unknown_relevance_level_is_rejected() -> None:
    module = load_module()
    result = _result(module, "q-001", returned=(("doc-a", "Section A"),))
    try:
        module.rank_of_first_relevant(result, level="chunk")
    except ValueError as exc:
        assert "chunk" in str(exc)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("unknown level must raise ValueError")


def test_score_summary_covers_empty_single_and_two_result_cases() -> None:
    module = load_module()
    empty = _result(module, "q-001")
    single = _result(
        module, "q-002", returned=(("doc-a", "Section A"),), scores=(0.7,)
    )
    pair = _result(
        module,
        "q-003",
        returned=(("doc-a", "Section A"), ("doc-b", "Section B")),
        scores=(0.8, 0.55),
    )

    assert module.score_summary(empty) == {
        "top_score": None,
        "runner_up_score": None,
        "delta": None,
    }
    assert module.score_summary(single) == {
        "top_score": 0.7,
        "runner_up_score": None,
        "delta": None,
    }
    assert module.score_summary(pair) == {
        "top_score": 0.8,
        "runner_up_score": 0.55,
        "delta": 0.25,
    }


def test_case_result_rejects_invalid_score_evidence() -> None:
    module = load_module()
    with pytest.raises(ValueError, match="parallel"):
        _result(
            module,
            "q-001",
            returned=(("doc-a", "Section A"),),
            scores=(0.9, 0.8),
        )
    with pytest.raises(ValueError, match="finite"):
        _result(
            module,
            "q-002",
            returned=(("doc-a", "Section A"),),
            scores=(math.inf,),
        )
    with pytest.raises(ValueError, match="score_kind"):
        _result(
            module,
            "q-003",
            returned=(("doc-a", "Section A"),),
            scores=(0.9,),
            observed_score_kind=None,
            configured_score_kind="not-a-kind",
        )
    with pytest.raises(ValueError, match="numeric"):
        _result(
            module,
            "q-004",
            returned=(("doc-a", "Section A"),),
            scores=(True,),
        )
    with pytest.raises(ValueError, match="numeric"):
        _result(
            module,
            "q-005",
            returned=(("doc-a", "Section A"),),
            scores=("0.9",),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="derived"):
        _result(
            module,
            "q-006",
            returned=(("doc-a", "Section A"), ("doc-b", "Section B")),
            scores=(sys.float_info.max, -sys.float_info.max),
        )
    with pytest.raises(ValueError, match="reranker_requested"):
        _result(
            module,
            "q-005",
            returned=(("doc-a", "Section A"),),
            scores=(0.9,),
            configured_score_kind="jina",
            observed_score_kind="jina",
            reranker_applied=True,
        )
    with pytest.raises(ValueError, match="compatible"):
        _result(
            module,
            "q-007",
            returned=(("doc-a", "Section A"),),
            scores=(0.9,),
            configured_score_kind="dense_cosine",
            observed_score_kind="rrf",
        )
    with pytest.raises(ValueError, match="compatible"):
        _result(
            module,
            "q-008",
            returned=(("doc-a", "Section A"),),
            scores=(0.9,),
            configured_score_kind="jina",
            observed_score_kind="dense_cosine",
            reranker_requested=True,
        )


def test_run_evaluation_maps_dense_bm25_rrf_and_jina_score_provenance() -> None:
    module = load_module()
    base_chunk = types.SimpleNamespace(
        document_id="doc-a",
        section="Section A",
        relevance_score=0.61,
        rerank_score=None,
    )
    dense = _evaluate_fake_chunks(
        module, (base_chunk,), configured_score_kind="dense_cosine"
    )
    bm25 = _evaluate_fake_chunks(module, (base_chunk,), configured_score_kind="bm25")
    rrf = _evaluate_fake_chunks(module, (base_chunk,), configured_score_kind="rrf")
    jina_chunk = types.SimpleNamespace(**(vars(base_chunk) | {"rerank_score": 0.93}))
    jina = _evaluate_fake_chunks(
        module, (jina_chunk,), configured_score_kind="jina", reranker_requested=True
    )

    assert (dense.observed_score_kind, dense.returned_scores) == ("dense_cosine", (0.61,))
    assert (bm25.observed_score_kind, bm25.returned_scores) == ("bm25", (0.61,))
    assert (rrf.observed_score_kind, rrf.returned_scores) == ("rrf", (0.61,))
    assert (jina.observed_score_kind, jina.returned_scores) == ("jina", (0.93,))
    assert jina.reranker_requested is True
    assert jina.reranker_applied is True


def test_run_evaluation_preserves_empty_and_reranker_fallback_provenance() -> None:
    module = load_module()
    empty = _evaluate_fake_chunks(
        module, (), configured_score_kind="jina", reranker_requested=True
    )
    fallback = _evaluate_fake_chunks(
        module,
        (
            types.SimpleNamespace(
                document_id="doc-a",
                section="Section A",
                relevance_score=0.42,
                rerank_score=None,
            ),
        ),
        configured_score_kind="jina",
        reranker_requested=True,
    )
    assert empty.configured_score_kind == "jina"
    assert empty.observed_score_kind is None
    assert empty.reranker_requested is True
    assert empty.reranker_applied is False
    assert fallback.observed_score_kind == "rrf"
    assert fallback.returned_scores == (0.42,)
    assert fallback.reranker_applied is False


def test_run_evaluation_rejects_mixed_rerank_scores() -> None:
    module = load_module()
    chunks = tuple(
        types.SimpleNamespace(
            document_id="doc-a",
            section="Section A",
            relevance_score=score,
            rerank_score=rerank,
        )
        for score, rerank in ((0.8, 0.9), (0.7, None))
    )
    with pytest.raises(ValueError, match="mixed null and non-null rerank scores"):
        _evaluate_fake_chunks(
            module, chunks, configured_score_kind="jina", reranker_requested=True
        )


@pytest.mark.parametrize("invalid_score", [True, "0.9", math.nan, math.inf])
def test_run_evaluation_rejects_invalid_raw_rerank_scores(invalid_score: object) -> None:
    module = load_module()
    chunk = types.SimpleNamespace(
        document_id="doc-a",
        section="Section A",
        relevance_score=0.8,
        rerank_score=invalid_score,
    )
    with pytest.raises(ValueError, match="rerank scores must be finite numeric"):
        _evaluate_fake_chunks(
            module, (chunk,), configured_score_kind="jina", reranker_requested=True
        )


def test_candidate_thresholds_are_deterministic_boundaries() -> None:
    module = load_module()
    maximum = 0.9
    assert module.candidate_thresholds([maximum, 0.5, 0.5, 0.7]) == (
        0.5,
        0.6,
        0.8,
        math.nextafter(maximum, math.inf),
    )
    assert module.candidate_thresholds([]) == ()
    with pytest.raises(ValueError, match="finite"):
        module.candidate_thresholds([math.nan])
    with pytest.raises(ValueError, match="derived"):
        module.candidate_thresholds([sys.float_info.max])
    adjacent = math.nextafter(1.0, math.inf)
    assert module.candidate_thresholds([1.0, adjacent]) == (
        1.0,
        adjacent,
        math.nextafter(adjacent, math.inf),
    )


def test_singleton_kind_retains_undefined_margin_summary_without_threshold() -> None:
    module = load_module()
    singleton = _result(
        module,
        "q-001",
        returned=(("doc-a", "Section A"),),
        scores=(0.7,),
    )

    sweeps = module.calibration_sweeps([singleton])

    assert sweeps["margin"] == []
    assert sweeps["margin_summary"] == [
        {
            "observed_score_kind": "dense_cosine",
            "defined_margin_case_count": 0,
            "undefined_margin_case_ids": ["q-001"],
            "candidate_count": 0,
        }
    ]


def test_absolute_and_margin_gate_equality_passes_and_below_abstains() -> None:
    module = load_module()
    equal = _result(
        module,
        "q-001",
        returned=(("doc-a", "Section A"), ("doc-x", "Noise")),
        scores=(0.5, 0.3),
    )
    below = _result(
        module,
        "q-002",
        returned=(("doc-a", "Section A"), ("doc-x", "Noise")),
        scores=(0.49, 0.3),
    )

    absolute = module.simulate_gate(
        [equal, below], score_kind="dense_cosine", gate_kind="absolute_score", threshold=0.5
    )
    margin = module.simulate_gate(
        [equal, below], score_kind="dense_cosine", gate_kind="margin", threshold=0.2
    )
    assert module._abstained(absolute[0]) is False
    assert module._abstained(absolute[1]) is True
    assert module._abstained(margin[0]) is False
    assert module._abstained(margin[1]) is True


def test_gate_is_per_kind_preserves_inherited_abstention_and_undefined_margin() -> None:
    module = load_module()
    dense = _result(
        module, "q-001", returned=(("doc-a", "Section A"),), scores=(0.4,)
    )
    rrf = _result(
        module,
        "q-002",
        returned=(("doc-a", "Section A"), ("doc-x", "Noise")),
        scores=(0.2, 0.19),
        configured_score_kind="rrf",
        observed_score_kind="rrf",
    )
    inherited = _result(
        module,
        "q-029",
        probe="unanswerable",
        expected_document_ids=(),
        expected_sections=(),
        retrieval_status="no_results",
        configured_score_kind="dense_cosine",
    )
    original = (dense, rrf, inherited)

    sweep = module.calibration_sweep(
        original,
        score_kind="dense_cosine",
        gate_kind="margin",
        candidates=[0.5],
    )[0]

    assert original == (dense, rrf, inherited)
    assert sweep["affected_population_count"] == 1
    assert sweep["inherited_abstention_case_ids"] == ["q-029"]
    assert sweep["undefined_margin_case_ids"] == ["q-001"]
    assert sweep["answerable"]["false_abstention_count"] == 0
    assert sweep["metrics"]["overall"]["document_level"]["hit_at_1"] == 1.0


def test_calibration_sweep_accounts_for_28_answerable_and_four_unanswerable() -> None:
    module = load_module()
    answerable = [
        _result(
            module,
            f"q-{index:03d}",
            probe="lexical" if index <= 14 else "semantic",
            returned=(("doc-a", "Section A"),),
            scores=((0.4 if index <= 3 else 0.8),),
        )
        for index in range(1, 29)
    ]
    unanswerable = [
        _result(
            module,
            f"q-{index:03d}",
            probe="unanswerable",
            expected_document_ids=(),
            expected_sections=(),
            returned=(("doc-x", "Noise"),),
            scores=((0.4 if index in (29, 30) else 0.8),),
        )
        for index in range(29, 33)
    ]

    candidate = module.calibration_sweep(
        [*answerable, *unanswerable],
        score_kind="dense_cosine",
        gate_kind="absolute_score",
        candidates=[0.5],
    )[0]

    assert candidate["unanswerable"] == {
        "case_count": 4,
        "abstention_count": 2,
        "abstention_rate": 0.5,
        "false_answer_case_ids": ["q-031", "q-032"],
    }
    assert candidate["answerable"] == {
        "case_count": 28,
        "false_abstention_count": 3,
        "false_abstention_rate": round(3 / 28, 4),
        "affected_case_ids": ["q-001", "q-002", "q-003"],
    }
    assert candidate["metrics"]["by_probe"]["lexical"]["document_level"][
        "hit_at_1"
    ] == round(11 / 14, 4)
    assert candidate["metrics"]["by_probe"]["semantic"]["document_level"][
        "hit_at_1"
    ] == 1.0


def test_score_report_schema_is_recursive_closed_and_metadata_only(tmp_path: Path) -> None:
    module = load_module()
    result = _result(
        module,
        "q-001",
        probe="semantic",
        returned=(("doc-a", "Section A"), ("doc-x", "Noise")),
        scores=(0.8, 0.5),
    )
    report = module.build_report(
        [result],
        embedder="hashing",
        retriever="dense",
        corpus_dir=tmp_path,
        document_count=1,
        chunk_count=2,
        top_k=5,
        min_score=0.2,
    )

    assert set(report["score_evidence"]) == {"cases"}
    evidence = report["score_evidence"]["cases"][0]
    assert set(evidence) == {
        "case_id",
        "probe",
        "configured_score_kind",
        "observed_score_kind",
        "reranker_requested",
        "reranker_applied",
        "returned_scores",
        "top_score",
        "runner_up_score",
        "delta",
    }
    assert type(evidence["case_id"]) is str
    assert type(evidence["probe"]) is str
    assert type(evidence["configured_score_kind"]) is str
    assert evidence["observed_score_kind"] is None or type(
        evidence["observed_score_kind"]
    ) is str
    assert type(evidence["reranker_requested"]) is bool
    assert type(evidence["reranker_applied"]) is bool
    assert type(evidence["returned_scores"]) is list
    assert all(type(score) is float for score in evidence["returned_scores"])
    for key in ("top_score", "runner_up_score", "delta"):
        assert evidence[key] is None or type(evidence[key]) is float

    def assert_level_metrics(value: object, *, section: bool) -> None:
        assert isinstance(value, dict)
        expected = {"hit_at_1", "hit_at_3", "mrr", "recall_at_5"}
        if section:
            expected.add("excluded_case_count")
        assert set(value) == expected
        for key in ("hit_at_1", "hit_at_3", "mrr", "recall_at_5"):
            assert type(value[key]) is float
        if section:
            assert type(value["excluded_case_count"]) is int

    def assert_metrics(value: object) -> None:
        assert isinstance(value, dict)
        assert set(value) == {"overall", "by_probe"}
        overall = value["overall"]
        assert isinstance(overall, dict)
        assert set(overall) == {"document_level", "section_level"}
        assert_level_metrics(overall["document_level"], section=False)
        assert_level_metrics(overall["section_level"], section=True)
        by_probe = value["by_probe"]
        assert isinstance(by_probe, dict)
        for probe, probe_metrics in by_probe.items():
            assert type(probe) is str
            assert isinstance(probe_metrics, dict)
            assert set(probe_metrics) == {
                "case_count",
                "document_level",
                "section_level",
            }
            assert type(probe_metrics["case_count"]) is int
            assert_level_metrics(probe_metrics["document_level"], section=False)
            assert_level_metrics(probe_metrics["section_level"], section=True)

    sweeps = report["evaluation_only_calibration_sweeps"]
    assert set(sweeps) == {"absolute_score", "margin", "margin_summary"}
    for summary in sweeps["margin_summary"]:
        assert set(summary) == {
            "observed_score_kind",
            "defined_margin_case_count",
            "undefined_margin_case_ids",
            "candidate_count",
        }
        assert type(summary["observed_score_kind"]) is str
        assert type(summary["defined_margin_case_count"]) is int
        assert type(summary["candidate_count"]) is int
        assert type(summary["undefined_margin_case_ids"]) is list
        assert all(type(case_id) is str for case_id in summary["undefined_margin_case_ids"])
    for candidates in (sweeps["absolute_score"], sweeps["margin"]):
        for candidate in candidates:
            assert set(candidate) == {
                "observed_score_kind",
                "gate_kind",
                "threshold",
                "affected_population_count",
                "inherited_abstention_case_ids",
                "undefined_margin_case_ids",
                "unanswerable",
                "answerable",
                "metrics",
            }
            assert type(candidate["observed_score_kind"]) is str
            assert type(candidate["gate_kind"]) is str
            assert type(candidate["threshold"]) is float
            assert type(candidate["affected_population_count"]) is int
            assert all(
                type(case_id) is str
                for case_id in candidate["inherited_abstention_case_ids"]
            )
            assert all(
                type(case_id) is str
                for case_id in candidate["undefined_margin_case_ids"]
            )
            assert set(candidate["unanswerable"]) == {
                "case_count",
                "abstention_count",
                "abstention_rate",
                "false_answer_case_ids",
            }
            assert set(candidate["answerable"]) == {
                "case_count",
                "false_abstention_count",
                "false_abstention_rate",
                "affected_case_ids",
            }
            for population in (candidate["unanswerable"], candidate["answerable"]):
                for key, value in population.items():
                    if key.endswith("case_ids") or key == "affected_case_ids":
                        assert type(value) is list
                        assert all(type(case_id) is str for case_id in value)
                    elif key.endswith("rate"):
                        assert value is None or type(value) is float
                    else:
                        assert type(value) is int
            assert_metrics(candidate["metrics"])

    forbidden = {"query", "text", "chunk", "chunks", "prompt", "plan", "email_body"}

    def assert_metadata_only(value: object) -> None:
        if isinstance(value, dict):
            assert forbidden.isdisjoint(value)
            for nested in value.values():
                assert_metadata_only(nested)
        elif isinstance(value, list):
            for nested in value:
                assert_metadata_only(nested)
        else:
            assert value is None or isinstance(value, (str, int, float, bool))

    assert_metadata_only(report["score_evidence"])
    assert_metadata_only(sweeps)


def test_bm25_shim_synthesises_a_retrieval_response() -> None:
    """BM25SearchAdapter returns (chunk_id, score); the shim rebuilds chunks."""
    module = load_module()
    from cowork_agent.domain.target_contracts import (
        RetrievalFilters,
        RetrievalLimits,
        RetrievalStatus,
        SemanticRetrievalRequest,
    )

    retriever = module.Bm25OnlyRetriever(_tiny_corpus(), top_k_default=5)
    asyncio.run(retriever.build_index())  # no-op, but part of the interface

    def _retrieve(query: str, tenant: str = "local"):
        request = SemanticRetrievalRequest(
            run_id="t",
            tenant_id=tenant,
            user_id="t",
            query=query,
            knowledge_gaps=(),
            filters=RetrievalFilters(tenant_scope=tenant, document_status=()),
            limits=RetrievalLimits(top_k=5, min_score=0.2, timeout_ms=1000),
        )
        return asyncio.run(retriever.retrieve(request))

    response = _retrieve("hộ chiếu")
    assert response.retrieval_status is RetrievalStatus.SUCCESS
    assert response.chunks[0].document_id == "doc-b"
    assert response.chunks[0].section == "Hộ chiếu"
    assert response.chunks[0].relevance_score > 0

    # No lexical overlap at all, and a foreign tenant, both abstain.
    assert _retrieve("zzzzz").retrieval_status is RetrievalStatus.NO_RESULTS
    assert _retrieve("hộ chiếu", tenant="other").chunks == ()


def test_build_retriever_returns_the_requested_stack() -> None:
    module = load_module()
    from cowork_agent.integrations.rag.fakes import HashingEmbedder
    from cowork_agent.integrations.rag.hybrid import HybridSemanticMemory
    from cowork_agent.integrations.rag.memory import InRepoSemanticMemory

    documents = _tiny_corpus()

    def _build(name: str, **kwargs: object):
        return module.build_retriever(
            name, documents, HashingEmbedder(), top_k=5, min_score=0.2, **kwargs
        )

    assert isinstance(_build("dense"), InRepoSemanticMemory)
    assert isinstance(_build("bm25"), module.Bm25OnlyRetriever)
    assert isinstance(_build("hybrid"), HybridSemanticMemory)


def test_qdrant_evaluation_retriever_builds_in_memory_index_and_delegates() -> None:
    """The evaluation Qdrant path is usable only after its one-time ingestion."""
    module = load_module()
    from cowork_agent.domain.target_contracts import (
        RetrievalFilters,
        RetrievalLimits,
        RetrievalStatus,
        SemanticRetrievalRequest,
    )
    from cowork_agent.integrations.rag.fakes import HashingEmbedder

    retriever = module.build_retriever(
        "qdrant", _tiny_corpus(), HashingEmbedder(), top_k=5, min_score=0.2
    )
    assert isinstance(retriever, module.QdrantEvaluationRetriever)
    request = SemanticRetrievalRequest(
        run_id="t",
        tenant_id="local",
        user_id="t",
        query="hộ chiếu",
        knowledge_gaps=(),
        filters=RetrievalFilters(tenant_scope="local", document_status=()),
        limits=RetrievalLimits(top_k=5, min_score=0.2, timeout_ms=1000),
    )

    with pytest.raises(RuntimeError, match="build_index"):
        asyncio.run(retriever.retrieve(request))

    asyncio.run(retriever.build_index())
    response = asyncio.run(retriever.retrieve(request))

    assert response.retrieval_status is RetrievalStatus.SUCCESS
    assert response.chunks[0].document_id == "doc-b"


def test_qdrant_evaluation_retriever_constructs_once_with_defaults_and_forwards_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A changed Qdrant boundary call must not silently change benchmark behavior."""
    module = load_module()
    import qdrant_client

    from cowork_agent.domain.target_contracts import (
        RetrievalFilters,
        RetrievalLimits,
        SemanticRetrievalRequest,
    )
    from cowork_agent.integrations.rag import qdrant as production_qdrant
    from cowork_agent.integrations.rag.fakes import HashingEmbedder

    constructed_locations: list[str] = []
    ingestions: list[tuple[object, str, object, object]] = []
    memory_constructions: list[tuple[object, str, object, int, float]] = []
    forwarded_requests: list[object] = []
    delegated_response = object()

    class FakeClient:
        def __init__(self, location: str) -> None:
            constructed_locations.append(location)

    async def fake_ingest(
        client: object,
        collection_name: str,
        documents: object,
        embedder: object,
    ) -> int:
        ingestions.append((client, collection_name, documents, embedder))
        return 2

    class FakeMemory:
        def __init__(
            self,
            client: object,
            collection_name: str,
            embedder: object,
            *,
            top_k_default: int,
            min_score_default: float,
        ) -> None:
            memory_constructions.append(
                (client, collection_name, embedder, top_k_default, min_score_default)
            )

        async def retrieve(self, request: object) -> object:
            forwarded_requests.append(request)
            return delegated_response

    monkeypatch.setattr(qdrant_client, "AsyncQdrantClient", FakeClient)
    monkeypatch.setattr(production_qdrant, "ingest_corpus", fake_ingest)
    monkeypatch.setattr(production_qdrant, "QdrantSemanticMemory", FakeMemory)

    documents = _tiny_corpus()
    embedder = HashingEmbedder()
    retriever = module.QdrantEvaluationRetriever(
        documents, embedder, top_k_default=7, min_score_default=0.35
    )
    request = SemanticRetrievalRequest(
        run_id="t",
        tenant_id="local",
        user_id="t",
        query="passport",
        knowledge_gaps=(),
        filters=RetrievalFilters(tenant_scope="local", document_status=()),
        limits=RetrievalLimits(top_k=7, min_score=0.35, timeout_ms=1000),
    )

    asyncio.run(retriever.build_index())
    asyncio.run(retriever.build_index())
    response = asyncio.run(retriever.retrieve(request))

    assert constructed_locations == [":memory:"]
    assert len(ingestions) == 1
    client, collection_name, ingested_documents, ingested_embedder = ingestions[0]
    assert collection_name == "retrieval-eval"
    assert ingested_documents is documents
    assert ingested_embedder is embedder
    assert memory_constructions == [(client, "retrieval-eval", embedder, 7, 0.35)]
    assert response is delegated_response
    assert forwarded_requests == [request]
    assert forwarded_requests[0] is request


def test_qdrant_evaluator_raises_when_adapter_converts_query_failure_to_no_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Qdrant outage must not enter the report as an abstention or miss."""
    module = load_module()
    import qdrant_client

    from cowork_agent.domain.target_contracts import (
        RetrievalFilters,
        RetrievalLimits,
        SemanticRetrievalRequest,
    )
    from cowork_agent.integrations.rag import qdrant as production_qdrant
    from cowork_agent.integrations.rag.fakes import HashingEmbedder

    class FailingQueryClient:
        def __init__(self, _location: str) -> None:
            pass

        async def query_points(self, **_kwargs: object) -> object:
            raise ValueError("qdrant service unavailable")

    async def fake_ingest(*_args: object, **_kwargs: object) -> int:
        return 2

    monkeypatch.setattr(qdrant_client, "AsyncQdrantClient", FailingQueryClient)
    monkeypatch.setattr(production_qdrant, "ingest_corpus", fake_ingest)

    retriever = module.QdrantEvaluationRetriever(
        _tiny_corpus(), HashingEmbedder(), top_k_default=5, min_score_default=0.2
    )
    request = SemanticRetrievalRequest(
        run_id="t",
        tenant_id="local",
        user_id="t",
        query="passport",
        knowledge_gaps=(),
        filters=RetrievalFilters(tenant_scope="local", document_status=()),
        limits=RetrievalLimits(top_k=5, min_score=0.2, timeout_ms=1000),
    )
    asyncio.run(retriever.build_index())

    with pytest.raises(module.QdrantEvaluationError, match="Qdrant query failed"):
        asyncio.run(retriever.retrieve(request))


def test_rerank_is_inert_unless_the_flag_is_passed() -> None:
    module = load_module()
    from cowork_agent.integrations.rag.jina_reranker import JinaRerankerAdapter

    assert module.build_reranker(enabled=False) is None
    assert isinstance(module.build_reranker(enabled=True), JinaRerankerAdapter)


def test_rerank_without_hybrid_exits_two() -> None:
    for retriever in ("dense", "bm25"):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--dry-run", "--rerank", "--retriever", retriever],
            capture_output=True,
            text=True,
            check=False,
            env=_subprocess_env(),
        )
        assert result.returncode == 2, result.stdout
        assert "--rerank only applies to --retriever hybrid" in result.stderr


def test_every_retriever_runs_through_one_measurement_path(tmp_path: Path) -> None:
    fixture = _write_covering_fixture(tmp_path)
    for retriever in ("dense", "bm25", "hybrid", "qdrant"):
        output = tmp_path / f"{retriever}.json"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--dry-run",
                "--retriever",
                retriever,
                "--fixture",
                str(fixture),
                "--output",
                str(output),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=_subprocess_env(),
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(output.read_text(encoding="utf-8"))
        assert report["retriever"] == retriever
        assert report["reranker"] is None
        expected_score_kind = "bm25" if retriever == "bm25" else (
            "rrf" if retriever == "hybrid" else "dense_cosine"
        )
        assert {
            case["configured_score_kind"] for case in report["score_evidence"]["cases"]
        } == {expected_score_kind}
        # Same shape for every stack, so the reports are directly comparable.
        assert set(report["document_level"]) == {"hit_at_1", "hit_at_3", "mrr", "recall_at_5"}
        assert report["by_probe"].keys() == {"lexical", "semantic", "mixed"}


def test_dry_run_writes_report_without_provider_keys(tmp_path: Path) -> None:
    fixture = _write_covering_fixture(tmp_path)
    output = tmp_path / "retrieval-eval.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--dry-run",
            "--fixture",
            str(fixture),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_subprocess_env(),
    )
    assert result.returncode == 0, result.stderr
    serialized = output.read_text(encoding="utf-8")
    report = json.loads(serialized)

    assert report["embedder"] == "hashing"
    assert report["retriever"] == "dense"
    assert report["limits"] == {"top_k": 5, "min_score": 0.2}
    assert report["corpus"]["document_count"] == len(_corpus_sections())
    assert report["corpus"]["chunk_count"] > report["corpus"]["document_count"]
    assert report["case_count"] == len(json.loads(fixture.read_text(encoding="utf-8")))
    for level in ("document_level", "section_level"):
        assert set(report[level]) >= {"hit_at_1", "hit_at_3", "mrr", "recall_at_5"}
    assert report["section_level"]["excluded_case_count"] == 12
    assert report["by_probe"].keys() == {"lexical", "semantic", "mixed"}
    assert report["abstention"]["case_count"] == 1
    assert set(report["latency_ms"]) == {"p50", "p95"}
    assert len(report["score_evidence"]["cases"]) == report["case_count"]
    assert {
        case["configured_score_kind"] for case in report["score_evidence"]["cases"]
    } == {"dense_cosine"}
    assert {
        case["observed_score_kind"]
        for case in report["score_evidence"]["cases"]
        if case["observed_score_kind"] is not None
    } == {"dense_cosine"}
    sweeps = report["evaluation_only_calibration_sweeps"]
    assert sweeps["absolute_score"]
    assert sweeps["margin"]

    # Privacy: identifiers and metrics only, never query or chunk text.
    for case in json.loads(fixture.read_text(encoding="utf-8")):
        assert case["query"] not in serialized
    assert '"query"' not in serialized
    assert '"text"' not in serialized


def test_dry_run_real_fixture_report_has_one_hundred_cases_and_seventeen_documents(
    tmp_path: Path,
) -> None:
    output = tmp_path / "retrieval-eval-real-fixture.json"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", "--output", str(output)],
        capture_output=True,
        text=True,
        check=False,
        env=_subprocess_env(),
    )
    assert result.returncode == 0, result.stderr

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["case_count"] == 100
    assert report["corpus"]["document_count"] == 17


def _run_gated_eval(tmp_path: Path, *gate_flags: str) -> subprocess.CompletedProcess[str]:
    fixture = _write_covering_fixture(tmp_path)
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--dry-run",
            "--fixture",
            str(fixture),
            "--output",
            str(tmp_path / "gated.json"),
            *gate_flags,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_subprocess_env(),
    )


def test_gate_flags_report_combined_metric_violations(tmp_path: Path) -> None:
    result = _run_gated_eval(
        tmp_path,
        "--fail-under-mrr",
        "1.0",
        "--fail-under-doc-mrr",
        "1.0",
        "--fail-under-recall",
        "1.0",
    )
    assert result.returncode == 1, result.stdout
    assert "section-level MRR" in result.stderr
    assert "document-level MRR" in result.stderr
    assert "document-level Recall@5" in result.stderr


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--fail-under-mrr", "nan"),
        ("--fail-under-doc-mrr", "inf"),
        ("--fail-under-recall", "-0.01"),
        ("--fail-under-recall", "1.01"),
        ("--fail-over-latency-p95", "-1"),
        ("--fail-over-latency-p95", "nan"),
        ("--fail-over-latency-p95", "inf"),
    ],
)
def test_gate_flags_reject_non_finite_and_out_of_range_values(
    flag: str, value: str
) -> None:
    module = load_module()
    with pytest.raises(SystemExit) as exc_info:
        module._parse_args([flag, value])
    assert exc_info.value.code == 2


def test_gate_flag_ranges_include_metric_and_latency_boundaries() -> None:
    module = load_module()
    args = module._parse_args(
        [
            "--fail-under-mrr",
            "0",
            "--fail-under-doc-mrr",
            "1",
            "--fail-under-recall",
            "1",
            "--fail-over-latency-p95",
            "0",
        ]
    )
    assert args.fail_under_mrr == 0.0
    assert args.fail_under_doc_mrr == 1.0
    assert args.fail_under_recall == 1.0
    assert args.fail_over_latency_p95 == 0.0


def test_launch_gate_equality_passes_and_all_strict_violations_are_reported() -> None:
    module = load_module()
    report = {
        "document_level": {"mrr": 0.6, "recall_at_5": 0.7},
        "section_level": {"mrr": 0.5},
        "latency_ms": {"p95": 10},
    }
    assert module.launch_gate_failures(
        report,
        min_section_mrr=0.5,
        min_document_mrr=0.6,
        min_document_recall=0.7,
        max_latency_p95=10.0,
    ) == []

    failures = module.launch_gate_failures(
        report,
        min_section_mrr=math.nextafter(0.5, math.inf),
        min_document_mrr=math.nextafter(0.6, math.inf),
        min_document_recall=math.nextafter(0.7, math.inf),
        max_latency_p95=math.nextafter(10.0, -math.inf),
    )
    assert len(failures) == 4
    assert "section-level MRR" in failures[0]
    assert "document-level MRR" in failures[1]
    assert "document-level Recall@5" in failures[2]
    assert "p95 latency" in failures[3]


def test_gate_flags_pass_when_thresholds_are_met(tmp_path: Path) -> None:
    result = _run_gated_eval(
        tmp_path,
        "--fail-under-mrr",
        "0.0",
        "--fail-under-doc-mrr",
        "0.0",
        "--fail-under-recall",
        "0.0",
        "--fail-over-latency-p95",
        "600000",
    )
    assert result.returncode == 0, result.stderr
    assert "FAIL:" not in result.stderr


def _tiny_corpus():
    """Two in-memory documents; no filesystem, no embeddings."""
    from cowork_agent.integrations.rag.knowledge_base import KnowledgeChunk, KnowledgeDocument

    bodies = {
        ("doc-a", "Căn cước"): "Thủ tục cấp lại thẻ căn cước công dân tại công an.",
        ("doc-b", "Hộ chiếu"): "Thủ tục xin cấp hộ chiếu phổ thông tại cơ quan xuất nhập cảnh.",
    }
    return tuple(
        KnowledgeDocument(
            document_id=document_id,
            title=document_id,
            source_url=f"{document_id}.md",
            chunks=(
                KnowledgeChunk(
                    chunk_id=f"{document_id}#0",
                    document_id=document_id,
                    document_title=document_id,
                    section=section,
                    text=text,
                    source_url=f"{document_id}.md",
                    tenant_id="local",
                ),
            ),
        )
        for (document_id, section), text in bodies.items()
    )


def _subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _corpus_sections() -> dict[str, list[str]]:
    """Real section vocabulary, so the generated fixture satisfies rule 5."""
    from cowork_agent.integrations.rag.knowledge_base import load_corpus

    return {
        document.document_id: list(
            dict.fromkeys(
                chunk.section for chunk in document.chunks if chunk.section is not None
            )
        )
        for document in load_corpus(CORPUS_DIR, tenant_id="local")
    }


def _write_covering_fixture(tmp_path: Path) -> Path:
    """Build a golden set that satisfies loader rule 6 for the real corpus.

    The harness always corpus-validates, so a smoke fixture has to cover
    every document with every answerable probe. Labels are generated from
    the corpus itself; this proves the plumbing, not retrieval quality.
    """
    sections = _corpus_sections()
    cases: list[dict[str, object]] = []
    for document_id, document_sections in sorted(sections.items()):
        for probe in ("lexical", "semantic", "mixed"):
            cases.append(
                {
                    "id": f"q-{len(cases) + 1:03d}",
                    "query": f"{document_id} {probe} probe {len(cases) + 1}",
                    "probe": probe,
                    "expected_document_ids": [document_id],
                    "expected_sections": document_sections[:1],
                    "email_body": None,
                    "notes": "generated smoke case",
                }
            )
    cases.append(
        {
            "id": f"q-{len(cases) + 1:03d}",
            "query": "generated smoke case the corpus cannot answer",
            "probe": "unanswerable",
            "expected_document_ids": [],
            "expected_sections": [],
            "email_body": None,
            "notes": "generated smoke case",
        }
    )
    target = tmp_path / "retrieval_golden.json"
    target.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    return target
