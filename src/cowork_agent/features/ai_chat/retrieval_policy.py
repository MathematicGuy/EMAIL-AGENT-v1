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
_TASK_DIRECTIVE_VERBS = frozenset(
    {"create", "make", "add", "draft", "prepare", "build", "tạo", "lập", "lên"}
)
_TASK_FILLERS = frozenset({"a", "an", "the", "new", "một", "cho"})
_TASK_ARTICLES = frozenset({"a", "an", "the", "một"})
_TASK_NEGATION_MARKERS = (
    ("do", "not"),
    ("don't",),
    ("dont",),
    ("never",),
    ("no",),
    ("không", "cần"),
    ("đừng",),
    ("không",),
)
_TURN_TARGETS = frozenset({"this", "that", "it"})
_MAX_TASK_FILLERS = 2


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


def select_memory_reads(
    request: ChatMessageRequest, *, company_rag_enabled: bool = True
) -> MemoryReadOptions:
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
        if company_rag_enabled and _contains_cue(query, _SEMANTIC_CUES)
        else SemanticMemoryRead(enabled=False)
    )
    return MemoryReadOptions(
        short_term=True,
        long_term=True,
        episodic=episodic,
        semantic=semantic,
    )


def clarification_memory_reads() -> MemoryReadOptions:
    """Read conversational/profile context without retrieval-capable sources."""

    return MemoryReadOptions(
        short_term=True,
        long_term=True,
        episodic=EpisodicMemoryRead(enabled=False, retrieval_eligible_only=True, max_items=1),
        semantic=SemanticMemoryRead(enabled=False),
    )


def is_explicit_task_request(request: ChatMessageRequest) -> bool:
    """Allow task persistence only for a deterministic, explicit user request."""

    tokens = _authorization_tokens(request.user_message)
    return not _contains_task_negation(tokens) and (
        _contains_verb_task_directive(tokens) or _contains_turn_task_directive(tokens)
    )


def _authorization_tokens(user_message: str) -> tuple[str, ...]:
    """Normalize the validated full message without applying retrieval's length cap."""

    return tuple(
        token.strip(punctuation) for token in " ".join(user_message.split()).casefold().split()
    )


def _contains_task_negation(tokens: tuple[str, ...]) -> bool:
    return any(
        any(
            tokens[index : index + len(marker)] == marker
            for index in range(len(tokens) - len(marker) + 1)
        )
        for marker in _TASK_NEGATION_MARKERS
    )


def _contains_verb_task_directive(tokens: tuple[str, ...]) -> bool:
    return any(
        token in _TASK_DIRECTIVE_VERBS
        and _is_task_target(tokens, index + 1, _TASK_FILLERS, _MAX_TASK_FILLERS)
        for index, token in enumerate(tokens)
    )


def _contains_turn_task_directive(tokens: tuple[str, ...]) -> bool:
    return any(
        tokens[index + 1] in _TURN_TARGETS
        and tokens[index + 2] == "into"
        and _is_task_target(tokens, index + 3, _TASK_ARTICLES, 1)
        for index, token in enumerate(tokens[:-3])
        if token == "turn"
    )


def _is_task_target(
    tokens: tuple[str, ...], index: int, fillers: frozenset[str], max_fillers: int
) -> bool:
    while index < len(tokens) and max_fillers and tokens[index] in fillers:
        index += 1
        max_fillers -= 1
    return (
        tokens[index : index + 1] in (("task",), ("tasks",), ("todo",), ("to-do",))
        or tokens[index : index + 2] in (("action", "plan"), ("kế", "hoạch"), ("to", "do"))
    )
