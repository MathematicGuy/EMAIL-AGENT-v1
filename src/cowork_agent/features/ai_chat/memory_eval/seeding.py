"""Seeding each scope through its real authorization path (SPEC §6).

waku-agent seeds conversationally because pushing facts in through the side
door would skip extraction - the step that decides what is worth keeping. Our
equivalent step is AUTHORIZATION. Writing rows straight into the repositories
would score retrieval as if authorization had happened when it had not, and
would let probes pass on episode states no real flow can reach.

A seeding failure is a finding, reported as such, not an exception that takes
the other three scopes down with it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from cowork_agent.domain.chat_contracts import (
    PROFILE_PREFERENCE_FIELDS,
    ChatMemoryScope,
    DeclarativeProfile,
    MemoryProvenance,
    MemoryProvenanceSource,
    MemoryType,
)

from ..memory_gateway import MemoryGateway
from .probes import SeedSpec


@dataclass(frozen=True, slots=True)
class SeedOutcome:
    scope: MemoryType
    ok: bool
    reason: str


def build_seed_profile(
    scope: ChatMemoryScope,
    fields: Mapping[str, str],
    *,
    now: datetime,
    profile_id: str,
) -> DeclarativeProfile:
    """Build a DeclarativeProfile from the probe set's declared preferences.

    Only the four fields in PROFILE_PREFERENCE_FIELDS are accepted. An unknown
    key is a probe-set authoring error and is refused loudly here rather than
    silently dropped, which would make a probe fail for an invisible reason.
    """

    unknown = set(fields) - set(PROFILE_PREFERENCE_FIELDS)
    if unknown:
        raise ValueError(f"unknown profile field(s): {sorted(unknown)}")
    return DeclarativeProfile(
        profile_id=profile_id,
        user_id=scope.user_id,
        language=fields.get("language"),
        timezone=fields.get("timezone"),
        assistant_persona=fields.get("assistant_persona"),
        response_tone=fields.get("response_tone"),
        created_at=now,
        updated_at=now,
        source_type=MemoryProvenanceSource.EXPLICIT_USER_CONFIG,
    )


async def seed_long_term(
    gateway: MemoryGateway,
    scope: ChatMemoryScope,
    spec: SeedSpec,
    *,
    now: datetime,
    profile_id: str,
) -> SeedOutcome:
    """Write the declared profile with explicit_user_config provenance.

    `scope` is passed rather than read off the gateway: MemoryGateway keeps its
    scope private and exposes no accessor, and the harness may not add one.
    """

    if not spec.long_term:
        return SeedOutcome(MemoryType.LONG_TERM, True, "nothing declared")
    try:
        profile = build_seed_profile(scope, spec.long_term, now=now, profile_id=profile_id)
        await gateway.write_profile(
            profile,
            provenance=MemoryProvenance(
                source_type=MemoryProvenanceSource.EXPLICIT_USER_CONFIG,
                source_id=profile_id,
                chat_turn_id=None,
                pipeline_version=None,
                model_id=None,
                prompt_version=None,
            ),
        )
    except Exception as error:  # noqa: BLE001 - a seed failure is a finding
        return SeedOutcome(MemoryType.LONG_TERM, False, f"{type(error).__name__}: {error}")
    return SeedOutcome(MemoryType.LONG_TERM, True, "seeded")
