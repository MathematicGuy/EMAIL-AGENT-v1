"""One-decision-per-turn classifier orchestration with asymmetric fallback."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from time import monotonic
from typing import Protocol

from cowork_agent.domain.chat_contracts import (
    MAX_CLASSIFIER_TURNS,
    MAX_RETRIEVAL_QUERY_LENGTH,
    ChatIntent,
    ChatMemoryScope,
    ChatMessageRequest,
    ChatTurn,
    IntentClassifierInput,
    IntentDecision,
    IntentReasonCode,
    ReadyDocumentRef,
    RoutingOutcome,
)
from cowork_agent.features.ai_chat.ports import (
    IntentClassifierPort,
    ReadyDocumentCatalogPort,
)
from cowork_agent.persistence.repositories.projects import ProjectDocument

from .observability import (
    IntentRoutingEvent,
    IntentRoutingSink,
    NullIntentRoutingSink,
)
from .prompt import INTENT_PROMPT_VERSION
from .resolver import finalize_route


class IntentClassifierError(RuntimeError):
    """Expected provider or schema failure eligible for the routing retry policy."""


class IntentClassifierTimeout(IntentClassifierError):
    pass


class IntentClassifierInvalidOutput(IntentClassifierError):
    pass


class IntentClassifierUnavailable(IntentClassifierError):
    pass


class EmptyReadyDocumentCatalog:
    """Safe M4 default until the M1–M3 document repository is composed."""

    async def list_ready(
        self, scope: ChatMemoryScope, *, at: datetime
    ) -> tuple[ReadyDocumentRef, ...]:
        del scope, at
        return ()


class CanonicalReadyDocumentRepository(Protocol):
    async def list_ready_for_scope(
        self,
        workspace_id: str,
        user_id: str,
        project_id: str,
        *,
        at: datetime,
    ) -> tuple[ProjectDocument, ...]: ...


class CanonicalReadyDocumentCatalog:
    """Ready-document metadata sourced from the canonical Postgres plane."""

    def __init__(self, repository: CanonicalReadyDocumentRepository) -> None:
        self._repository = repository

    async def list_ready(
        self, scope: ChatMemoryScope, *, at: datetime
    ) -> tuple[ReadyDocumentRef, ...]:
        documents = await self._repository.list_ready_for_scope(
            "local", scope.user_id, scope.project_id, at=at
        )
        return tuple(
            ReadyDocumentRef(document_id=document.id, title=document.filename)
            for document in documents
        )


class ChatRoutingService:
    def __init__(
        self,
        *,
        classifier: IntentClassifierPort,
        catalog: ReadyDocumentCatalogPort,
        model_id: str,
        timeout_ms: int = 10_000,
        max_attempts: int = 2,
        tool_axis_enabled: bool = False,
        prompt_version: str = INTENT_PROMPT_VERSION,
        sink: IntentRoutingSink | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        if timeout_ms <= 0 or max_attempts != 2:
            raise ValueError("routing requires a positive timeout and exactly two attempts")
        self._classifier = classifier
        self._catalog = catalog
        self._model_id = model_id
        self._timeout_seconds = timeout_ms / 1000
        self._max_attempts = max_attempts
        self._tool_axis_enabled = tool_axis_enabled
        self._prompt_version = prompt_version
        self._sink = sink or NullIntentRoutingSink()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic_clock = monotonic_clock

    async def route(
        self,
        *,
        scope: ChatMemoryScope,
        request: ChatMessageRequest,
        recent_turns: tuple[ChatTurn, ...],
    ) -> RoutingOutcome:
        ready_documents = await self._catalog.list_ready(scope, at=self._clock())
        classifier_input = IntentClassifierInput(
            current_message=request.user_message,
            recent_turns=recent_turns[-MAX_CLASSIFIER_TURNS:],
            ready_documents=ready_documents,
        )
        started = self._monotonic_clock()
        decision: IntentDecision | None = None
        retried = False
        for attempt in range(self._max_attempts):
            try:
                decision = await asyncio.wait_for(
                    self._classifier.classify(classifier_input),
                    timeout=self._timeout_seconds,
                )
                break
            except TimeoutError:
                pass
            except IntentClassifierError:
                pass
            if attempt == 0:
                retried = True
                self._emit("chat.intent.classifier_retried", scope, started=started)
        fallback_used = decision is None
        if decision is None:
            decision = _fallback_decision(request.user_message, bool(ready_documents))
            if ready_documents:
                self._emit("chat.intent.fallback_to_rag", scope, started=started)
        outcome = finalize_route(
            decision,
            has_ready_documents=bool(ready_documents),
            tool_axis_enabled=self._tool_axis_enabled,
            classifier_retried=retried,
            fallback_used=fallback_used,
            prompt_version=self._prompt_version,
        )
        if IntentReasonCode.TOOL_REQUESTED_BUT_DISABLED in outcome.reason_codes:
            self._emit("chat.intent.tool_downgraded", scope, outcome, started)
        if IntentReasonCode.NO_READY_DOCUMENTS in outcome.reason_codes:
            self._emit("chat.intent.precondition_downgraded", scope, outcome, started)
        self._emit("chat.intent.classified", scope, outcome, started)
        self._emit("chat.route.decided", scope, outcome, started)
        return outcome

    def _emit(
        self,
        name: str,
        scope: ChatMemoryScope,
        outcome: RoutingOutcome | None = None,
        started: float | None = None,
    ) -> None:
        latency_ms = (
            max(0, int((self._monotonic_clock() - started) * 1000)) if started is not None else 0
        )
        self._sink.emit(
            IntentRoutingEvent(
                name=name,
                user_id=scope.user_id,
                session_id=scope.session_id,
                route=outcome.route if outcome is not None else None,
                reason_codes=outcome.reason_codes if outcome is not None else (),
                confidence=(float(outcome.decision.confidence) if outcome is not None else None),
                latency_ms=latency_ms,
                model_id=self._model_id,
                prompt_version=self._prompt_version,
            )
        )


def _fallback_decision(user_message: str, has_ready_documents: bool) -> IntentDecision:
    query = " ".join(user_message.split())[:MAX_RETRIEVAL_QUERY_LENGTH]
    reasons = [IntentReasonCode.CLASSIFIER_UNAVAILABLE]
    if not has_ready_documents:
        reasons.append(IntentReasonCode.NO_READY_DOCUMENTS)
    return IntentDecision(
        intent=(ChatIntent.KNOWLEDGE_QUERY if has_ready_documents else ChatIntent.CHAT),
        needs_rag=has_ready_documents,
        needs_tool=False,
        tool_name=None,
        needs_clarification=False,
        retrieval_query=query if has_ready_documents else None,
        confidence=0.0,
        reason_codes=tuple(reasons),
    )
