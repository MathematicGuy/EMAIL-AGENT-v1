import asyncio
from datetime import UTC, datetime

import pytest

from cowork_agent.config import (
    GeminiSettings,
    MimoSettings,
    MistralSettings,
    OpenRouterSettings,
)
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
from cowork_agent.features.ai_chat.controller import ChatReplyUnavailable, ChatResponseInvalid
from cowork_agent.features.ai_chat.generation_context import assemble_generation_context
from cowork_agent.integrations.key_rotation import APIKeyRotator
from cowork_agent.integrations.llm import chat_reply
from cowork_agent.integrations.llm.chat_reply import (
    GeminiChatReply,
    MimoChatReply,
    MistralChatReply,
    OpenRouterChatReply,
    system_prompt_sha,
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
                "supersedes_index": None,
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
    reply = MistralChatReply(model="mistral-small-2603", complete=complete)

    chunks = asyncio.run(_collect(reply, request, context))

    assert chunks[0].text == "I prepared the requested plan."
    assert chunks[0].task_proposal is not None
    assert chunks[0].task_proposal.task_title == "Prepare quarterly report"
    assert chunks[0].task_proposal.model_id == "mistral-small-2603"
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
        MimoChatReply.from_settings(
            MimoSettings(
                APIKeyRotator(["key"], "Mimo"),
                "model",
                "https://token-plan-ams.xiaomimimo.com/v1",
                1,
                1,
                1,
            )
        ),
        MimoChatReply,
    )
    assert isinstance(
        MistralChatReply.from_settings(MistralSettings("key", "model", 1, 1, 1)),
        MistralChatReply,
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
    reply = MistralChatReply(model="mistral-small-2603", complete=complete)

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
            "index": 0,
            "task_title": "Earlier task",
            "action_plan": ["Use the earlier plan"],
            "validation_status": "user_approved",
            "updated_at": "2026-08-11T00:00:00+00:00",
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

    allowed = MistralChatReply(model="mistral-small-2603", complete=allowed_response)
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

    fabricated = MistralChatReply(model="mistral-small-2603", complete=fabricated_response)
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

    reply = MistralChatReply(model="mistral-small-2603", complete=response)
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

    reply = MistralChatReply(model="mistral-small-2603", complete=response)
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
            "supersedes_index": None,
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


def _revision_reply(supersedes_index: object) -> MistralChatReply:
    async def complete(payload: dict[str, object]) -> dict[str, object]:
        del payload
        return {
            "assistant_text": "Đã dời lịch.",
            "task_proposal": {
                "task_title": "Dời lịch tác vụ trước",
                "minimal_request_paraphrase": "Dời lịch tác vụ trước",
                "action_plan": ["Cập nhật ngày mới"],
                "rag_citations": [],
                "missing_information": [],
                "prompt_version": "chat-v2",
                "confidence": 0.9,
                "supersedes_index": supersedes_index,
            },
        }

    return MistralChatReply(model="mistral-small-2603", complete=complete)


def _revision_context() -> object:
    request = ChatMessageRequest("session-1", "Tạo tác vụ dời lịch tác vụ trước.", "idem-3")
    return assemble_generation_context(
        request,
        MemoryContextResponse(
            turns=(),
            profile=None,
            episodes=(_eligible_episode(),),
            semantic_context=None,
            degraded=False,
            degraded_sources=(),
        ),
    )


def test_configured_reply_resolves_a_supersedes_ordinal_to_the_episode_id() -> None:
    """Concern D: the model names a position, the server owns the identifier."""
    request = ChatMessageRequest("session-1", "Tạo tác vụ dời lịch tác vụ trước.", "idem-3")

    chunks = asyncio.run(_collect(_revision_reply(0), request, _revision_context()))

    assert chunks[0].task_proposal is not None
    assert chunks[0].task_proposal.supersedes == "episode-1"


def test_configured_reply_rejects_a_supersedes_ordinal_with_no_advisory_episode() -> None:
    request = ChatMessageRequest("session-1", "Tạo tác vụ dời lịch tác vụ trước.", "idem-3")

    with pytest.raises(ChatReplyUnavailable):
        asyncio.run(_collect(_revision_reply(7), request, _revision_context()))


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


def _dated_episode(
    *, episode_id: str, title: str, plan: str, updated_at: datetime
) -> TaskEpisode:
    return TaskEpisode(
        episode_id=episode_id,
        record_id=f"record-{episode_id}",
        user_id="user-1",
        chat_session_id="earlier-session",
        chat_turn_id=f"turn-{episode_id}",
        creation_reason="explicit_user_task_request",
        task_title=title,
        minimal_request_paraphrase=title,
        action_plan=(plan,),
        rag_citations=(),
        missing_information=(),
        validation_status=ValidationStatus.USER_APPROVED,
        retrieval_eligible=True,
        source_type=EpisodeSourceType.SYSTEM_GENERATED_CHAT_TASK,
        created_at=updated_at,
        updated_at=updated_at,
        pipeline_version="v2-m4",
        model_id=None,
        prompt_version=None,
        confidence=None,
    )


def test_advisory_episodes_carry_the_recency_the_store_ranked_them_by() -> None:
    # ep_update_01 of the v3 memory eval: two approved episodes about the same
    # passport submission, one superseding the other's date. Retrieval already
    # orders them newest-first — measured against the real SQLite store — and
    # then this payload threw the timestamps away, so the model saw two equal
    # facts that contradict each other and asserted the superseded 5 September
    # with certainty. A reader cannot prefer the later episode without being
    # told which one is later.
    received: list[dict[str, object]] = []

    async def complete(payload: dict[str, object]) -> dict[str, object]:
        received.append(payload)
        return {"assistant_text": "Answer", "task_proposal": None}

    superseding = _dated_episode(
        episode_id="episode-later",
        title="Dời ngày nộp hồ sơ hộ chiếu Cần Thơ",
        plan="Dời lịch nộp hồ sơ sang ngày 12 tháng 9.",
        updated_at=datetime(2026, 8, 21, 19, 31, tzinfo=UTC),
    )
    superseded = _dated_episode(
        episode_id="episode-earlier",
        title="Cấp lại hộ chiếu cho văn phòng Cần Thơ",
        plan="Nộp hồ sơ vào ngày 5 tháng 9.",
        updated_at=datetime(2026, 8, 21, 19, 30, tzinfo=UTC),
    )
    request = ChatMessageRequest("session-1", "Ngày nộp hồ sơ là ngày nào?", "idem-3")
    context = assemble_generation_context(
        request,
        MemoryContextResponse(
            turns=(),
            profile=None,
            episodes=(superseding, superseded),
            semantic_context=None,
            degraded=False,
            degraded_sources=(),
        ),
    )

    reply = MistralChatReply(model="mistral-small-2603", complete=complete)
    asyncio.run(_collect(reply, request, context))

    rendered = received[0]["context"]["advisory_episodes"]
    assert [episode["task_title"] for episode in rendered] == [
        "Dời ngày nộp hồ sơ hộ chiếu Cần Thơ",
        "Cấp lại hộ chiếu cho văn phòng Cần Thơ",
    ]
    assert [episode["updated_at"] for episode in rendered] == [
        "2026-08-21T19:31:00+00:00",
        "2026-08-21T19:30:00+00:00",
    ]


def test_system_prompt_a_refusal_is_the_complete_answer() -> None:
    """Wrap-invention recites neighbouring facts after declining (mimo v4 Concern D)."""

    prompt = " ".join(chat_reply._SYSTEM_INSTRUCTION.split())
    assert (
        "When the labeled context does not contain the fact the question asked for, "
        "the complete answer is that the fact is absent. Write that one statement and stop."
    ) in prompt


def test_system_prompt_sha_is_stable_across_calls() -> None:
    assert system_prompt_sha() == system_prompt_sha()
    assert len(system_prompt_sha()) == 64


def test_system_prompt_sha_changes_when_the_prompt_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fingerprint that survives an edit would make two runs look comparable."""

    before = system_prompt_sha()
    monkeypatch.setattr(chat_reply, "_SYSTEM_INSTRUCTION", "a different instruction")
    assert system_prompt_sha() != before


def test_a_broken_response_is_not_reported_as_a_provider_outage() -> None:
    """The memory evaluation triaged three of these as network dropouts."""

    request, context = _grounded_task_context()

    async def uncitable(payload: dict[str, object]) -> dict[str, object]:
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

    reply = MistralChatReply(model="mistral-small-2603", complete=uncitable)
    with pytest.raises(ChatResponseInvalid):
        asyncio.run(_collect(reply, request, context))


def test_a_provider_that_raises_is_still_a_provider_outage() -> None:
    request, context = _grounded_task_context()

    async def down(payload: dict[str, object]) -> dict[str, object]:
        del payload
        raise TimeoutError("connection reset")

    reply = MistralChatReply(model="mistral-small-2603", complete=down)
    with pytest.raises(ChatReplyUnavailable) as caught:
        asyncio.run(_collect(reply, request, context))
    assert not isinstance(caught.value, ChatResponseInvalid)
