import asyncio
import json
from collections.abc import AsyncIterator

import pytest

from cowork_agent.domain.chat_contracts import ChatMessageRequest, MemoryContextResponse
from cowork_agent.features.ai_chat.controller import ChatReplyUnavailable
from cowork_agent.features.ai_chat.generation_context import assemble_generation_context
from cowork_agent.features.ai_chat.ports import ChatReplyChunk
from cowork_agent.features.batch_evaluation.credentials import CredentialLeasingPool
from cowork_agent.integrations.llm.evaluation_mistral import MistralEvaluationReplyFactory
from cowork_agent.integrations.llm.providers.mistral import MistralAPIError


async def _collect(reply: object) -> tuple[ChatReplyChunk, ...]:
    request = ChatMessageRequest("session-1", "private prompt", "idem-1")
    context = assemble_generation_context(
        request,
        MemoryContextResponse(
            turns=(), profile=None, episodes=(), semantic_context=None, degraded=False, degraded_sources=()
        ),
    )
    stream = getattr(reply, "stream_reply")(request, context)
    return tuple([chunk async for chunk in stream])


def _lease() -> object:
    async def scenario() -> object:
        pool = CredentialLeasingPool.from_env(
            "MISTRAL_API_KEY", {"MISTRAL_API_KEY": "secret-evaluation-key"}
        )
        return await pool.lease()

    return asyncio.run(scenario())


@pytest.mark.parametrize(
    ("error", "outcome", "status", "retry_after"),
    [
        (MistralAPIError("rate limited", status_code=429, retry_after_seconds=17), "rate_limited", 429, 17),
        (MistralAPIError("not authorized", status_code=401), "authentication_failed", 401, None),
        (TimeoutError("private transport detail"), "timed_out", None, None),
    ],
)
def test_evaluation_reply_records_safe_failure_metadata_without_content_or_key(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    outcome: str,
    status: int | None,
    retry_after: int | None,
) -> None:
    events: list[object] = []

    async def failing_stream(*args: object, **kwargs: object) -> AsyncIterator[ChatReplyChunk]:
        del args, kwargs
        raise ChatReplyUnavailable("configured chat provider is unavailable") from error
        yield ChatReplyChunk("unreachable")

    monkeypatch.setattr(
        "cowork_agent.integrations.llm.chat_reply.MistralChatReply.from_settings",
        classmethod(lambda cls, settings: type("FakeReply", (), {"stream_reply": failing_stream})()),
    )
    reply = MistralEvaluationReplyFactory().bind(_lease(), "mistral-small", events.append)

    with pytest.raises(ChatReplyUnavailable, match="configured chat provider is unavailable"):
        asyncio.run(_collect(reply))

    assert len(events) == 1
    event = events[0]
    assert event.outcome == outcome
    assert event.status_code == status
    assert event.retry_after_seconds == retry_after
    assert event.latency_ms >= 0
    assert event.request_attempt_id.startswith("mistral-request-")
    assert "private prompt" not in repr(event)
    assert "private transport detail" not in repr(event)
    assert "secret-evaluation-key" not in repr(event)


def test_evaluation_reply_records_success_without_reply_content_or_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []

    async def successful_stream(*args: object, **kwargs: object) -> AsyncIterator[ChatReplyChunk]:
        del args, kwargs
        yield ChatReplyChunk("private reply")

    monkeypatch.setattr(
        "cowork_agent.integrations.llm.chat_reply.MistralChatReply.from_settings",
        classmethod(lambda cls, settings: type("FakeReply", (), {"stream_reply": successful_stream})()),
    )
    reply = MistralEvaluationReplyFactory().bind(_lease(), "mistral-small", events.append)

    chunks = asyncio.run(_collect(reply))

    assert chunks == (ChatReplyChunk("private reply"),)
    assert len(events) == 1
    assert events[0].outcome == "succeeded"
    assert events[0].status_code is None
    assert "private reply" not in repr(events[0])
    assert "secret-evaluation-key" not in repr(events[0])


def test_evaluation_reply_uses_the_hidden_lease_key_only_to_create_transient_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    async def successful_stream(*args: object, **kwargs: object) -> AsyncIterator[ChatReplyChunk]:
        del args, kwargs
        yield ChatReplyChunk(json.dumps({"safe": True}))

    def fake_from_settings(settings: object) -> object:
        captured.append(settings)
        return type("FakeReply", (), {"stream_reply": successful_stream})()

    monkeypatch.setattr(
        "cowork_agent.integrations.llm.chat_reply.MistralChatReply.from_settings",
        classmethod(lambda cls, settings: fake_from_settings(settings)),
    )
    MistralEvaluationReplyFactory(max_output_tokens=123, timeout_seconds=9).bind(
        _lease(), "mistral-small", lambda event: None
    )

    assert captured[0].model == "mistral-small"
    assert captured[0].max_output_tokens == 123
    assert captured[0].timeout_seconds == 9
    assert "secret-evaluation-key" not in repr(captured[0])
