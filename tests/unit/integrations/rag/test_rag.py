"""Tests for the in-repo Semantic Memory (RAG) adapter (V1-M3 T3.1/T3.2)."""

import asyncio
import json
from datetime import date
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
from cowork_agent.integrations.rag.knowledge_base import (
    KnowledgeChunk,
    KnowledgeDocument,
    allowed_chunk_indices,
    load_corpus,
)
from cowork_agent.integrations.rag.memory import InRepoSemanticMemory

REPO_ROOT = Path(__file__).resolve().parents[4]
CORPUS_DIR = REPO_ROOT / "data" / "extracted"


def _request(
    *,
    query: str = "đăng ký tạm trú",
    top_k: int = 5,
    min_score: float = 0.0,
    filters: RetrievalFilters | None = None,
) -> SemanticRetrievalRequest:
    return SemanticRetrievalRequest(
        run_id="run-1",
        user_id="user@example.com",
        query=query,
        knowledge_gaps=(),
        filters=filters if filters is not None else RetrievalFilters(document_status=("ready",)),
        limits=RetrievalLimits(top_k=top_k, min_score=min_score, timeout_ms=1500),
    )


def _filter_chunk(
    chunk_id: str,
    document_id: str,
    *,
    document_date: date | None = None,
    text: str = "body",
) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        document_id=document_id,
        document_title=document_id,
        section=None,
        text=text,
        source_url=f"{document_id}.md",
        document_date=document_date,
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
        "cap-lai-cccd",
        "chi-tiet-thu-tuc-1-004194-1786097965866",
        "chi-tiet-thu-tuc-1-115132-1786096253281",
        "chi-tiet-thu-tuc-1-115970-1786097982328",
        "chi-tiet-thu-tuc-1-116194-1786096137126",
        "chi-tiet-thu-tuc-2-001194-1786096928665",
        "chi-tiet-thu-tuc-3-000228-1786096860852",
        "dang-ky-ket-hon",
        "dang-ky-tam-tru",
        "dang-ky-xe",
        "design-machine-learning-systems",
        "huong-dan-nop-ho-so-dai-hoc-vinuni",
        "thu-tuc-dang-ky-bhxh-luatvietnam",
        "thue-dien-tu",
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
    alpha = next(chunk for chunk in document.chunks if chunk.section == "First Rule")
    assert alpha.text == "Policy\nFirst Rule\n\nAlpha body."


def test_load_corpus_copies_page_coordinates_and_omits_page_markers(
    tmp_path: Path,
) -> None:
    doc = tmp_path / "paged.md"
    doc.write_text(
        "# Title\n"
        "<!-- Page 1 -->\n"
        "First page body.\n"
        "\n"
        "<!-- Page 2 -->\n"
        "Second page body.\n",
        encoding="utf-8",
    )
    (document,) = load_corpus(tmp_path)
    covered: set[int] = set()
    for chunk in document.chunks:
        assert "<!-- Page" not in chunk.text
        if chunk.page_start is None:
            continue
        page_end = chunk.page_end if chunk.page_end is not None else chunk.page_start
        covered.update(range(chunk.page_start, page_end + 1))
    assert covered & {1, 2}


def test_load_corpus_leaves_page_coordinates_none_without_markers(
    tmp_path: Path,
) -> None:
    doc = tmp_path / "unpaged.md"
    doc.write_text("# Title\n\nJust a body.\n", encoding="utf-8")
    (document,) = load_corpus(tmp_path)
    assert document.chunks
    for chunk in document.chunks:
        assert chunk.page_start is None
        assert chunk.page_end is None


def test_load_corpus_strips_closed_frontmatter_from_chunk_text(tmp_path: Path) -> None:
    doc = tmp_path / "policy-file.md"
    doc.write_text(
        "---\n"
        "document_id: yaml-id-must-not-win\n"
        "title: Frontmatter Title\n"
        "source_file: Policy File.pdf\n"
        "extractor: pdf_native\n"
        "page_count: 2\n"
        "processed_at: 2026-08-16T00:00:00+00:00\n"
        "---\n"
        "\n"
        "# Body Heading\n"
        "\n"
        "Intro line.\n"
        "\n"
        "## First Rule\n"
        "\n"
        "Alpha body.\n",
        encoding="utf-8",
    )
    (document,) = load_corpus(tmp_path)
    assert document.document_id == "policy-file"
    assert document.title == "Body Heading"
    joined = "\n".join(chunk.text for chunk in document.chunks)
    for key in (
        "document_id:",
        "title:",
        "source_file:",
        "extractor:",
        "page_count:",
        "processed_at:",
    ):
        assert key not in joined
    assert "Frontmatter Title" not in joined
    alpha = next(chunk for chunk in document.chunks if chunk.section == "First Rule")
    assert alpha.text == "Body Heading\nFirst Rule\n\nAlpha body."


def test_load_corpus_joins_manifest_document_date_by_output_stem_onto_knowledge_chunk(
    tmp_path: Path,
) -> None:
    (tmp_path / "policy.md").write_text(
        "# Policy\n\nIntro line.\n\n## First Rule\n\nAlpha body.\n\n## Second Rule\n\nBeta body.\n",
        encoding="utf-8",
    )
    (tmp_path / "ingestion-manifest.json").write_text(
        json.dumps(
            {
                "Policy File.docx": {
                    "extractor": "docx",
                    "output": "policy.md",
                    "page_count": 1,
                    "processed_at": "",
                    "reason_code": None,
                    "sha256": "abc",
                    "source": "Policy File.docx",
                    "status": "succeeded",
                    "title": "",
                    "document_date": "2026-08-07",
                }
            }
        ),
        encoding="utf-8",
    )

    (document,) = load_corpus(tmp_path)

    assert document.chunks
    for chunk in document.chunks:
        assert chunk.document_date == date(2026, 8, 7)


def test_load_corpus_without_manifest_or_empty_field_leaves_document_date_none(
    tmp_path: Path,
) -> None:
    (tmp_path / "plain.md").write_text("# Title\n\nJust a body.\n", encoding="utf-8")
    (document,) = load_corpus(tmp_path)
    assert document.chunks
    for chunk in document.chunks:
        assert chunk.document_date is None

    (tmp_path / "dated.md").write_text("# Dated\n\nBody.\n", encoding="utf-8")
    (tmp_path / "ingestion-manifest.json").write_text(
        json.dumps(
            {
                "plain.txt": {
                    "extractor": "text",
                    "output": "plain.md",
                    "page_count": 1,
                    "processed_at": "",
                    "reason_code": None,
                    "sha256": "abc",
                    "source": "plain.txt",
                    "status": "succeeded",
                    "title": "",
                    "document_date": "",
                },
                "dated.docx": {
                    "extractor": "docx",
                    "output": "dated.md",
                    "page_count": 1,
                    "processed_at": "",
                    "reason_code": None,
                    "sha256": "def",
                    "source": "dated.docx",
                    "status": "succeeded",
                    "title": "",
                    "document_date": "not-a-date",
                },
            }
        ),
        encoding="utf-8",
    )
    documents = load_corpus(tmp_path)
    for document in documents:
        for chunk in document.chunks:
            assert chunk.document_date is None


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
    assert response.retrieval_status is RetrievalStatus.UNAVAILABLE
    assert response.chunks == ()
    assert response.query_id.startswith("q_")


def test_timeout_status_when_embedder_times_out() -> None:
    documents = load_corpus(CORPUS_DIR)
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


def test_allowed_chunk_indices_unconstrained_keeps_all() -> None:
    chunks = (
        _filter_chunk("a#0", "a", document_date=None),
        _filter_chunk("b#0", "b", document_date=date(2026, 8, 7)),
    )

    assert allowed_chunk_indices(chunks, RetrievalFilters()) == (0, 1)
    assert allowed_chunk_indices(chunks, RetrievalFilters(document_status=("ready",))) == (0, 1)


def test_allowed_chunk_indices_document_ids_keeps_only_those_ids() -> None:
    chunks = (
        _filter_chunk("a#0", "a"),
        _filter_chunk("b#0", "b"),
        _filter_chunk("b#1", "b"),
        _filter_chunk("c#0", "c"),
    )

    assert allowed_chunk_indices(chunks, RetrievalFilters(document_ids=("b",))) == (1, 2)


def test_allowed_chunk_indices_years_excludes_none_dates_and_other_years() -> None:
    chunks = (
        _filter_chunk("none#0", "none", document_date=None),
        _filter_chunk("y2025#0", "y2025", document_date=date(2025, 1, 1)),
        _filter_chunk("y2026#0", "y2026", document_date=date(2026, 8, 7)),
    )

    assert allowed_chunk_indices(chunks, RetrievalFilters(years=(2026,))) == (2,)


def test_allowed_chunk_indices_months_excludes_none_dates() -> None:
    chunks = (
        _filter_chunk("none#0", "none", document_date=None),
        _filter_chunk("july#0", "july", document_date=date(2026, 7, 1)),
        _filter_chunk("aug#0", "aug", document_date=date(2026, 8, 7)),
    )

    assert allowed_chunk_indices(chunks, RetrievalFilters(months=(8,))) == (2,)


def test_allowed_chunk_indices_ands_document_ids_and_years() -> None:
    chunks = (
        _filter_chunk("keep#0", "keep", document_date=date(2026, 8, 7)),
        _filter_chunk("keep#1", "keep", document_date=date(2025, 1, 1)),
        _filter_chunk("other#0", "other", document_date=date(2026, 8, 7)),
    )

    assert allowed_chunk_indices(
        chunks, RetrievalFilters(document_ids=("keep",), years=(2026,))
    ) == (0,)


def test_inrepo_retrieve_with_years_on_undated_corpus_returns_no_results_without_embed(
    tmp_path: Path,
) -> None:
    (tmp_path / "plain.md").write_text(
        "# Title\n\nJust a body about residency.\n", encoding="utf-8"
    )
    documents = load_corpus(tmp_path)
    embedder = _CountingEmbedder()
    memory = InRepoSemanticMemory(documents, embedder)
    asyncio.run(memory.build_index())
    embeds_after_build = embedder.calls

    response = asyncio.run(
        memory.retrieve(_request(query="residency", filters=RetrievalFilters(years=(1999,))))
    )

    assert response.retrieval_status is RetrievalStatus.NO_RESULTS
    assert response.chunks == ()
    assert embedder.calls == embeds_after_build


def test_inrepo_retrieve_copies_document_date_onto_semantic_chunk() -> None:
    dated = date(2026, 8, 7)
    documents = (
        KnowledgeDocument(
            "policy",
            "Policy",
            "policy.md",
            (
                _filter_chunk(
                    "policy#0",
                    "policy",
                    document_date=dated,
                    text="residency registration procedure",
                ),
            ),
        ),
    )
    memory = InRepoSemanticMemory(documents, HashingEmbedder())
    asyncio.run(memory.build_index())

    response = asyncio.run(memory.retrieve(_request(query="residency registration")))

    assert response.retrieval_status is RetrievalStatus.SUCCESS
    assert response.chunks
    assert all(chunk.document_date == dated for chunk in response.chunks)


class _CountingEmbedder:
    def __init__(self) -> None:
        self.calls = 0
        self._inner = HashingEmbedder()

    async def embed(self, texts, *, task: str = "retrieval.query"):
        self.calls += 1
        return await self._inner.embed(texts, task=task)
