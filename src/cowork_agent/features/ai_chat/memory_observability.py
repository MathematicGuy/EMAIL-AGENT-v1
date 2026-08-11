"""Metadata-only observability contracts for AI Chat memory operations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from cowork_agent.domain.chat_contracts import MemoryType

_MAX_COUNT = 10_000


class MemoryOperation(StrEnum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"


class MemoryOutcome(StrEnum):
    REQUESTED = "requested"
    SUCCESS = "success"
    DEGRADED = "degraded"
    DENIED = "denied"


@dataclass(frozen=True, slots=True)
class MemoryOperationEvent:
    """Safe, low-cardinality event; intentionally contains no subject data."""

    memory_type: MemoryType
    operation: MemoryOperation
    outcome: MemoryOutcome
    result_count: int = 0
    filtered_count: int = 0
    latency_ms: int = 0
    reason_code: str | None = None
    feature: str = "ai_chat"

    def __post_init__(self) -> None:
        if self.feature != "ai_chat":
            raise ValueError("feature must be ai_chat")
        for name in ("result_count", "filtered_count", "latency_ms"):
            value = getattr(self, name)
            if not isinstance(value, int) or value < 0 or value > _MAX_COUNT:
                raise ValueError(f"{name} must be a bounded nonnegative integer")
        if self.reason_code is not None and (
            not self.reason_code.replace("_", "").isalnum()
            or len(self.reason_code) > 64
        ):
            raise ValueError("reason_code must be a short safe identifier")

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "feature": self.feature,
            "memory_type": self.memory_type.value,
            "operation": self.operation.value,
            "outcome": self.outcome.value,
            "result_count": self.result_count,
            "filtered_count": self.filtered_count,
            "latency_ms": self.latency_ms,
            "reason_code": self.reason_code,
        }


class MemoryOperationSink(Protocol):
    def emit(self, event: MemoryOperationEvent) -> None: ...


class NullMemoryOperationSink:
    def emit(self, event: MemoryOperationEvent) -> None:
        del event


class RecordingMemoryOperationSink:
    def __init__(self) -> None:
        self.events: tuple[MemoryOperationEvent, ...] = ()

    def emit(self, event: MemoryOperationEvent) -> None:
        self.events += (event,)
