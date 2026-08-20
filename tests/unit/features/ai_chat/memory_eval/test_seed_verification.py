from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatTurn,
    DeclarativeProfile,
    MemoryProvenanceSource,
    MemoryType,
)
from cowork_agent.features.ai_chat.memory_eval.arms import ArmScopedMemoryGateway
from cowork_agent.features.ai_chat.memory_eval.live_seeding import verify_seed
from cowork_agent.features.ai_chat.memory_gateway import MemoryGateway
from cowork_agent.features.ai_chat.session_buffer import InMemoryChatSessionBuffer

pytestmark = pytest.mark.extended


class _Declarative:
    def __init__(self, profile: DeclarativeProfile | None) -> None:
        self._profile = profile

    async def read_profile(self, namespace: object) -> DeclarativeProfile | None:
        del namespace
        return self._profile

    async def write_profile(
        self, namespace: object, profile: DeclarativeProfile
    ) -> DeclarativeProfile:
        del namespace
        return profile

    async def delete_profile(self, namespace: object) -> bool:
        del namespace
        return True


def _scope() -> ChatMemoryScope:
    return ChatMemoryScope(tenant_id="t", user_id="u", session_id="s")


def _profile() -> DeclarativeProfile:
    now = datetime(2026, 8, 18, tzinfo=UTC)
    return DeclarativeProfile(
        profile_id="p1",
        user_id="u",
        language="vi",
        timezone=None,
        assistant_persona=None,
        response_tone=None,
        created_at=now,
        updated_at=now,
        source_type=MemoryProvenanceSource.EXPLICIT_USER_CONFIG,
    )


def test_a_seeded_profile_verifies(
    memory_gateway_factory: Callable[..., MemoryGateway],
) -> None:
    gateway = memory_gateway_factory(declarative_memory=_Declarative(_profile()))
    findings = asyncio.run(verify_seed(gateway, _scope(), (MemoryType.LONG_TERM,)))
    assert findings == ()


def test_an_empty_profile_is_reported_as_a_seed_that_did_not_land(
    memory_gateway_factory: Callable[..., MemoryGateway],
) -> None:
    gateway = memory_gateway_factory(declarative_memory=_Declarative(None))
    findings = asyncio.run(verify_seed(gateway, _scope(), (MemoryType.LONG_TERM,)))
    assert [item.scope for item in findings] == [MemoryType.LONG_TERM]
    assert "came back empty" in findings[0].reason


def test_an_empty_short_term_buffer_is_reported(
    memory_gateway_factory: Callable[..., MemoryGateway],
) -> None:
    gateway = memory_gateway_factory()
    findings = asyncio.run(verify_seed(gateway, _scope(), (MemoryType.SHORT_TERM,)))
    assert [item.scope for item in findings] == [MemoryType.SHORT_TERM]


def test_a_populated_buffer_verifies(
    memory_gateway_factory: Callable[..., MemoryGateway],
) -> None:
    gateway = memory_gateway_factory()
    gateway.append_turn(
        ChatTurn(
            turn_id="t1",
            session_id="s",
            user_message="a seeded line",
            assistant_message="ok",
            created_at=datetime(2026, 8, 18, tzinfo=UTC),
        )
    )
    assert asyncio.run(verify_seed(gateway, _scope(), (MemoryType.SHORT_TERM,))) == ()


def test_only_the_requested_scopes_are_checked(
    memory_gateway_factory: Callable[..., MemoryGateway],
) -> None:
    # An unseeded scope is not a failure — it was never declared.
    gateway = memory_gateway_factory(declarative_memory=_Declarative(None))
    assert asyncio.run(verify_seed(gateway, _scope(), ())) == ()


def test_verification_ignores_the_arm_mask() -> None:
    # The ablated arm masks its target scope out of the PROBE's read. Seed
    # verification is not a probe read: it asks whether the store holds what was
    # written. Verifying through the mask reported every ablated arm's target as
    # "seed did not land", which is how a healthy store reads as amnesia.
    gateway = ArmScopedMemoryGateway(
        masked_scope=MemoryType.LONG_TERM,
        scope=_scope(),
        session_buffer=InMemoryChatSessionBuffer(max_turns=20, ttl_seconds=1800),
        declarative_memory=_Declarative(_profile()),
    )
    assert asyncio.run(verify_seed(gateway, _scope(), (MemoryType.LONG_TERM,))) == ()


def test_verification_still_reports_a_scope_that_really_is_empty() -> None:
    gateway = ArmScopedMemoryGateway(
        masked_scope=MemoryType.LONG_TERM,
        scope=_scope(),
        session_buffer=InMemoryChatSessionBuffer(max_turns=20, ttl_seconds=1800),
        declarative_memory=_Declarative(None),
    )
    findings = asyncio.run(verify_seed(gateway, _scope(), (MemoryType.LONG_TERM,)))
    assert [item.scope for item in findings] == [MemoryType.LONG_TERM]
