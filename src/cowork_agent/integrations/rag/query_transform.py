"""Query Transformation Layer: Multi-Query Expansion & HyDE (Hypothetical Document Embeddings)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class TransformedQuery:
    """Container for transformed query variations."""

    original_query: str
    expanded_queries: tuple[str, ...]
    hypothetical_docs: tuple[str, ...] = ()


class QueryTransformerPort(Protocol):
    """Port interface for query transformation providers."""

    async def transform(
        self, query: str, knowledge_gaps: tuple[str, ...] = ()
    ) -> TransformedQuery:
        """Transform a raw query into expanded queries and hypothetical documents."""
        ...


class RuleBasedQueryTransformer:
    """Deterministic rule-based query transformer for Multi-Query & HyDE."""

    def __init__(
        self, *, enable_hyde: bool = True, num_expansions: int = 3, num_hyde: int = 3
    ) -> None:
        self._enable_hyde = enable_hyde
        self._num_expansions = num_expansions
        self._num_hyde = num_hyde

    async def transform(
        self, query: str, knowledge_gaps: tuple[str, ...] = ()
    ) -> TransformedQuery:
        expansions: list[str] = []
        for gap in knowledge_gaps:
            if gap and gap not in expansions and gap != query:
                expansions.append(f"{query} {gap}")

        # Add domain paraphrases if expansion pool is small
        if len(expansions) < self._num_expansions:
            expansions.append(f"Quy trình thủ tục {query}")
        if len(expansions) < self._num_expansions:
            expansions.append(f"Hướng dẫn quy định {query}")
        if len(expansions) < self._num_expansions:
            expansions.append(f"Chi tiết nội dung {query}")

        hypothetical_docs: list[str] = []
        if self._enable_hyde:
            hypothetical_docs = [
                f"Tài liệu quy định chi tiết về: {query}.",
                f"Hướng dẫn thực hiện và quy trình tổng quan cho: {query}.",
                f"Báo cáo giải trình và câu trả lời tham chiếu cho: {query}.",
            ][: self._num_hyde]

        return TransformedQuery(
            original_query=query,
            expanded_queries=tuple(expansions[: self._num_expansions]),
            hypothetical_docs=tuple(hypothetical_docs),
        )


class LLMQueryTransformer:
    """LLM-backed query transformer generating Multi-Query expansions and Multi-HyDE docs."""

    def __init__(
        self,
        llm_provider: Any,
        *,
        enable_hyde: bool = True,
        num_expansions: int = 3,
        num_hyde: int = 3,
    ) -> None:
        self._llm = llm_provider
        self._enable_hyde = enable_hyde
        self._num_expansions = num_expansions
        self._num_hyde = num_hyde
        self._fallback = RuleBasedQueryTransformer(
            enable_hyde=enable_hyde,
            num_expansions=num_expansions,
            num_hyde=num_hyde,
        )

    async def transform(
        self, query: str, knowledge_gaps: tuple[str, ...] = ()
    ) -> TransformedQuery:
        if not self._enable_hyde:
            return await self._fallback.transform(query, knowledge_gaps)

        try:
            prompt = (
                f"Tạo đúng {self._num_hyde} câu trả lời giả định cho câu hỏi: '{query}'.\n"
                'Trả về kết quả dạng JSON array duy nhất, ví dụ: ["câu 1", "câu 2", "câu 3"].'
            )
            raw_response = await self._llm.generate(prompt)
            raw_text = getattr(raw_response, "text", str(raw_response)).strip()

            start_idx = raw_text.find("[")
            end_idx = raw_text.rfind("]")
            if start_idx != -1 and end_idx != -1 and start_idx < end_idx:
                parsed = json.loads(raw_text[start_idx : end_idx + 1])
                if isinstance(parsed, list):
                    hyde_docs = [str(item).strip() for item in parsed if item][: self._num_hyde]
                else:
                    hyde_docs = []
            else:
                hyde_docs = []

            fallback_res = await self._fallback.transform(query, knowledge_gaps)
            if not hyde_docs:
                hyde_docs = list(fallback_res.hypothetical_docs)

            return TransformedQuery(
                original_query=query,
                expanded_queries=fallback_res.expanded_queries,
                hypothetical_docs=tuple(hyde_docs[: self._num_hyde]),
            )
        except Exception:
            return await self._fallback.transform(query, knowledge_gaps)

