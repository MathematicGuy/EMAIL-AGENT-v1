import asyncio
from datetime import UTC, datetime

import pytest

from cowork_agent.config import FaucetSettings, GeminiSettings, GroqSettings, OpenRouterSettings
from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatMessageRequest,
    EpisodeCitation,
    EpisodeSourceType,
    MemoryContextResponse,
    MemoryNamespace,
    MemoryType,
    SemanticMemoryQuery,
    TaskEpisode,
)
from cowork_agent.domain.target_contracts import (
    RetrievalStatus,
    SemanticChunk,
    SemanticRetrievalRequest,
    SemanticRetrievalResponse,
    ValidationStatus,
)
from cowork_agent.features.ai_chat.controller import ChatReplyUnavailable
from cowork_agent.features.ai_chat.generation_context import assemble_generation_context
from cowork_agent.integrations.llm.chat_reply import (
    FaucetChatReply,
    GeminiChatReply,
    GroqChatReply,
    OpenRouterChatReply,
)
from cowork_agent.integrations.rag.chat_memory import SemanticChatMemoryAdapter


def test_configured_chat_reply_uses_only_generation_context_and_returns_proposal() -> None:
    received: list[dict[str, object]] = []

    async def complete(payload: dict[str, object]) -> dict[str, object]:
        received.append(payload)
        return {
            "assistant_text": "I prepared the requested plan.",
            "conversation_title": "Quarterly report plan",
            "task_proposal": {
                "task_title": "Prepare quarterly report",
                "minimal_request_paraphrase": "Prepare a quarterly report",
                "action_plan": ["Collect results", "Draft report"],
                "rag_citations": [],
                "missing_information": [],
                "prompt_version": "chat-v2",
                "confidence": 0.9,
            },
        }

    request = ChatMessageRequest("session-1", "Create a task for the report.", "idem-1")
    context = assemble_generation_context(
        request,
        MemoryContextResponse(
            turns=(), profile=None, episodes=(), semantic_context=None,
            degraded=False, degraded_sources=(),
        ),
    )
    reply = FaucetChatReply(model="faucet-model", complete=complete)

    chunks = asyncio.run(_collect(reply, request, context))

    assert chunks[0].text == "I prepared the requested plan."
    assert chunks[0].task_proposal is not None
    assert chunks[0].task_proposal.task_title == "Prepare quarterly report"
    assert chunks[0].task_proposal.model_id == "faucet-model"
    assert chunks[0].conversation_title == "Quarterly report plan"
    assert received[0]["context"]["current_instruction"] == "Create a task for the report."
    assert "email" not in str(received[0]).casefold()


def test_configured_provider_settings_select_the_matching_chat_reply_adapter() -> None:
    assert isinstance(
        GeminiChatReply.from_settings(
            GeminiSettings(("key",), "model", True, 1, 1, 1, 1)
        ),
        GeminiChatReply,
    )
    assert isinstance(
        GroqChatReply.from_settings(GroqSettings("key", "model", 1, 1)), GroqChatReply
    )
    assert isinstance(
        FaucetChatReply.from_settings(FaucetSettings("key", "model", 1, 1, 1)),
        FaucetChatReply,
    )


def test_configured_reply_keeps_company_evidence_and_advisory_episodes_separate() -> None:
    received: list[dict[str, object]] = []

    async def complete(payload: dict[str, object]) -> dict[str, object]:
        received.append(payload)
        return {"assistant_text": "Grounded answer", "task_proposal": None}

    request = ChatMessageRequest("session-1", "What does the company policy say?", "idem-2")
    context = assemble_generation_context(
        request,
        MemoryContextResponse(
            turns=(),
            profile=None,
            episodes=(_eligible_episode(),),
            semantic_context={
                "source_label": "current_company_evidence",
                "retrieval_status": "success",
                "chunks": ({"chunk_id": "chunk-1", "text": "Policy requires review."},),
                "citations": (
                    {
                        "document_id": "policy-1",
                        "document_title": "Policy",
                        "section": "Review",
                        "source_url": "https://docs.example.com/policy",
                    },
                ),
                "scores": ({"chunk_id": "chunk-1", "relevance_score": 0.9},),
            },
            degraded=False,
            degraded_sources=(),
        ),
    )
    reply = FaucetChatReply(model="faucet-model", complete=complete)

    asyncio.run(_collect(reply, request, context))

    payload = received[0]["context"]
    assert payload["current_company_evidence"]["chunks"] == [
        {"chunk_id": "chunk-1", "text": "Policy requires review."}
    ]
    assert payload["current_company_evidence"]["scores"] == [
        {"chunk_id": "chunk-1", "relevance_score": 0.9}
    ]
    assert payload["advisory_episodes"] == [
        {
            "task_title": "Earlier task",
            "action_plan": ["Use the earlier plan"],
            "validation_status": "user_approved",
        }
    ]
    assert payload["conflict_precedence"] == [
        "current_instruction",
        "current_company_evidence",
        "stored_preference",
        "advisory_episode",
    ]
    assert all(
        forbidden not in str(payload).casefold() for forbidden in ("email", "tool", "mailbox")
    )


def test_configured_reply_allows_only_current_company_evidence_citations() -> None:
    request, context = _grounded_task_context()

    async def allowed_response(payload: dict[str, object]) -> dict[str, object]:
        del payload
        return _task_response(
            [
                {
                    "document_id": "policy-1",
                    "document_title": "Policy",
                    "section": "Review",
                    "source_url": "https://docs.example.com/policy",
                }
            ]
        )

    allowed = FaucetChatReply(model="faucet-model", complete=allowed_response)
    chunks = asyncio.run(_collect(allowed, request, context))

    assert chunks[0].task_proposal is not None
    assert chunks[0].task_proposal.rag_citations == (
        EpisodeCitation("policy-1", "Policy", "Review", "https://docs.example.com/policy"),
    )

    async def fabricated_response(payload: dict[str, object]) -> dict[str, object]:
        del payload
        return _task_response(
            [
                {
                    "document_id": "invented",
                    "document_title": "Invented",
                    "section": None,
                    "source_url": "https://invalid.example.com",
                }
            ]
        )

    fabricated = FaucetChatReply(model="faucet-model", complete=fabricated_response)
    with pytest.raises(ChatReplyUnavailable):
        asyncio.run(_collect(fabricated, request, context))


def test_configured_reply_rejects_citations_without_current_company_evidence() -> None:
    request = ChatMessageRequest("session-1", "Create a task for this.", "idem-3")
    context = assemble_generation_context(
        request,
        MemoryContextResponse(
            turns=(), profile=None, episodes=(), semantic_context=None,
            degraded=False, degraded_sources=(),
        ),
    )

    async def response(payload: dict[str, object]) -> dict[str, object]:
        del payload
        return _task_response(
            [
                {
                    "document_id": "policy-1",
                    "document_title": "Policy",
                    "section": "Review",
                    "source_url": "https://docs.example.com/policy",
                }
            ]
        )

    reply = FaucetChatReply(model="faucet-model", complete=response)
    with pytest.raises(ChatReplyUnavailable):
        asyncio.run(_collect(reply, request, context))


def test_configured_reply_projects_real_semantic_adapter_citations_to_task_coordinates() -> None:
    request = ChatMessageRequest("session-1", "Turn this into a task.", "idem-5")
    semantic_context = asyncio.run(
        SemanticChatMemoryAdapter(_SemanticMemory()).read_semantic_context(
            MemoryNamespace(
                scope=ChatMemoryScope("tenant-1", "user-1", "session-1"),
                memory_type=MemoryType.SEMANTIC,
                record_id=None,
                source_id=None,
            ),
            SemanticMemoryQuery("travel policy", max_items=1, min_score=0.6, timeout_ms=500),
        )
    )
    context = assemble_generation_context(
        request,
        MemoryContextResponse(
            turns=(),
            profile=None,
            episodes=(),
            semantic_context=semantic_context,
            degraded=False,
            degraded_sources=(),
        ),
    )

    async def response(payload: dict[str, object]) -> dict[str, object]:
        del payload
        return _task_response(
            [
                {
                    "document_id": "travel-policy",
                    "document_title": "Travel Policy",
                    "section": "Receipts",
                    "source_url": "https://docs.example.com/travel",
                }
            ]
        )

    reply = FaucetChatReply(model="faucet-model", complete=response)
    chunks = asyncio.run(_collect(reply, request, context))

    assert chunks[0].task_proposal is not None
    assert chunks[0].task_proposal.rag_citations == (
        EpisodeCitation("travel-policy", "Travel Policy", "Receipts", "https://docs.example.com/travel"),
    )


def _grounded_task_context():
    request = ChatMessageRequest("session-1", "Create a task for this policy.", "idem-4")
    return request, assemble_generation_context(
        request,
        MemoryContextResponse(
            turns=(),
            profile=None,
            episodes=(),
            semantic_context={
                "source_label": "current_company_evidence",
                "retrieval_status": "success",
                "chunks": ({"chunk_id": "chunk-1", "text": "Policy requires review."},),
                "citations": (
                    {
                        "document_id": "policy-1",
                        "document_title": "Policy",
                        "section": "Review",
                        "source_url": "https://docs.example.com/policy",
                    },
                ),
                "scores": (),
            },
            degraded=False,
            degraded_sources=(),
        ),
    )


def _task_response(citations: list[dict[str, object]]) -> dict[str, object]:
    return {
        "assistant_text": "Task proposal",
        "task_proposal": {
            "task_title": "Prepare report",
            "minimal_request_paraphrase": "Prepare a report",
            "action_plan": ["Draft the report"],
            "rag_citations": citations,
            "missing_information": [],
            "prompt_version": "chat-v2",
            "confidence": 0.9,
        },
    }


class _SemanticMemory:
    async def retrieve(self, request: SemanticRetrievalRequest) -> SemanticRetrievalResponse:
        del request
        return SemanticRetrievalResponse(
            query_id="query-1",
            chunks=(
                SemanticChunk(
                    chunk_id="chunk-1",
                    document_id="travel-policy",
                    document_title="Travel Policy",
                    section="Receipts",
                    text="Keep receipts.",
                    source_url="https://docs.example.com/travel",
                    document_version="2026-08",
                    relevance_score=0.9,
                    rerank_score=None,
                ),
            ),
            retrieval_status=RetrievalStatus.SUCCESS,
            latency_ms=1,
        )


def _eligible_episode() -> TaskEpisode:
    now = datetime(2026, 8, 11, tzinfo=UTC)
    return TaskEpisode(
        episode_id="episode-1",
        record_id="record-1",
        user_id="user-1",
        chat_session_id="earlier-session",
        chat_turn_id="turn-1",
        creation_reason="explicit_user_task_request",
        task_title="Earlier task",
        minimal_request_paraphrase="Earlier request",
        action_plan=("Use the earlier plan",),
        rag_citations=(EpisodeCitation("doc-1", "Policy", None, "https://docs.example.com"),),
        missing_information=(),
        validation_status=ValidationStatus.USER_APPROVED,
        retrieval_eligible=True,
        source_type=EpisodeSourceType.SYSTEM_GENERATED_CHAT_TASK,
        created_at=now,
        updated_at=now,
        pipeline_version="v2-m4",
        model_id=None,
        prompt_version=None,
        confidence=None,
    )
async def _collect(reply: object, request: ChatMessageRequest, context: object):
    return [chunk async for chunk in reply.stream_reply(request, context)]  # type: ignore[attr-defined]


def test_gemini_chat_reply_rotates_past_rate_limited_key() -> None:
    from cowork_agent.integrations.llm.providers.gemini import GeminiRateLimitError

    attempted_keys: list[str] = []

    class FakeTransport:
        async def generate(
            self,
            *,
            api_key: str,
            model: str,
            prompt: str,
            schema: object,
            timeout_seconds: int,
            system_instruction: str | None = None,
        ) -> dict[str, object]:
            del model, prompt, schema, timeout_seconds, system_instruction
            attempted_keys.append(api_key)
            if api_key == "key-1":
                raise GeminiRateLimitError("Rate limit on key-1")
            return {
                "assistant_text": "Rotated reply",
                "conversation_title": "Title",
                "citation_ids": [],
                "task_proposal": None,
            }

    settings = GeminiSettings(
        api_keys=("key-1", "key-2"),
        model="gemini-3.5-flash-lite",
        rotate_on_rate_limit=True,
        max_attempts=2,
        max_emails_per_batch=5,
        max_input_tokens=1000,
        timeout_seconds=30,
    )
    reply = GeminiChatReply.from_settings(settings, transport=FakeTransport())
    request = ChatMessageRequest("session-1", "Hello", "idem-1")
    context = assemble_generation_context(
        request,
        MemoryContextResponse(
            turns=(),
            profile=None,
            episodes=(),
            semantic_context=None,
            degraded=False,
            degraded_sources=(),
        ),
    )
    chunks = asyncio.run(_collect(reply, request, context))
    assert chunks[0].text == "Rotated reply"
    assert attempted_keys == ["key-1", "key-2"]


def test_openrouter_chat_reply_hops_to_gemini_on_openrouter_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cowork_agent.integrations.llm.providers.openrouter import OpenRouterAPIError

    gemini_calls: list[object] = []

    async def fake_execute(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        raise OpenRouterAPIError("upstream down")

    async def fake_gemini(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        gemini_calls.append(True)
        return {
            "assistant_text": "Gemini last-resort reply",
            "conversation_title": "Last resort",
            "citation_ids": [],
            "task_proposal": None,
        }

    monkeypatch.setattr(
        "cowork_agent.integrations.llm.providers.openrouter.execute_chat_completion",
        fake_execute,
    )
    monkeypatch.setattr(
        "cowork_agent.integrations.llm.last_resort.gemini_json_complete",
        fake_gemini,
    )
    reply = OpenRouterChatReply.from_settings(
        _openrouter_settings(), GeminiSettings(("key",), "model", True, 1, 1, 1, 1)
    )
    request = ChatMessageRequest("session-1", "Hello", "idem-hop")
    context = assemble_generation_context(
        request,
        MemoryContextResponse(
            turns=(),
            profile=None,
            episodes=(),
            semantic_context=None,
            degraded=False,
            degraded_sources=(),
        ),
    )

    chunks = asyncio.run(_collect(reply, request, context))

    assert chunks[0].text == "Gemini last-resort reply"
    assert gemini_calls == [True]


def test_openrouter_chat_reply_does_not_hop_on_schema_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gemini_calls: list[object] = []

    async def fake_execute(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        return {"assistant_text": "x"}

    async def fake_gemini(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        gemini_calls.append(True)
        return {
            "assistant_text": "should not be used",
            "conversation_title": "Unused",
            "citation_ids": [],
            "task_proposal": None,
        }

    monkeypatch.setattr(
        "cowork_agent.integrations.llm.providers.openrouter.execute_chat_completion",
        fake_execute,
    )
    monkeypatch.setattr(
        "cowork_agent.integrations.llm.last_resort.gemini_json_complete",
        fake_gemini,
    )
    reply = OpenRouterChatReply.from_settings(
        _openrouter_settings(), GeminiSettings(("key",), "model", True, 1, 1, 1, 1)
    )
    request = ChatMessageRequest("session-1", "Hello", "idem-schema")
    context = assemble_generation_context(
        request,
        MemoryContextResponse(
            turns=(),
            profile=None,
            episodes=(),
            semantic_context=None,
            degraded=False,
            degraded_sources=(),
        ),
    )

    with pytest.raises(ChatReplyUnavailable):
        asyncio.run(_collect(reply, request, context))
    assert gemini_calls == []


def test_openrouter_chat_reply_from_settings_without_last_resort() -> None:
    assert isinstance(
        OpenRouterChatReply.from_settings(_openrouter_settings()), OpenRouterChatReply
    )


def _openrouter_settings() -> OpenRouterSettings:
    return OpenRouterSettings.from_env(
        {"OPENROUTER_API_KEY": "test-key", "OPENROUTER_MODEL": "deepseek/x"},
        load_env_file=False,
    )
