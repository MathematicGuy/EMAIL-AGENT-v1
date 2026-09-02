from __future__ import annotations

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


def _reads() -> MemoryReadOptions:
    return MemoryReadOptions(
        short_term=True,
        long_term=True,
        episodic=EpisodicMemoryQuery(query="q", max_items=5, min_score=0.6, timeout_ms=500),
        semantic=SemanticMemoryQuery(query="q", max_items=5, min_score=0.6, timeout_ms=500),
    )


def test_mask_reads_all_scopes() -> None:
    reads = _reads()
    assert mask_reads(reads, None) == reads

    st_masked = mask_reads(reads, MemoryType.SHORT_TERM)
    assert st_masked.short_term is False and st_masked.long_term is True

    lt_masked = mask_reads(reads, MemoryType.LONG_TERM)
    assert lt_masked.long_term is False and lt_masked.short_term is True

    ep_masked = mask_reads(reads, MemoryType.EPISODIC)
    assert (
        isinstance(ep_masked.episodic, EpisodicMemoryRead) and ep_masked.episodic.enabled is False
    )

    sem_masked = mask_reads(reads, MemoryType.SEMANTIC)
    assert (
        isinstance(sem_masked.semantic, SemanticMemoryRead) and sem_masked.semantic.enabled is False
    )

    disabled = MemoryReadOptions(
        short_term=True,
        long_term=True,
        episodic=EpisodicMemoryRead(enabled=False, retrieval_eligible_only=True, max_items=1),
        semantic=SemanticMemoryRead(enabled=False),
    )
    assert mask_reads(disabled, MemoryType.EPISODIC) == disabled


def test_mask_request_preserves_scope_and_session() -> None:
    scope = ChatMemoryScope(tenant_id="t", user_id="u", session_id="s")
    request = MemoryContextRequest(session_id="s", scope=scope, reads=_reads())
    masked = mask_request(request, MemoryType.LONG_TERM)
    assert masked.scope == scope
    assert masked.session_id == "s"
    assert masked.reads.long_term is False
