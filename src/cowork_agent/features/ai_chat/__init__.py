"""Framework-free AI Chat orchestration, memory policies, and gateway."""

from .controller import (
    ChatController,
    ChatReplyUnavailable,
    ChatScopeMismatch,
    ChatSessionAccessDenied,
    InMemoryChatSessionRegistry,
    UnavailableChatReply,
)
from .deletion import MemoryDeletionReport
from .evaluation import (
    LaunchGateResult,
    LaunchThresholds,
    PairedEvaluationCase,
    PairedEvaluationReport,
    evaluate_launch_gate,
)
from .evaluation_dataset import (
    DATASET_VERSION,
    SYNTHETIC_DATASET,
    CaseLabels,
    DatasetEntry,
    DeterministicPairedScorer,
)
from .evaluation_runner import (
    PairedCaseScores,
    PairedScorer,
    run_paired_evaluation,
    thresholds_from_env,
)
from .generation_context import (
    CompanyEvidence,
    ContextSource,
    GenerationContext,
    LabeledSection,
    assemble_generation_context,
)
from .memory_gateway import MemoryGateway, MemorySourceUnavailableError, NamespaceAccessDenied
from .memory_observability import (
    MemoryOperation,
    MemoryOperationEvent,
    MemoryOperationSink,
    MemoryOutcome,
    NullMemoryOperationSink,
    RecordingMemoryOperationSink,
)
from .retention import ExpiredMemoryPurgePort, MemoryPurgeCoordinator, MemoryPurgeReport
from .retrieval_policy import is_explicit_task_request, select_memory_reads

__all__ = [
    "CaseLabels",
    "ChatController",
    "ChatReplyUnavailable",
    "ChatScopeMismatch",
    "ChatSessionAccessDenied",
    "CompanyEvidence",
    "ContextSource",
    "DATASET_VERSION",
    "DatasetEntry",
    "DeterministicPairedScorer",
    "GenerationContext",
    "LaunchGateResult",
    "LaunchThresholds",
    "LabeledSection",
    "InMemoryChatSessionRegistry",
    "MemoryGateway",
    "MemoryDeletionReport",
    "PairedCaseScores",
    "PairedEvaluationCase",
    "PairedEvaluationReport",
    "PairedScorer",
    "MemoryOperation",
    "MemoryPurgeCoordinator",
    "MemoryPurgeReport",
    "MemoryOperationEvent",
    "MemoryOperationSink",
    "MemoryOutcome",
    "MemorySourceUnavailableError",
    "NamespaceAccessDenied",
    "ExpiredMemoryPurgePort",
    "NullMemoryOperationSink",
    "RecordingMemoryOperationSink",
    "SYNTHETIC_DATASET",
    "UnavailableChatReply",
    "assemble_generation_context",
    "evaluate_launch_gate",
    "is_explicit_task_request",
    "run_paired_evaluation",
    "select_memory_reads",
    "thresholds_from_env",
]
