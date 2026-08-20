from __future__ import annotations

import pytest

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    EpisodicMemoryQuery,
    EpisodicMemoryRead,
    MemoryContextRequest,
    MemoryReadOptions,
    MemoryType,
    SemanticMemoryQuery,
    SemanticMemoryRead,
)
from cowork_agent.features.ai_chat.memory_eval.arms import mask_reads, mask_request

pytestmark = pytest.mark.extended


def _reads() -> MemoryReadOptions:
    return MemoryReadOptions(
        short_term=True,
        long_term=True,
        episodic=EpisodicMemoryQuery(query="q", max_items=5, min_score=0.6, timeout_ms=500),
        semantic=SemanticMemoryQuery(query="q", max_items=5, min_score=0.6, timeout_ms=500),
    )


def test_masking_none_changes_nothing() -> None:
    reads = _reads()
    assert mask_reads(reads, None) == reads


def test_masking_short_term_turns_it_off_and_leaves_the_rest() -> None:
    masked = mask_reads(_reads(), MemoryType.SHORT_TERM)
    assert masked.short_term is False
    assert masked.long_term is True
    assert isinstance(masked.episodic, EpisodicMemoryQuery)
    assert isinstance(masked.semantic, SemanticMemoryQuery)


def test_masking_long_term_turns_it_off() -> None:
    masked = mask_reads(_reads(), MemoryType.LONG_TERM)
    assert masked.long_term is False
    assert masked.short_term is True


def test_masking_episodic_swaps_in_the_disabled_read() -> None:
    masked = mask_reads(_reads(), MemoryType.EPISODIC)
    assert isinstance(masked.episodic, EpisodicMemoryRead)
    assert masked.episodic.enabled is False
    assert isinstance(masked.semantic, SemanticMemoryQuery)


def test_masking_semantic_swaps_in_the_disabled_read() -> None:
    masked = mask_reads(_reads(), MemoryType.SEMANTIC)
    assert isinstance(masked.semantic, SemanticMemoryRead)
    assert masked.semantic.enabled is False
    assert isinstance(masked.episodic, EpisodicMemoryQuery)


def test_masking_an_already_disabled_read_is_idempotent() -> None:
    reads = MemoryReadOptions(
        short_term=True,
        long_term=True,
        episodic=EpisodicMemoryRead(enabled=False, retrieval_eligible_only=True, max_items=1),
        semantic=SemanticMemoryRead(enabled=False),
    )
    assert mask_reads(reads, MemoryType.EPISODIC) == reads


def test_mask_request_preserves_scope_and_session() -> None:
    scope = ChatMemoryScope(tenant_id="t", user_id="u", session_id="s")
    request = MemoryContextRequest(session_id="s", scope=scope, reads=_reads())
    masked = mask_request(request, MemoryType.LONG_TERM)
    assert masked.scope == scope
    assert masked.session_id == "s"
    assert masked.reads.long_term is False
