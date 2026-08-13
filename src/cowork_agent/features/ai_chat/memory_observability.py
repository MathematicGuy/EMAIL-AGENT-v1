"""Metadata-only observability contracts for AI Chat memory operations."""

from __future__ import annotations

import logging
import threading
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
            not self.reason_code.replace("_", "").isalnum() or len(self.reason_code) > 64
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


class MemoryOperationMetrics:
    """Thread-safe aggregator for memory operation events."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[tuple[str, str, str], int] = {}
        self._safety: dict[str, int] = {}
        self._latency: dict[tuple[str, str, str], tuple[int, int]] = {}

    def record(self, event: MemoryOperationEvent) -> None:
        key = (event.memory_type.value, event.operation.value, event.outcome.value)
        with self._lock:
            self._counts[key] = self._counts.get(key, 0) + 1
            if event.outcome == MemoryOutcome.DENIED:
                reason = event.reason_code if event.reason_code is not None else "unspecified"
                self._safety[reason] = self._safety.get(reason, 0) + 1
            if event.latency_ms > 0:
                total, samples = self._latency.get(key, (0, 0))
                self._latency[key] = (total + event.latency_ms, samples + 1)
            else:
                if key not in self._latency:
                    self._latency[key] = (0, 0)

    def snapshot(self) -> dict[tuple[str, str, str], int]:
        with self._lock:
            return dict(self._counts)

    def safety_incidents(self) -> dict[str, int]:
        with self._lock:
            return dict(self._safety)

    def latency_summary(self) -> dict[tuple[str, str, str], tuple[int, int]]:
        with self._lock:
            return dict(self._latency)


class LoggingMemoryOperationSink:
    """Production-safe metadata-only sink that never raises."""

    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        metrics: MemoryOperationMetrics | None = None,
    ) -> None:
        self._logger = logger if logger is not None else logging.getLogger("cowork_agent.memory")
        self._metrics = metrics if metrics is not None else MemoryOperationMetrics()

    def emit(self, event: MemoryOperationEvent) -> None:
        try:
            self._metrics.record(event)
            if event.outcome == MemoryOutcome.DENIED:
                self._logger.error("memory_safety_incident %s", event.to_dict())
            else:
                self._logger.info("memory_operation %s", event.to_dict())
        except Exception:
            pass
