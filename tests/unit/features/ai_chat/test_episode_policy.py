"""Pure authorization tests for system-generated chat-summary writes."""

from datetime import UTC, datetime

import pytest

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatSummaryEpisode,
    EpisodeSourceType,
    MemoryNamespace,
    MemoryType,
)
from cowork_agent.domain.target_contracts import ValidationStatus
from cowork_agent.features.ai_chat.episode_policy import (
    ChatSummaryWriteRejected,
    authorize_chat_summary_write,
)


def _episode() -> ChatSummaryEpisode:
    now = datetime(2026, 8, 10, 9, tzinfo=UTC)
    return ChatSummaryEpisode(
        episode_id="chat-summary-1",
        record_id="record-1",
        tenant_id="tenant-1",
        user_id="user@example.com",
        chat_session_id="session-1",
        chat_turn_id="turn-1",
        summary="The user asked for help prioritizing the approved procedure.",
        validation_status=ValidationStatus.SYSTEM_GENERATED,
        retrieval_eligible=False,
        source_type=EpisodeSourceType.SYSTEM_GENERATED_CHAT_SUMMARY,
        created_at=now,
        updated_at=now,
        expires_at=None,
        pipeline_version="2",
        model_id=None,
        prompt_version=None,
        confidence=None,
    )


def test_policy_rejects_a_namespace_that_does_not_match_summary_provenance() -> None:
    episode = _episode()
    namespace = MemoryNamespace(
        scope=ChatMemoryScope(
            tenant_id=episode.tenant_id,
            user_id=episode.user_id,
            session_id=episode.chat_session_id,
        ),
        memory_type=MemoryType.EPISODIC,
        record_id=episode.record_id,
        source_id="different-turn",
    )

    with pytest.raises(ChatSummaryWriteRejected, match="source"):
        authorize_chat_summary_write(namespace, episode)
