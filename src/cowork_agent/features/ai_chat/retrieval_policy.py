"""Deterministic intent policy for optional chat-memory retrieval."""

from __future__ import annotations

from string import punctuation

from cowork_agent.domain.chat_contracts import (
    MAX_RETRIEVAL_QUERY_LENGTH,
    ChatMessageRequest,
    EpisodicMemoryQuery,
    EpisodicMemoryRead,
    MemoryReadOptions,
    SemanticMemoryQuery,
    SemanticMemoryRead,
)

EPISODIC_RETRIEVAL_MAX_ITEMS = 5
EPISODIC_RETRIEVAL_MIN_SCORE = 0.6
EPISODIC_RETRIEVAL_TIMEOUT_MS = 500
SEMANTIC_RETRIEVAL_MAX_ITEMS = 5
SEMANTIC_RETRIEVAL_MIN_SCORE = 0.6
SEMANTIC_RETRIEVAL_TIMEOUT_MS = 500

_EPISODIC_CUES = frozenset(
    {
        "previous task",
        "prior task",
        "past task",
        "related work",
        "earlier task",
    }
)
_SEMANTIC_CUES = frozenset(
    {
        "company policy",
        "company procedure",
        "company handbook",
        "employee handbook",
        "our policy",
        "our procedure",
    }
)


def _normalized_query(user_message: str) -> str:
    return " ".join(user_message.split())[:MAX_RETRIEVAL_QUERY_LENGTH]


def _contains_cue(normalized_message: str, cues: frozenset[str]) -> bool:
    tokens = tuple(token.strip(punctuation) for token in normalized_message.casefold().split())
    return any(
        any(
            tokens[index : index + len(cue.split())] == tuple(cue.split())
            for index in range(len(tokens) - len(cue.split()) + 1)
        )
        for cue in cues
    )


def select_memory_reads(request: ChatMessageRequest) -> MemoryReadOptions:
    """Select optional retrieval only for deterministic, explicit user intent."""

    query = _normalized_query(request.user_message)
    episodic = (
        EpisodicMemoryQuery(
            query=query,
            max_items=EPISODIC_RETRIEVAL_MAX_ITEMS,
            min_score=EPISODIC_RETRIEVAL_MIN_SCORE,
            timeout_ms=EPISODIC_RETRIEVAL_TIMEOUT_MS,
        )
        if _contains_cue(query, _EPISODIC_CUES)
        else EpisodicMemoryRead(enabled=False, retrieval_eligible_only=True, max_items=1)
    )
    semantic = (
        SemanticMemoryQuery(
            query=query,
            max_items=SEMANTIC_RETRIEVAL_MAX_ITEMS,
            min_score=SEMANTIC_RETRIEVAL_MIN_SCORE,
            timeout_ms=SEMANTIC_RETRIEVAL_TIMEOUT_MS,
        )
        if _contains_cue(query, _SEMANTIC_CUES)
        else SemanticMemoryRead(enabled=False)
    )
    return MemoryReadOptions(
        short_term=True,
        long_term=True,
        episodic=episodic,
        semantic=semantic,
    )
