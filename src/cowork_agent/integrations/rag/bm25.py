"""Tenant-scoped, in-memory BM25 lexical retrieval for knowledge chunks."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from typing import Final

from .knowledge_base import KnowledgeChunk

_TOKEN_PATTERN: Final = re.compile(r"[^\W_]+", re.UNICODE)
_K1: Final = 1.5
_B: Final = 0.75


class BM25SearchAdapter:
    """Rank knowledge chunks lexically without crossing tenant boundaries.

    The ACL filter is applied before document statistics and scoring, so a
    caller cannot infer another tenant's corpus from scores or rankings.
    """

    def __init__(self, chunks: Sequence[KnowledgeChunk]) -> None:
        self._chunks = tuple(chunks)
        self._term_frequencies = tuple(Counter(_tokenize(chunk.text)) for chunk in self._chunks)
        self._document_lengths = tuple(
            sum(term_frequencies.values()) for term_frequencies in self._term_frequencies
        )

    def search(self, query: str, *, tenant_id: str, top_k: int) -> tuple[tuple[str, float], ...]:
        """Return positive-score chunks ranked by BM25 score then chunk ID."""
        query_terms = tuple(dict.fromkeys(_tokenize(query)))
        if not query_terms or top_k <= 0:
            return ()

        # ACL first: only tenant-visible chunks contribute to any statistics.
        allowed = tuple(
            (chunk, term_frequencies, document_length)
            for chunk, term_frequencies, document_length in zip(
                self._chunks, self._term_frequencies, self._document_lengths, strict=True
            )
            if chunk.tenant_id == tenant_id
        )
        if not allowed:
            return ()

        document_count = len(allowed)
        average_length = sum(item[2] for item in allowed) / document_count
        document_frequencies = {
            term: sum(term in term_frequencies for _, term_frequencies, _ in allowed)
            for term in query_terms
        }
        scores = (
            (
                chunk.chunk_id,
                _score(
                    query_terms,
                    term_frequencies,
                    document_length,
                    document_frequencies,
                    document_count,
                    average_length,
                ),
            )
            for chunk, term_frequencies, document_length in allowed
        )
        return tuple(
            (chunk_id, score)
            for chunk_id, score in sorted(scores, key=lambda item: (-item[1], item[0]))
            if score > 0
        )[:top_k]


def _tokenize(text: str) -> tuple[str, ...]:
    """Extract Unicode words, ignoring Markdown punctuation and case."""
    return tuple(token.casefold() for token in _TOKEN_PATTERN.findall(text))


def _score(
    query_terms: Sequence[str],
    term_frequencies: Counter[str],
    document_length: int,
    document_frequencies: dict[str, int],
    document_count: int,
    average_length: float,
) -> float:
    score = 0.0
    for term in query_terms:
        frequency = term_frequencies[term]
        if not frequency:
            continue
        document_frequency = document_frequencies[term]
        inverse_document_frequency = math.log(
            1 + (document_count - document_frequency + 0.5) / (document_frequency + 0.5)
        )
        normalization = _K1 * (1 - _B + _B * document_length / average_length)
        score += inverse_document_frequency * frequency * (_K1 + 1) / (frequency + normalization)
    return score
