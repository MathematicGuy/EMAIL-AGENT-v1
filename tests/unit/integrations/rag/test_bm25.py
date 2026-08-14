"""Unit tests for the BM25 lexical search adapter."""

from cowork_agent.integrations.rag.bm25 import BM25SearchAdapter
from cowork_agent.integrations.rag.knowledge_base import KnowledgeChunk


def _chunk(chunk_id: str, text: str) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        document_id="document",
        document_title="Document",
        section=None,
        text=text,
        source_url="knowledge/document.md",
    )


def test_search_ranks_exact_english_and_vietnamese_terms_despite_markdown_case_and_punctuation(
) -> None:
    adapter = BM25SearchAdapter(
        (
            _chunk("matching", "## VNeID guide\n\nCấp lại **CCCD** through VNeID."),
            _chunk("other", "## Marriage\n\nRegister a marriage certificate."),
        )
    )

    results = adapter.search("vneid, CẤP LẠI cccd!", top_k=2)

    assert [chunk_id for chunk_id, _ in results] == ["matching"]
    assert results[0][1] > 0


def test_search_orders_equal_scores_by_chunk_id_and_respects_top_k() -> None:
    adapter = BM25SearchAdapter(
        (
            _chunk("zeta", "same exact terms"),
            _chunk("alpha", "same exact terms"),
            _chunk("extra", "same exact terms"),
        )
    )

    results = adapter.search("same exact terms", top_k=2)

    assert [chunk_id for chunk_id, _ in results] == ["alpha", "extra"]
