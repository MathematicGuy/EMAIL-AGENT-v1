"""Pure authorization policy for system-generated chat-summary writes."""

from cowork_agent.domain.chat_contracts import (
    ChatSummaryEpisode,
    EpisodeSourceType,
    MemoryNamespace,
    MemoryType,
    ValidationStatus,
)


class ChatSummaryWriteRejected(ValueError):
    """The requested episodic chat-summary write is not authorized."""


def authorize_chat_summary_write(namespace: MemoryNamespace, episode: ChatSummaryEpisode) -> None:
    """Raise unless the namespace and system-only summary provenance exactly agree."""

    try:
        ChatSummaryEpisode.from_dict(episode.to_dict())
    except (KeyError, TypeError, ValueError) as error:
        raise ChatSummaryWriteRejected("chat summary has an invalid bounded shape") from error
    if namespace.memory_type is not MemoryType.EPISODIC:
        raise ChatSummaryWriteRejected("chat summaries require an episodic namespace")
    if (
        namespace.tenant_id != episode.tenant_id
        or namespace.user_id != episode.user_id
        or namespace.session_id != episode.chat_session_id
    ):
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
