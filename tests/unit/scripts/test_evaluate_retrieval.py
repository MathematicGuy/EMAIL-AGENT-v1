"""Metric math and dry-run plumbing for the retrieval harness (SPEC-rag §6.4).

The metric tests build CaseResults by hand: no corpus, no embedder, no
filesystem. Only the two subprocess tests touch the real corpus.
"""

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path

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
):
    return module.CaseResult(
        case_id=case_id,
        probe=probe,
        expected_document_ids=expected_document_ids,
        expected_sections=expected_sections,
        returned_document_ids=tuple(document_id for document_id, _ in returned),
        returned_sections=tuple(section for _, section in returned),
        retrieval_status=retrieval_status,
        latency_ms=latency_ms,
    )


def _filler(count: int, start: int = 1) -> tuple[tuple[str, str | None], ...]:
    """`count` irrelevant results, so ranks can be positioned exactly."""
    return tuple((f"doc-x{index}", f"Noise {index}") for index in range(start, start + count))


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
    for retriever in ("dense", "bm25", "hybrid"):
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
    assert report["section_level"]["excluded_case_count"] == 0
    assert report["by_probe"].keys() == {"lexical", "semantic", "mixed"}
    assert report["abstention"]["case_count"] == 1
    assert set(report["latency_ms"]) == {"p50", "p95"}

    # Privacy: identifiers and metrics only, never query or chunk text.
    for case in json.loads(fixture.read_text(encoding="utf-8")):
        assert case["query"] not in serialized
    assert '"query"' not in serialized
    assert '"text"' not in serialized


def test_fail_under_mrr_gates_on_section_level(tmp_path: Path) -> None:
    fixture = _write_covering_fixture(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--dry-run",
            "--fixture",
            str(fixture),
            "--output",
            str(tmp_path / "gated.json"),
            "--fail-under-mrr",
            "1.01",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_subprocess_env(),
    )
    assert result.returncode == 1, result.stdout
    assert "section-level MRR" in result.stderr


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
