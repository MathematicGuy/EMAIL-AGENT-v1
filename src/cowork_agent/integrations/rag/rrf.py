"""Reciprocal rank fusion for ranked retrieval candidate IDs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

RRF_K: Final = 60


@dataclass(frozen=True, slots=True)
class FusedCandidate:
    """One text-free retrieval candidate ordered by its fused score."""

    chunk_id: str
    score: float


class ReciprocalRankFusion:
    """Fuse dense and lexical rankings using the fixed RRF constant."""

    def fuse(
        self,
        *,
        dense_results: Sequence[tuple[str, float]],
        bm25_results: Sequence[tuple[str, float]],
    ) -> tuple[FusedCandidate, ...]:
        """Return unique chunk IDs sorted by descending RRF score then ID.

        Scores attached to the input rankings are deliberately ignored: RRF
        uses only their one-based positions. A duplicate ID in one ranking
        contributes once, at the position of its first occurrence.
        """
        scores: dict[str, float] = {}
        for results in (dense_results, bm25_results):
            seen_ids: set[str] = set()
            for rank, (chunk_id, _) in enumerate(results, start=1):
                if chunk_id in seen_ids:
                    continue
                seen_ids.add(chunk_id)
                scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (RRF_K + rank)

        return tuple(
            FusedCandidate(chunk_id=chunk_id, score=score)
            for chunk_id, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        )
