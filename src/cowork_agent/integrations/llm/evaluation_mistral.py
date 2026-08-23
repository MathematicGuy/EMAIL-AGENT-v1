"""Lease-bound Mistral chat replies with safe per-request attempt observations."""

from __future__ import annotations

import itertools
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

from cowork_agent.config import MistralSettings
from cowork_agent.domain.chat_contracts import ChatMessageRequest
from cowork_agent.features.ai_chat.generation_context import GenerationContext
from cowork_agent.features.ai_chat.ports import ChatReplyChunk, ChatReplyPort
from cowork_agent.features.batch_evaluation.contracts import ProviderAttemptEvent
from cowork_agent.features.batch_evaluation.credentials import CredentialLease
from cowork_agent.integrations.llm.chat_reply import MistralChatReply
from cowork_agent.integrations.llm.providers.mistral import MistralAPIError

AttemptSink = Callable[[ProviderAttemptEvent], Awaitable[None] | None]


@dataclass(slots=True)
class MistralEvaluationReplyFactory:
    """Build transient Mistral replies from a lease without retaining its secret."""

    max_emails_per_batch: int = 1
    max_output_tokens: int = 2048
    timeout_seconds: int = 60
    clock: Callable[[], float] = time.monotonic
    _request_ids: itertools.count[int] = field(default_factory=itertools.count, init=False)

    def bind(
        self,
        lease: CredentialLease,
        model: str,
        attempt_sink: AttemptSink,
    ) -> ChatReplyPort:
        settings = MistralSettings(
            api_key=lease._api_key,
            model=model,
            max_emails_per_batch=self.max_emails_per_batch,
            max_output_tokens=self.max_output_tokens,
            timeout_seconds=self.timeout_seconds,
        )
        return _AttemptObservableMistralReply(
            reply=MistralChatReply.from_settings(settings),
            credential_alias=lease.alias,
            attempt_sink=attempt_sink,
            clock=self.clock,
            request_id_factory=lambda: f"mistral-request-{next(self._request_ids) + 1}",
        )


class _AttemptObservableMistralReply:
    def __init__(
        self,
        *,
        reply: ChatReplyPort,
        credential_alias: str,
        attempt_sink: AttemptSink,
        clock: Callable[[], float],
        request_id_factory: Callable[[], str],
    ) -> None:
        self._reply = reply
        self._credential_alias = credential_alias
        self._attempt_sink = attempt_sink
        self._clock = clock
        self._request_id_factory = request_id_factory

    async def stream_reply(
        self, request: ChatMessageRequest, context: GenerationContext
    ) -> AsyncIterator[str | ChatReplyChunk]:
        started_at = self._clock()
        request_id = self._request_id_factory()
        try:
            async for chunk in self._reply.stream_reply(request, context):
                yield chunk
        except BaseException as exc:
            try:
                await self._emit(exc, started_at, request_id)
            except Exception:
                pass
            raise
        await self._emit(None, started_at, request_id)

    async def _emit(
        self, error: BaseException | None, started_at: float, request_id: str
    ) -> None:
        status_code, retry_after_seconds, outcome = _attempt_metadata(error)
        event = ProviderAttemptEvent(
            credential_alias=self._credential_alias,
            request_attempt_id=request_id,
            outcome=outcome,
            status_code=status_code,
            retry_after_seconds=retry_after_seconds,
            latency_ms=max(0, int((self._clock() - started_at) * 1000)),
        )
        result = self._attempt_sink(event)
        if result is not None:
            await result


def _attempt_metadata(error: BaseException | None) -> tuple[int | None, int | None, str]:
    if error is None:
        return None, None, "succeeded"
    mistral_error = next(
        (cause for cause in _cause_chain(error) if isinstance(cause, MistralAPIError)), None
    )
    if mistral_error is not None:
        status_code = _safe_status_code(mistral_error.status_code)
        retry_after = _safe_retry_after(mistral_error.retry_after_seconds)
        if status_code == 429:
            return status_code, retry_after, "rate_limited"
        if status_code in {401, 403}:
            return status_code, retry_after, "authentication_failed"
        if status_code is not None and status_code >= 500:
            return status_code, retry_after, "provider_unavailable"
        if any(isinstance(cause, TimeoutError) for cause in _cause_chain(error)):
            return status_code, retry_after, "timed_out"
        return status_code, retry_after, "failed"
    if any(isinstance(cause, TimeoutError) for cause in _cause_chain(error)):
        return None, None, "timed_out"
    return None, None, "failed"


def _cause_chain(error: BaseException) -> tuple[BaseException, ...]:
    causes: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and current not in causes:
        causes.append(current)
        current = current.__cause__ or current.__context__
    return tuple(causes)


def _safe_status_code(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 100:
        return value
    return None


def _safe_retry_after(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
