"""Pure authorization policy for system-generated chat-summary writes."""

from datetime import datetime

from cowork_agent.domain.chat_contracts import (
    ChatSummaryEpisode,
    EpisodeSourceType,
    EpisodeTransition,
    MemoryNamespace,
    MemoryType,
    TaskEpisode,
    ValidationStatus,
)


class ChatSummaryWriteRejected(ValueError):
    """The requested episodic chat-summary write is not authorized."""


class TaskEpisodeWriteRejected(ValueError):
    """The requested episodic task write is not authorized."""


class TaskEpisodeTransitionRejected(ValueError):
    """The requested episodic task lifecycle transition is not authorized."""


def authorize_chat_summary_write(namespace: MemoryNamespace, episode: ChatSummaryEpisode) -> None:
    """Raise unless the namespace and system-only summary provenance exactly agree."""

    try:
        ChatSummaryEpisode.from_dict(episode.to_dict())
    except (KeyError, TypeError, ValueError) as error:
        raise ChatSummaryWriteRejected("chat summary has an invalid bounded shape") from error
    if namespace.memory_type is not MemoryType.EPISODIC:
        raise ChatSummaryWriteRejected("chat summaries require an episodic namespace")
    if namespace.user_id != episode.user_id or namespace.session_id != episode.chat_session_id:
        raise ChatSummaryWriteRejected("summary scope does not match the write namespace")
    if namespace.record_id != episode.record_id:
        raise ChatSummaryWriteRejected("summary record does not match the write namespace")
    if namespace.source_id != episode.chat_turn_id:
        raise ChatSummaryWriteRejected("summary source does not match the write namespace")
    if episode.validation_status is not ValidationStatus.SYSTEM_GENERATED:
        raise ChatSummaryWriteRejected("chat summaries must be system-generated")
    if episode.retrieval_eligible is not False:
        raise ChatSummaryWriteRejected("chat summaries must be retrieval-ineligible")
    if episode.source_type is not EpisodeSourceType.SYSTEM_GENERATED_CHAT_SUMMARY:
        raise ChatSummaryWriteRejected("chat summaries require chat-summary provenance")


def authorize_task_episode_write(
    namespace: MemoryNamespace,
    episode: TaskEpisode,
    *,
    expires_at: datetime | None,
) -> TaskEpisode:
    """Return the validated canonical task episode or raise on an unsafe write."""

    try:
        trusted_episode = TaskEpisode.from_dict(episode.to_dict())
    except (KeyError, TypeError, ValueError) as error:
        raise TaskEpisodeWriteRejected("task episode has an invalid bounded shape") from error
    if namespace.memory_type is not MemoryType.EPISODIC:
        raise TaskEpisodeWriteRejected("task episodes require an episodic namespace")
    if (
        namespace.user_id != trusted_episode.user_id
        or namespace.session_id != trusted_episode.chat_session_id
    ):
        raise TaskEpisodeWriteRejected("task episode scope does not match the write namespace")
    if namespace.record_id != trusted_episode.record_id:
        raise TaskEpisodeWriteRejected("task episode record does not match the write namespace")
    if namespace.source_id != trusted_episode.chat_turn_id:
        raise TaskEpisodeWriteRejected("task episode source does not match the write namespace")
    if trusted_episode.creation_reason != "explicit_user_task_request":
        raise TaskEpisodeWriteRejected("task episodes require an explicit creation reason")
    if trusted_episode.validation_status is not ValidationStatus.SYSTEM_GENERATED:
        raise TaskEpisodeWriteRejected("task episode writes must be system-generated")
    if trusted_episode.retrieval_eligible:
        raise TaskEpisodeWriteRejected("task episode writes must be retrieval-ineligible")
    if trusted_episode.source_type is not EpisodeSourceType.SYSTEM_GENERATED_CHAT_TASK:
        raise TaskEpisodeWriteRejected("task episodes require chat-task provenance")
    if expires_at is None:
        return trusted_episode
    if not isinstance(expires_at, datetime):
        raise TaskEpisodeWriteRejected("expires_at must be a datetime or None")
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        raise TaskEpisodeWriteRejected("expires_at must be timezone-aware")
    try:
        is_later = expires_at > trusted_episode.created_at
    except TypeError as error:
        raise TaskEpisodeWriteRejected(
            "expires_at must be comparable to episode.created_at"
        ) from error
    if not is_later:
        raise TaskEpisodeWriteRejected("expires_at must be later than episode.created_at")
    return trusted_episode


def build_task_episode_transition(
    namespace: MemoryNamespace,
    *,
    episode_id: str,
    from_status: ValidationStatus,
    to_status: ValidationStatus,
    transitioned_at: datetime,
) -> EpisodeTransition:
    """Build one canonical, allowed task lifecycle transition for execution."""

    if not isinstance(transitioned_at, datetime):
        raise TaskEpisodeTransitionRejected("transitioned_at must be a datetime")
    if transitioned_at.tzinfo is None or transitioned_at.utcoffset() is None:
        raise TaskEpisodeTransitionRejected("transitioned_at must be timezone-aware")
    try:
        transition = EpisodeTransition(
            episode_id=episode_id,
            namespace=namespace,
            from_status=from_status,
            to_status=to_status,
            retrieval_eligible=to_status
            in {ValidationStatus.USER_APPROVED, ValidationStatus.COMPLETED},
            transitioned_at=transitioned_at,
        )
        canonical_transition = EpisodeTransition.from_dict(transition.to_dict())
    except (KeyError, TypeError, ValueError) as error:
        raise TaskEpisodeTransitionRejected("task episode transition is invalid") from error
    if (
        canonical_transition.namespace != namespace
        or canonical_transition.episode_id != episode_id
        or canonical_transition.from_status is not from_status
        or canonical_transition.to_status is not to_status
        or canonical_transition.transitioned_at != transitioned_at
    ):
        raise TaskEpisodeTransitionRejected("task episode transition identity is invalid")
    return canonical_transition
