"""Metadata-only events for chat intent routing."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from cowork_agent.domain.chat_contracts import ChatRoute, IntentReasonCode


@dataclass(frozen=True, slots=True)
class IntentRoutingEvent:
    name: str
    user_id: str
    session_id: str
    route: ChatRoute | None = None
    reason_codes: tuple[IntentReasonCode, ...] = ()
    confidence: float | None = None
    latency_ms: int = 0
    model_id: str | None = None
    prompt_version: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "event": self.name,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "route": self.route.value if self.route is not None else None,
            "reason_codes": [code.value for code in self.reason_codes],
            "confidence": self.confidence,
            "latency_ms": self.latency_ms,
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
        }


class IntentRoutingSink(Protocol):
    def emit(self, event: IntentRoutingEvent) -> None: ...


class NullIntentRoutingSink:
    def emit(self, event: IntentRoutingEvent) -> None:
        del event


class RecordingIntentRoutingSink:
    def __init__(self) -> None:
        self.events: tuple[IntentRoutingEvent, ...] = ()

    def emit(self, event: IntentRoutingEvent) -> None:
        self.events += (event,)


class LoggingIntentRoutingSink:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("cowork_agent.chat_intent")

    def emit(self, event: IntentRoutingEvent) -> None:
        try:
            self._logger.info("chat_intent %s", event.to_dict())
        except Exception:
            pass
