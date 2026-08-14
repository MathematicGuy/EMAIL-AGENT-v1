"""Tests for the in-repo Semantic Memory (RAG) adapter (V1-M3 T3.1/T3.2)."""

import asyncio
from pathlib import Path

import pytest

from cowork_agent.domain.target_contracts import (
    RetrievalFilters,
    RetrievalLimits,
    RetrievalStatus,
    SemanticRetrievalRequest,
)
from cowork_agent.integrations.rag import NullSemanticMemory
from cowork_agent.integrations.rag.fakes import HashingEmbedder, SlowEmbedder
from cowork_agent.integrations.rag.knowledge_base import load_corpus
from cowork_agent.integrations.rag.memory import InRepoSemanticMemory

REPO_ROOT = Path(__file__).resolve().parents[4]
CORPUS_DIR = REPO_ROOT / "data" / "extracted"


def _request(
    *,
    query: str = "đăng ký tạm trú",
    top_k: int = 5,
    min_score: float = 0.0,
) -> SemanticRetrievalRequest:
    return SemanticRetrievalRequest(
        run_id="run-1",
        user_id="user@example.com",
        query=query,
        knowledge_gaps=(),
        filters=RetrievalFilters(document_status=("ready",)),
        limits=RetrievalLimits(top_k=top_k, min_score=min_score, timeout_ms=1500),
    )


def _built_memory(corpus_dir: Path = CORPUS_DIR):
    documents = load_corpus(corpus_dir)
    memory = InRepoSemanticMemory(documents, HashingEmbedder())
    asyncio.run(memory.build_index())
    return memory


def test_load_corpus_reads_the_committed_documents() -> None:
    documents = load_corpus(CORPUS_DIR)
    assert [doc.document_id for doc in documents] == [
        "01-2021-nd-cp-283247",
        "31-2024-qh15-523642",
        "41-2024-qh15-557190",
        "49-2019-qh14-402073",
        "cap_lai_cccd",
        "chi-tiet-thu-tuc-1-004194-1786097965866",
        "chi-tiet-thu-tuc-1-115132-1786096253281",
        "chi-tiet-thu-tuc-1-115970-1786097982328",
        "chi-tiet-thu-tuc-1-116194-1786096137126",
        "chi-tiet-thu-tuc-2-001194-1786096928665",
        "chi-tiet-thu-tuc-3-000228-1786096860852",
        "dang-ky-tam-tru",
        "dang_ky_ket_hon",
        "dang_ky_xe",
        "huong_dan_nop_ho_so_dai_hoc_vinuni",
        "thu_tuc_dang_ky_bhxh_luatvietnam",
        "thue_dien_tu",
    ]
    for document in documents:
        assert document.title
        assert document.chunks
        for chunk in document.chunks:
            assert chunk.text.strip()
            assert chunk.document_id == document.document_id
            assert chunk.source_url.startswith("data/extracted/")


def test_load_corpus_chunks_by_h2_sections(tmp_path: Path) -> None:
    doc = tmp_path / "policy.md"
    doc.write_text(
        "# Policy\n\nIntro line.\n\n## First Rule\n\nAlpha body.\n\n## Second Rule\n\nBeta body.\n",
        encoding="utf-8",
    )
    (document,) = load_corpus(tmp_path)
    assert document.title == "Policy"
    sections = [chunk.section for chunk in document.chunks]
    assert "First Rule" in sections and "Second Rule" in sections
    assert any(chunk.text == "Alpha body." for chunk in document.chunks)


def test_load_corpus_rejects_missing_or_empty_dir(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        load_corpus(tmp_path / "missing")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="no markdown"):
        load_corpus(empty)


def test_ranking_min_score_and_top_k() -> None:
    memory = _built_memory()
    response = asyncio.run(memory.retrieve(_request(query="VNeID cấp lại CCCD", top_k=2)))
    assert response.retrieval_status is RetrievalStatus.SUCCESS
    assert len(response.chunks) <= 2
    scores = [chunk.relevance_score for chunk in response.chunks]
    assert scores == sorted(scores, reverse=True)

    filtered = asyncio.run(memory.retrieve(_request(query="VNeID cấp lại CCCD", min_score=0.99)))
    assert filtered.retrieval_status is RetrievalStatus.NO_RESULTS


def test_null_semantic_memory_returns_structured_no_results() -> None:
    response = asyncio.run(NullSemanticMemory().retrieve(_request()))
    assert response.retrieval_status is RetrievalStatus.NO_RESULTS
    assert response.chunks == ()
    assert response.query_id.startswith("q_")


def test_timeout_status_when_embedder_times_out() -> None:
    documents = load_corpus(CORPUS_DIR, tenant_id="local")
    memory = InRepoSemanticMemory(documents, HashingEmbedder())
    asyncio.run(memory.build_index())
    memory._embedder = SlowEmbedder()
    response = asyncio.run(memory.retrieve(_request()))
    assert response.retrieval_status is RetrievalStatus.TIMEOUT
    assert response.chunks == ()


def test_hashing_embedder_is_deterministic() -> None:
    first = asyncio.run(HashingEmbedder().embed(("xin chào",)))
    second = asyncio.run(HashingEmbedder().embed(("xin chào",)))
    assert first == second
