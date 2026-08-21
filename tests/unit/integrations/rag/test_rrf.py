"""Unit tests for reciprocal rank fusion of retrieval candidate IDs."""

import pytest

from cowork_agent.integrations.rag.rrf import ReciprocalRankFusion


def test_fuse_uses_one_based_ranks_and_sums_scores_across_ranked_lists() -> None:
    fused = ReciprocalRankFusion().fuse(
        dense_results=(("dense-only", 0.9), ("shared", 0.8)),
        bm25_results=(("shared", 12.0), ("bm25-only", 10.0)),
    )

    assert [candidate.chunk_id for candidate in fused] == [
        "shared",
        "dense-only",
        "bm25-only",
    ]
    assert fused[0].score == pytest.approx(1 / 62 + 1 / 61)
    assert fused[1].score == pytest.approx(1 / 61)
    assert fused[2].score == pytest.approx(1 / 62)


def test_fuse_counts_a_duplicate_chunk_id_once_at_its_first_rank() -> None:
    fused = ReciprocalRankFusion().fuse(
        dense_results=(("alpha", 0.9), ("alpha", 0.8), ("beta", 0.7)),
        bm25_results=(("beta", 10.0),),
    )

    assert [candidate.chunk_id for candidate in fused] == ["beta", "alpha"]
    assert fused[0].score == pytest.approx(1 / 63 + 1 / 61)
    assert fused[1].score == pytest.approx(1 / 61)


def test_fuse_preserves_a_single_ranked_list() -> None:
    fused = ReciprocalRankFusion().fuse(
        dense_results=(),
        bm25_results=(("first", 4.0), ("second", 3.0)),
    )

    assert [candidate.chunk_id for candidate in fused] == ["first", "second"]
    assert [candidate.score for candidate in fused] == pytest.approx([1 / 61, 1 / 62])


def test_fuse_breaks_equal_scores_by_chunk_id_deterministically() -> None:
    fused = ReciprocalRankFusion().fuse(
        dense_results=(("zeta", 0.9), ("alpha", 0.8)),
        bm25_results=(("alpha", 10.0), ("zeta", 9.0)),
    )

    assert [candidate.chunk_id for candidate in fused] == ["alpha", "zeta"]
