"""Does a masked arm actually keep the scope out of the prompt? (SPEC §5.2)

`test_arms.py` shows `mask_reads` turns the flag off. That is not the question
a report depends on. The question is whether the value can still reach the
model - through the gateway, through the assembled context, and into the
payload the provider sends. If it can, every ablation conclusion in every
report is void, and the harness is the thing to fix (concern C).

If it cannot, a never-filled arm that still answers correctly is telling us
something about the QUESTION (concern B): it was guessable without the memory.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatMessageRequest,
    DeclarativeProfile,
    MemoryContextRequest,
    MemoryProvenanceSource,
    MemoryType,
)
from cowork_agent.features.ai_chat.generation_context import assemble_generation_context
from cowork_agent.features.ai_chat.memory_eval.arms import ArmScopedMemoryGateway
from cowork_agent.features.ai_chat.retrieval_policy import select_memory_reads
from cowork_agent.features.ai_chat.session_buffer import InMemoryChatSessionBuffer

# The exact payload `_ConfiguredChatReply.stream_reply` hands the provider.
# Reaching for a private is deliberate: the assertion is about what reaches the
# model, and anything short of the payload would be an assertion about an
# intermediate object the model never sees.
from cowork_agent.integrations.llm.chat_reply import _request_payload

# Verbatim from evaluations/MEMORIES/probes/v1-four-scopes.json - the seeded
# long-term profile and the question `lt_recall_01` asks about it.
SEEDED_TIMEZONE = "Asia/Ho_Chi_Minh"
SEEDED_PERSONA = "điều phối viên vận hành"
LT_RECALL_QUESTION = "Khi sắp lịch cho tôi thì nên dùng múi giờ nào?"


class _Declarative:
    def __init__(self, profile: DeclarativeProfile | None) -> None:
        self._profile = profile

    async def read_profile(self, namespace: object) -> DeclarativeProfile | None:
        del namespace
        return self._profile


def _scope() -> ChatMemoryScope:
    return ChatMemoryScope(tenant_id="t", user_id="u", session_id="s")


def _seeded_profile() -> DeclarativeProfile:
    now = datetime(2026, 8, 18, tzinfo=UTC)
    return DeclarativeProfile(
        profile_id="p1",
        user_id="u",
        language="vi",
        timezone=SEEDED_TIMEZONE,
        assistant_persona=SEEDED_PERSONA,
        response_tone="ngắn gọn",
        created_at=now,
        updated_at=now,
        source_type=MemoryProvenanceSource.EXPLICIT_USER_CONFIG,
    )


def _gateway(masked_scope: MemoryType | None) -> ArmScopedMemoryGateway:
    return ArmScopedMemoryGateway(
        masked_scope=masked_scope,
        scope=_scope(),
        session_buffer=InMemoryChatSessionBuffer(max_turns=20, ttl_seconds=1800),
        declarative_memory=_Declarative(_seeded_profile()),
    )


def _payload_for(masked_scope: MemoryType | None) -> str:
    """Run one arm end to end and return what the provider would be sent."""

    request = ChatMessageRequest(
        session_id="s", user_message=LT_RECALL_QUESTION, idempotency_key="k"
    )
    context_request = MemoryContextRequest(
        session_id="s", scope=_scope(), reads=select_memory_reads(request)
    )
    memory = asyncio.run(_gateway(masked_scope).read_context(context_request))
    if masked_scope is MemoryType.LONG_TERM:
        assert memory.profile is None
    context = assemble_generation_context(request, memory)
    assert (context.stored_preference is None) is (masked_scope is MemoryType.LONG_TERM)
    return json.dumps(_request_payload(request, context), ensure_ascii=False)


def test_the_store_really_holds_the_profile_when_nothing_is_masked() -> None:
    # Without this, the masked assertion below would pass against an empty
    # store and prove nothing at all.
    assert SEEDED_TIMEZONE in _payload_for(None)


def test_a_long_term_masked_arm_sends_the_profile_nowhere_near_the_model() -> None:
    payload = _payload_for(MemoryType.LONG_TERM)
    assert SEEDED_TIMEZONE not in payload
    assert SEEDED_PERSONA not in payload
