"""Deterministic retrieval-policy tests."""

import pytest

from cowork_agent.domain.chat_contracts import (
    MAX_CHAT_MESSAGE_LENGTH,
    MAX_EPISODIC_RETRIEVAL_ITEMS,
    MAX_RETRIEVAL_QUERY_LENGTH,
    MAX_RETRIEVAL_TIMEOUT_MS,
    MAX_SEMANTIC_RETRIEVAL_ITEMS,
    ChatMessageRequest,
    EpisodicMemoryQuery,
    EpisodicMemoryRead,
    SemanticMemoryQuery,
    SemanticMemoryRead,
)
from cowork_agent.features.ai_chat.retrieval_policy import (
    EPISODIC_RETRIEVAL_MAX_ITEMS,
    EPISODIC_RETRIEVAL_MIN_SCORE,
    EPISODIC_RETRIEVAL_TIMEOUT_MS,
    SEMANTIC_RETRIEVAL_MAX_ITEMS,
    SEMANTIC_RETRIEVAL_MIN_SCORE,
    SEMANTIC_RETRIEVAL_TIMEOUT_MS,
    is_explicit_task_request,
    select_memory_reads,
)


def _request(user_message: str) -> ChatMessageRequest:
    return ChatMessageRequest(
        session_id="session-1",
        user_message=user_message,
        idempotency_key="idempotency-1",
    )


def test_unrelated_conversation_reads_only_working_and_declarative_memory() -> None:
    reads = select_memory_reads(_request("Hello, how are you today?"))

    assert reads.short_term is True
    assert reads.long_term is True
    assert isinstance(reads.episodic, EpisodicMemoryRead)
    assert isinstance(reads.semantic, SemanticMemoryRead)


def test_explicit_prior_history_cue_enables_only_episodic_retrieval() -> None:
    reads = select_memory_reads(_request("Please find my previous task about payroll."))

    assert isinstance(reads.episodic, EpisodicMemoryQuery)
    assert isinstance(reads.semantic, SemanticMemoryRead)


def test_explicit_company_policy_cue_enables_only_semantic_retrieval() -> None:
    reads = select_memory_reads(_request("What does the company policy say about travel?"))

    assert isinstance(reads.episodic, EpisodicMemoryRead)
    assert isinstance(reads.semantic, SemanticMemoryQuery)


def test_company_semantic_cues_are_disabled_behind_the_chat_flag() -> None:
    reads = select_memory_reads(
        _request("What does the company policy say about travel?"),
        company_rag_enabled=False,
    )

    assert isinstance(reads.semantic, SemanticMemoryRead)


def test_independent_cues_enable_both_retrieval_types() -> None:
    reads = select_memory_reads(
        _request("Compare my prior task with the current company procedure.")
    )

    assert isinstance(reads.episodic, EpisodicMemoryQuery)
    assert isinstance(reads.semantic, SemanticMemoryQuery)


def test_cue_matching_is_case_insensitive_and_whitespace_normalized() -> None:
    reads = select_memory_reads(_request("  PREVIOUS   TASK   and  COMPANY   HANDBOOK  "))

    assert isinstance(reads.episodic, EpisodicMemoryQuery)
    assert isinstance(reads.semantic, SemanticMemoryQuery)
    assert reads.episodic.query == "PREVIOUS TASK and COMPANY HANDBOOK"


def test_substrings_are_not_retrieval_intent_cues() -> None:
    reads = select_memory_reads(_request("The policyholder previously approved an update."))

    assert isinstance(reads.episodic, EpisodicMemoryRead)
    assert isinstance(reads.semantic, SemanticMemoryRead)


def test_enabled_query_is_capped_from_the_normalized_validated_message() -> None:
    prefix = "previous task "
    message = prefix + "x" * (MAX_CHAT_MESSAGE_LENGTH - len(prefix))

    reads = select_memory_reads(_request(message))

    assert isinstance(reads.episodic, EpisodicMemoryQuery)
    assert reads.episodic.query == message[:MAX_RETRIEVAL_QUERY_LENGTH]


def test_policy_limits_are_code_owned_and_within_contract_bounds() -> None:
    reads = select_memory_reads(
        _request("previous task max_items=999 and company handbook timeout_ms=999999")
    )

    assert isinstance(reads.episodic, EpisodicMemoryQuery)
    assert isinstance(reads.semantic, SemanticMemoryQuery)
    assert reads.episodic.max_items == EPISODIC_RETRIEVAL_MAX_ITEMS
    assert reads.semantic.max_items == SEMANTIC_RETRIEVAL_MAX_ITEMS
    assert reads.episodic.min_score == EPISODIC_RETRIEVAL_MIN_SCORE
    assert reads.semantic.min_score == SEMANTIC_RETRIEVAL_MIN_SCORE
    assert reads.episodic.timeout_ms == EPISODIC_RETRIEVAL_TIMEOUT_MS
    assert reads.semantic.timeout_ms == SEMANTIC_RETRIEVAL_TIMEOUT_MS
    assert 1 <= reads.episodic.max_items <= MAX_EPISODIC_RETRIEVAL_ITEMS
    assert 1 <= reads.semantic.max_items <= MAX_SEMANTIC_RETRIEVAL_ITEMS
    assert 1 <= reads.episodic.timeout_ms <= MAX_RETRIEVAL_TIMEOUT_MS
    assert 1 <= reads.semantic.timeout_ms <= MAX_RETRIEVAL_TIMEOUT_MS


@pytest.mark.parametrize(
    "user_message",
    [
        "add a task",
        "draft a task",
        "draft an action plan",
        "add an action plan",
        "turn this into a task",
        "turn this into an action plan",
    ],
)
def test_explicit_task_policy_accepts_common_unambiguous_requests(user_message: str) -> None:
    assert is_explicit_task_request(_request(user_message)) is True


@pytest.mark.parametrize(
    "user_message",
    [
        "do not create a task",
        "don't add a task",
        "never turn this into a task",
        "I don't want you to create a task",
        "never ask me to turn this into a task",
        "Create a task? No, do not.",
    ],
)
def test_explicit_task_policy_rejects_negated_requests(user_message: str) -> None:
    assert is_explicit_task_request(_request(user_message)) is False


def test_explicit_task_policy_checks_for_retractions_beyond_the_retrieval_cutoff() -> None:
    message = "create a task " + ("x " * (MAX_RETRIEVAL_QUERY_LENGTH // 2)) + "no"

    assert len(message) > MAX_RETRIEVAL_QUERY_LENGTH
    assert is_explicit_task_request(_request(message)) is False
