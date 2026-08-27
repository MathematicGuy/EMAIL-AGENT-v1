"""Lease-bound Mistral chat replies with safe per-request attempt observations."""

from __future__ import annotations

import asyncio
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

    def stream_reply(
        self, request: ChatMessageRequest, context: GenerationContext
    ) -> AsyncIterator[str | ChatReplyChunk]:
        started_at = self._clock()
        request_id = self._request_id_factory()
        stream = self._reply.stream_reply(request, context).__aiter__()

        async def emit_terminal(error: BaseException | None) -> None:
            try:
                await self._emit(error, started_at, request_id)
            except Exception:
                pass

        return _AttemptObservableStream(stream, emit_terminal)

    async def _emit(self, error: BaseException | None, started_at: float, request_id: str) -> None:
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


class _AttemptObservableStream(AsyncIterator[str | ChatReplyChunk]):
    def __init__(
        self,
        stream: AsyncIterator[str | ChatReplyChunk],
        emit_terminal: Callable[[BaseException | None], Awaitable[None]],
    ) -> None:
        self._stream = stream
        self._emit_terminal_callback = emit_terminal
        self._buffered_chunk: str | ChatReplyChunk | None = None
        self._closed = False
        self._inner_closed = False
        self._terminal_emitted = False

    def __aiter__(self) -> AsyncIterator[str | ChatReplyChunk]:
        return self

    async def __anext__(self) -> str | ChatReplyChunk:
        if self._closed:
            raise StopAsyncIteration

        try:
            if self._buffered_chunk is None:
                self._buffered_chunk = await anext(self._stream)

            try:
                next_chunk = await anext(self._stream)
            except StopAsyncIteration:
                await self._emit_terminal(None)
                self._closed = True
                buffered_chunk = self._buffered_chunk
                self._buffered_chunk = None
                assert buffered_chunk is not None
                return buffered_chunk

            buffered_chunk = self._buffered_chunk
            self._buffered_chunk = next_chunk
            assert buffered_chunk is not None
            return buffered_chunk
        except StopAsyncIteration:
            await self._emit_terminal(None)
            self._closed = True
            raise
        except (GeneratorExit, asyncio.CancelledError) as exc:
            await self._emit_terminal(exc)
            self._closed = True
            await self._close_inner()
            raise
        except BaseException as exc:
            await self._emit_terminal(exc)
            self._closed = True
            await self._close_inner()
            raise

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._emit_terminal(asyncio.CancelledError())
        await self._close_inner()

    async def _emit_terminal(self, error: BaseException | None) -> None:
        if self._terminal_emitted:
            return
        self._terminal_emitted = True
        await self._emit_terminal_callback(error)

    async def _close_inner(self) -> None:
        if self._inner_closed:
            return
        self._inner_closed = True
        close = getattr(self._stream, "aclose", None)
        if close is not None:
            try:
                await close()
            except BaseException:
                pass


def _attempt_metadata(error: BaseException | None) -> tuple[int | None, int | None, str]:
    if error is None:
        return None, None, "succeeded"
    if isinstance(error, GeneratorExit | asyncio.CancelledError):
        return None, None, "cancelled"
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
