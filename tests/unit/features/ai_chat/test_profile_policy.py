from datetime import UTC, datetime

import pytest

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatToolChoice,
    DeclarativeProfile,
    MemoryNamespace,
    MemoryProvenance,
    MemoryProvenanceSource,
    MemoryType,
)
from cowork_agent.features.ai_chat.profile_policy import (
    MAX_PREFERENCE_LENGTH,
    ProfileWriteRejected,
    authorize_profile_write,
)

NOW = datetime(2026, 8, 10, 9, tzinfo=UTC)


def _namespace(
    *,
    tenant_id: str = "tenant-1",
    user_id: str = "user@example.com",
    memory_type: MemoryType = MemoryType.LONG_TERM,
) -> MemoryNamespace:
    return MemoryNamespace(
        scope=ChatMemoryScope(tenant_id=tenant_id, user_id=user_id, session_id="session-1"),
        memory_type=memory_type,
        record_id=None,
        source_id=None,
    )


def _profile(**overrides: object) -> DeclarativeProfile:
    values: dict[str, object] = {
        "profile_id": "profile-1",
        "tenant_id": "tenant-1",
        "user_id": "user@example.com",
        "language": "vi",
        "timezone": "Asia/Bangkok",
        "assistant_persona": "Coworker",
        "response_tone": "direct",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return DeclarativeProfile(**values)  # type: ignore[arg-type]


def _provenance(
    *,
    source_type: MemoryProvenanceSource = MemoryProvenanceSource.EXPLICIT_USER_CONFIG,
    source_tool: ChatToolChoice | None = None,
) -> MemoryProvenance:
    return MemoryProvenance(
        source_type=source_type,
        source_id="chat-settings-form",
        source_tool=source_tool,
        run_id=None,
        chat_turn_id="turn-1",
        pipeline_version=None,
        model_id=None,
        prompt_version=None,
    )


def test_explicit_user_configuration_is_authorized() -> None:
    authorize_profile_write(_namespace(), _profile(), _provenance())


def test_passive_inference_from_chat_or_email_is_rejected() -> None:
    inferred = _provenance(
        source_type=MemoryProvenanceSource.SYSTEM_GENERATED_CHAT_TOOL_OUTPUT,
        source_tool=ChatToolChoice.EMAIL,
    )

    with pytest.raises(ProfileWriteRejected):
        authorize_profile_write(_namespace(), _profile(), inferred)


def test_enterprise_corpus_provenance_is_rejected() -> None:
    with pytest.raises(ProfileWriteRejected):
        authorize_profile_write(
            _namespace(),
            _profile(),
            _provenance(source_type=MemoryProvenanceSource.ENTERPRISE_CORPUS),
        )


def test_explicit_provenance_derived_from_the_email_tool_is_still_rejected() -> None:
    with pytest.raises(ProfileWriteRejected):
        authorize_profile_write(
            _namespace(), _profile(), _provenance(source_tool=ChatToolChoice.EMAIL)
        )


@pytest.mark.parametrize(
    "namespace",
    [
        _namespace(tenant_id="tenant-2"),
        _namespace(user_id="other@example.com"),
    ],
    ids=["tenant", "user"],
)
def test_foreign_scope_is_rejected(namespace: MemoryNamespace) -> None:
    with pytest.raises(ProfileWriteRejected):
        authorize_profile_write(namespace, _profile(), _provenance())


def test_non_long_term_namespace_is_rejected() -> None:
    with pytest.raises(ProfileWriteRejected):
        authorize_profile_write(
            _namespace(memory_type=MemoryType.EPISODIC), _profile(), _provenance()
        )


def test_oversized_preference_is_rejected_to_keep_the_profile_compact() -> None:
    with pytest.raises(ProfileWriteRejected):
        authorize_profile_write(
            _namespace(),
            _profile(assistant_persona="x" * (MAX_PREFERENCE_LENGTH + 1)),
            _provenance(),
        )


def test_profile_contract_refuses_non_explicit_source_type() -> None:
    with pytest.raises(ValueError, match="explicit-only"):
        _profile(source_type=MemoryProvenanceSource.ENTERPRISE_CORPUS)
