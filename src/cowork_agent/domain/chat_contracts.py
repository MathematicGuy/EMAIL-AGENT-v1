"""Framework-free V2-M1 contracts for chat and the Memory Gateway boundary."""

from ._chat_contracts_chat import (
    ChatMessageRequest,
    ChatMessageStreamEvent,
    stream_event_from_dict,
)
from ._chat_contracts_common import (
    AI_CHAT_FEATURE,
    CHAT_CONTRACTS_VERSION,
    ChatEventType,
    ChatToolChoice,
    DegradedMemorySource,
    EpisodeSourceType,
    MemoryCitationType,
    MemoryType,
)
from ._chat_contracts_memory import (
    ChatMemoryScope,
    ChatTurn,
    DeclarativeProfile,
    EpisodeCitation,
    EpisodeTransition,
    EpisodicMemoryRead,
    MemoryContextRequest,
    MemoryContextResponse,
    MemoryNamespace,
    MemoryProvenance,
    MemoryProvenanceSource,
    MemoryReadOptions,
    SemanticMemoryRead,
    TaskEpisode,
)
from .target_contracts import ValidationStatus

__all__ = [
    "AI_CHAT_FEATURE",
    "CHAT_CONTRACTS_VERSION",
    "ChatEventType",
    "ChatMemoryScope",
    "ChatMessageRequest",
    "ChatMessageStreamEvent",
    "ChatToolChoice",
    "ChatTurn",
    "DeclarativeProfile",
    "DegradedMemorySource",
    "EpisodeCitation",
    "EpisodeSourceType",
    "EpisodeTransition",
    "EpisodicMemoryRead",
    "MemoryCitationType",
    "MemoryContextRequest",
    "MemoryContextResponse",
    "MemoryNamespace",
    "MemoryProvenance",
    "MemoryProvenanceSource",
    "MemoryReadOptions",
    "MemoryType",
    "SemanticMemoryRead",
    "TaskEpisode",
    "ValidationStatus",
    "stream_event_from_dict",
]
