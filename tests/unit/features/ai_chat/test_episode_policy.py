"""Pure authorization tests for system-generated chat-summary writes."""

from datetime import UTC, datetime, timedelta

import pytest

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatSummaryEpisode,
    EpisodeCitation,
    EpisodeSourceType,
    EpisodeTransition,
    MemoryNamespace,
    MemoryType,
    TaskEpisode,
)
from cowork_agent.domain.target_contracts import ValidationStatus
from cowork_agent.features.ai_chat.episode_policy import (
    ChatSummaryWriteRejected,
    TaskEpisodeTransitionRejected,
    TaskEpisodeWriteRejected,
    authorize_chat_summary_write,
    authorize_task_episode_write,
    build_task_episode_transition,
)


def _episode() -> ChatSummaryEpisode:
    now = datetime(2026, 8, 10, 9, tzinfo=UTC)
    return ChatSummaryEpisode(
        episode_id="chat-summary-1",
        record_id="record-1",
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


def _task_episode() -> TaskEpisode:
    now = datetime(2026, 8, 10, 9, tzinfo=UTC)
    return TaskEpisode(
        episode_id="task-episode-1",
        record_id="record-1",
        user_id="user@example.com",
        chat_session_id="session-1",
        chat_turn_id="turn-1",
        creation_reason="explicit_user_task_request",
        task_title="Submit the report",
        minimal_request_paraphrase="Submit the requested report.",
        action_plan=("Open the approved template.",),
        rag_citations=(
            EpisodeCitation(
                document_id="doc-1",
                document_title="Procedure",
                section=None,
                source_url="https://docs.example.com/procedure",
            ),
        ),
        missing_information=(),
        validation_status=ValidationStatus.SYSTEM_GENERATED,
        retrieval_eligible=False,
        source_type=EpisodeSourceType.SYSTEM_GENERATED_CHAT_TASK,
        created_at=now,
        updated_at=now,
        pipeline_version="2",
        model_id=None,
        prompt_version=None,
        confidence=None,
    )


def _task_namespace(episode: TaskEpisode) -> MemoryNamespace:
    return MemoryNamespace(
        scope=ChatMemoryScope(
            user_id=episode.user_id,
            session_id=episode.chat_session_id,
        ),
        memory_type=MemoryType.EPISODIC,
        record_id=episode.record_id,
        source_id=episode.chat_turn_id,
    )


def test_policy_rejects_a_namespace_that_does_not_match_summary_provenance() -> None:
    episode = _episode()
    namespace = MemoryNamespace(
        scope=ChatMemoryScope(
            user_id=episode.user_id,
            session_id=episode.chat_session_id,
        ),
        memory_type=MemoryType.EPISODIC,
        record_id=episode.record_id,
        source_id="different-turn",
    )

    with pytest.raises(ChatSummaryWriteRejected, match="source"):
        authorize_chat_summary_write(namespace, episode)


def test_task_episode_policy_accepts_a_bounded_initial_write_with_optional_expiry() -> None:
    episode = _task_episode()
    namespace = _task_namespace(episode)

    authorize_task_episode_write(namespace, episode, expires_at=None)
    authorize_task_episode_write(
        namespace, episode, expires_at=episode.created_at + timedelta(days=1)
    )


def test_task_episode_policy_rejects_foreign_scope() -> None:
    scopes = [
        ChatMemoryScope(user_id="other@example.com", session_id="session-1"),
        ChatMemoryScope(user_id="user@example.com", session_id="session-2"),
    ]
    for scope in scopes:
        episode = _task_episode()
        namespace = MemoryNamespace(
            scope=scope,
            memory_type=MemoryType.EPISODIC,
            record_id=episode.record_id,
            source_id=episode.chat_turn_id,
        )
        with pytest.raises(TaskEpisodeWriteRejected, match="scope"):
            authorize_task_episode_write(namespace, episode, expires_at=None)


def test_task_episode_policy_rejects_a_mismatched_identity() -> None:
    cases = [("record-2", "turn-1", "record"), ("record-1", "turn-2", "source")]
    for record_id, source_id, match in cases:
        episode = _task_episode()
        namespace = MemoryNamespace(
            scope=ChatMemoryScope(user_id="user@example.com", session_id="session-1"),
            memory_type=MemoryType.EPISODIC,
            record_id=record_id,
            source_id=source_id,
        )
        with pytest.raises(TaskEpisodeWriteRejected, match=match):
            authorize_task_episode_write(namespace, episode, expires_at=None)


def test_task_episode_policy_reparses_and_rejects_tampered_shapes() -> None:
    cases = [
        ("creation_reason", "implicit"),
        ("validation_status", ValidationStatus.USER_APPROVED),
        ("retrieval_eligible", True),
        ("source_type", EpisodeSourceType.SYSTEM_GENERATED_CHAT_SUMMARY),
        ("action_plan", ("x" * 501,)),
        ("rag_citations", ({"raw_email": "copied"},)),
        ("missing_information", ({"tool_payload": {}},)),
    ]
    for field, value in cases:
        episode = _task_episode()
        object.__setattr__(episode, field, value)
        with pytest.raises(TaskEpisodeWriteRejected, match="invalid bounded shape"):
            authorize_task_episode_write(_task_namespace(episode), episode, expires_at=None)


def test_task_episode_policy_requires_an_aware_future_expiry() -> None:
    cases = [
        datetime(2026, 8, 10, 9),
        datetime(2026, 8, 10, 9, tzinfo=UTC),
        "2026-08-11T09:00:00+00:00",
    ]
    for expires_at in cases:
        episode = _task_episode()
        with pytest.raises(TaskEpisodeWriteRejected, match="expires_at"):
            authorize_task_episode_write(_task_namespace(episode), episode, expires_at=expires_at)


def test_task_episode_transition_policy_builds_a_canonical_allowed_transition() -> None:
    cases = [
        (ValidationStatus.SYSTEM_GENERATED, ValidationStatus.USER_APPROVED, True),
        (ValidationStatus.SYSTEM_GENERATED, ValidationStatus.COMPLETED, True),
        (ValidationStatus.SYSTEM_GENERATED, ValidationStatus.REJECTED, False),
        (ValidationStatus.USER_APPROVED, ValidationStatus.COMPLETED, True),
        (ValidationStatus.USER_APPROVED, ValidationStatus.REJECTED, False),
    ]
    for from_status, to_status, retrieval_eligible in cases:
        episode = _task_episode()
        namespace = _task_namespace(episode)
        transitioned_at = datetime(2026, 8, 11, 9, tzinfo=UTC)

        transition = build_task_episode_transition(
            namespace,
            episode_id=episode.episode_id,
            from_status=from_status,
            to_status=to_status,
            transitioned_at=transitioned_at,
        )

        assert type(transition) is EpisodeTransition
        assert transition == EpisodeTransition(
            episode_id=episode.episode_id,
            namespace=namespace,
            from_status=from_status,
            to_status=to_status,
            retrieval_eligible=retrieval_eligible,
            transitioned_at=transitioned_at,
        )


def test_task_episode_transition_policy_rejects_invalid_status_changes() -> None:
    cases = [
        (ValidationStatus.SYSTEM_GENERATED, ValidationStatus.SYSTEM_GENERATED),
        (ValidationStatus.COMPLETED, ValidationStatus.REJECTED),
        (ValidationStatus.REJECTED, ValidationStatus.USER_APPROVED),
        ("system_generated", ValidationStatus.USER_APPROVED),
    ]
    for from_status, to_status in cases:
        episode = _task_episode()
        with pytest.raises(TaskEpisodeTransitionRejected, match="transition"):
            build_task_episode_transition(
                _task_namespace(episode),
                episode_id=episode.episode_id,
                from_status=from_status,  # type: ignore[arg-type]
                to_status=to_status,  # type: ignore[arg-type]
                transitioned_at=datetime(2026, 8, 11, 9, tzinfo=UTC),
            )


def test_task_episode_transition_policy_requires_an_aware_datetime() -> None:
    cases = [datetime(2026, 8, 11, 9), "2026-08-11T09:00:00+00:00"]
    for transitioned_at in cases:
        episode = _task_episode()
        with pytest.raises(TaskEpisodeTransitionRejected, match="transitioned_at"):
            build_task_episode_transition(
                _task_namespace(episode),
                episode_id=episode.episode_id,
                from_status=ValidationStatus.SYSTEM_GENERATED,
                to_status=ValidationStatus.USER_APPROVED,
                transitioned_at=transitioned_at,  # type: ignore[arg-type]
            )


def test_task_episode_transition_policy_rejects_invalid_episode_identity() -> None:
    for episode_id in ("", 1):
        episode = _task_episode()
        with pytest.raises(TaskEpisodeTransitionRejected, match="transition"):
            build_task_episode_transition(
                _task_namespace(episode),
                episode_id=episode_id,  # type: ignore[arg-type]
                from_status=ValidationStatus.SYSTEM_GENERATED,
                to_status=ValidationStatus.USER_APPROVED,
                transitioned_at=datetime(2026, 8, 11, 9, tzinfo=UTC),
            )
