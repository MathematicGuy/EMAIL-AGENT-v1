from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    DeclarativeProfile,
    MemoryNamespace,
    MemoryProvenanceSource,
    MemoryType,
)
from cowork_agent.features.ai_chat.memory_eval.probes import SeedSpec
from cowork_agent.features.ai_chat.memory_eval.seeding import (
    SeedOutcome,
    build_seed_profile,
    seed_long_term,
)
from cowork_agent.features.ai_chat.memory_gateway import MemoryGateway


class _Declarative:
    def __init__(self, *, fail: bool = False) -> None:
        self.written: list[DeclarativeProfile] = []
        self._fail = fail

    async def read_profile(self, namespace: MemoryNamespace) -> DeclarativeProfile | None:
        del namespace
        return self.written[-1] if self.written else None

    async def write_profile(
        self, namespace: MemoryNamespace, profile: DeclarativeProfile
    ) -> DeclarativeProfile:
        del namespace
        if self._fail:
            raise RuntimeError("adapter down")
        self.written.append(profile)
        return profile

    async def delete_profile(self, namespace: MemoryNamespace) -> bool:
        del namespace
        return True


def _scope() -> ChatMemoryScope:
    return ChatMemoryScope(tenant_id="t", user_id="u", session_id="s")


def test_build_seed_profile_maps_only_known_preference_fields() -> None:
    profile = build_seed_profile(
        _scope(),
        {"language": "vi", "response_tone": "concise"},
        now=datetime(2026, 8, 18, tzinfo=UTC),
        profile_id="prof-1",
    )
    assert profile.language == "vi"
    assert profile.response_tone == "concise"
    assert profile.assistant_persona is None
    assert profile.source_type is MemoryProvenanceSource.EXPLICIT_USER_CONFIG


def test_build_seed_profile_rejects_an_unknown_field() -> None:
    with pytest.raises(ValueError, match="unknown profile field"):
        build_seed_profile(
            _scope(),
            {"nickname": "x"},
            now=datetime(2026, 8, 18, tzinfo=UTC),
            profile_id="prof-1",
        )


def test_seed_long_term_writes_with_explicit_user_config_provenance(
    memory_gateway_factory: Callable[..., MemoryGateway],
) -> None:
    # memory_gateway_factory is defined in this module's conftest (Step 2).
    declarative = _Declarative()
    gateway = memory_gateway_factory(declarative_memory=declarative)
    spec = SeedSpec((), {"language": "vi"}, (), None)
    outcome = asyncio.run(
        seed_long_term(
            gateway,
            _scope(),
            spec,
            now=datetime(2026, 8, 18, tzinfo=UTC),
            profile_id="p1",
        )
    )
    assert outcome == SeedOutcome(MemoryType.LONG_TERM, True, "seeded")
    assert declarative.written[0].language == "vi"
    assert declarative.written[0].source_type is MemoryProvenanceSource.EXPLICIT_USER_CONFIG


def test_seed_long_term_reports_a_failure_instead_of_raising(
    memory_gateway_factory: Callable[..., MemoryGateway],
) -> None:
    # A seeding failure is a finding about the scope, not a reason to abandon
    # the other three. SPEC §6.1.
    gateway = memory_gateway_factory(declarative_memory=_Declarative(fail=True))
    spec = SeedSpec((), {"language": "vi"}, (), None)
    outcome = asyncio.run(
        seed_long_term(
            gateway,
            _scope(),
            spec,
            now=datetime(2026, 8, 18, tzinfo=UTC),
            profile_id="p1",
        )
    )
    assert outcome.ok is False
    assert "adapter down" in outcome.reason


def test_seed_long_term_is_skipped_when_no_profile_is_declared(
    memory_gateway_factory: Callable[..., MemoryGateway],
) -> None:
    gateway = memory_gateway_factory(declarative_memory=_Declarative())
    outcome = asyncio.run(
        seed_long_term(
            gateway,
            _scope(),
            SeedSpec((), {}, (), None),
            now=datetime(2026, 8, 18, tzinfo=UTC),
            profile_id="p1",
        )
    )
    assert outcome.ok is True
    assert outcome.reason == "nothing declared"
