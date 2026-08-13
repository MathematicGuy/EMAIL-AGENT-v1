"""Query Transformation Layer: Multi-Query Expansion & HyDE (Hypothetical Document Embeddings)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class TransformedQuery:
    """Container for transformed query variations."""

    original_query: str
    expanded_queries: tuple[str, ...]
    hypothetical_doc: str | None


class QueryTransformerPort(Protocol):
    """Port interface for query transformation providers."""

    async def transform(self, query: str, knowledge_gaps: tuple[str, ...] = ()) -> TransformedQuery:
        """Transform a raw query into expanded queries and hypothetical document."""
        ...


class RuleBasedQueryTransformer:
    """Deterministic rule-based query transformer for Multi-Query & HyDE."""

    def __init__(self, *, enable_hyde: bool = True, num_expansions: int = 3) -> None:
        self._enable_hyde = enable_hyde
        self._num_expansions = num_expansions

    async def transform(self, query: str, knowledge_gaps: tuple[str, ...] = ()) -> TransformedQuery:
        expansions = [query]
        for gap in knowledge_gaps:
            if gap and gap not in expansions:
                expansions.append(f"{query} {gap}")

        # Add domain paraphrases if expansion pool is small
        if len(expansions) < self._num_expansions:
            expansions.append(f"Quy trình thủ tục {query}")
            expansions.append(f"Hướng dẫn quy định {query}")

        hypothetical_doc = (
            f"Tài liệu quy định chi tiết về: {query}. "
            f"Nội dung hướng dẫn giải quyết các thắc mắc và yêu cầu liên quan đến {query}."
            if self._enable_hyde
            else None
        )

        return TransformedQuery(
            original_query=query,
            expanded_queries=tuple(expansions[: self._num_expansions + 1]),
            hypothetical_doc=hypothetical_doc,
        )
