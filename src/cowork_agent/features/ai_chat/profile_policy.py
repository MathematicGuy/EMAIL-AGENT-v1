"""Pure explicit-only write policy for the declarative chat profile (FR-04).

Long-term writes are authorized only for an explicit user configuration, an
explicit "remember this" request, or a trusted administrative configuration —
all of which carry ``explicit_user_config`` provenance. All other provenance
is refused here, before any adapter is reached.
"""

from cowork_agent.domain.chat_contracts import (
    PROFILE_PREFERENCE_FIELDS,
    DeclarativeProfile,
    MemoryNamespace,
    MemoryProvenance,
    MemoryProvenanceSource,
    MemoryType,
)

# ponytail: one flat bound keeps the profile compact (FR-05); make it a tenant
# policy value only when a product surface actually needs longer fields.
MAX_PREFERENCE_LENGTH = 200


class ProfileWriteRejected(ValueError):
    """The requested long-term write is not an authorized explicit write."""


def authorize_profile_write(
    namespace: MemoryNamespace,
    profile: DeclarativeProfile,
    provenance: MemoryProvenance,
) -> None:
    """Raise ``ProfileWriteRejected`` unless this is an explicit, in-scope write."""

    if namespace.memory_type is not MemoryType.LONG_TERM:
        raise ProfileWriteRejected("profile writes require a long-term namespace")
    if namespace.tenant_id != profile.tenant_id or namespace.user_id != profile.user_id:
        raise ProfileWriteRejected("profile scope does not match the write namespace")
    if provenance.source_type is not MemoryProvenanceSource.EXPLICIT_USER_CONFIG:
        raise ProfileWriteRejected("long-term writes require explicit_user_config provenance")
    for name in PROFILE_PREFERENCE_FIELDS:
        value = getattr(profile, name)
        if value is not None and len(value) > MAX_PREFERENCE_LENGTH:
            raise ProfileWriteRejected(f"{name} exceeds the compact profile bound")
