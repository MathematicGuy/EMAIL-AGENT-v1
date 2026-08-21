"""Bounded query-rewrite contract for retrieve-first Email RAG."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

MAX_QUERY_REWRITE_BODY_CHARS = 1_200
MAX_RETRIEVAL_QUERY_CHARS = 300


@dataclass(frozen=True, slots=True)
class QueryRewriteMessage:
    subject: str
    body_excerpt: str


@dataclass(frozen=True, slots=True)
class QueryRewriteInput:
    candidate_action_items: tuple[str, ...]
    knowledge_gaps: tuple[str, ...]
    messages: tuple[QueryRewriteMessage, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "candidateActionItems": list(self.candidate_action_items),
            "knowledgeGaps": list(self.knowledge_gaps),
            "messages": [
                {"subject": message.subject, "bodyExcerpt": message.body_excerpt}
                for message in self.messages
            ],
        }


class RetrievalQueryRewriterPort(Protocol):
    async def rewrite(self, payload: QueryRewriteInput) -> str | None: ...


def build_query_rewrite_input(
    *,
    candidate_action_items: Sequence[str | None],
    knowledge_gaps: Sequence[str],
    messages: Sequence[tuple[str, str]],
) -> QueryRewriteInput:
    return QueryRewriteInput(
        candidate_action_items=tuple(item for item in candidate_action_items if item),
        knowledge_gaps=tuple(dict.fromkeys(gap for gap in knowledge_gaps if gap)),
        messages=tuple(
            QueryRewriteMessage(subject=subject, body_excerpt=body[:MAX_QUERY_REWRITE_BODY_CHARS])
            for subject, body in messages
        ),
    )


def deterministic_query(payload: QueryRewriteInput) -> str:
    """Privacy-bounded fallback used only after a rewrite failure."""
    options = (*payload.candidate_action_items, *(message.subject for message in payload.messages))
    for value in options:
        normalized = " ".join(value.split())
        if normalized:
            return normalized[:MAX_RETRIEVAL_QUERY_CHARS]
    return "email cần xử lý"
