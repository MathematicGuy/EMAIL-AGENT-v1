"""Framework-free V2-M1 chat and memory contract tests."""

import json
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime

import pytest

from cowork_agent.domain.chat_contracts import (
    AI_CHAT_FEATURE,
    CHAT_CONTRACTS_VERSION,
    ChatEventType,
    ChatMemoryScope,
    ChatMessageRequest,
    ChatMessageStreamEvent,
    ChatToolChoice,
    ChatTurn,
    DeclarativeProfile,
    DegradedMemorySource,
    EpisodeCitation,
    EpisodeSourceType,
    EpisodeTransition,
    EpisodicMemoryRead,
    MemoryCitationType,
    MemoryContextRequest,
    MemoryContextResponse,
    MemoryNamespace,
    MemoryProvenance,
    MemoryProvenanceSource,
    MemoryReadOptions,
    MemoryType,
    SemanticMemoryRead,
    TaskEpisode,
    stream_event_from_dict,
)
from cowork_agent.domain.target_contracts import ValidationStatus


def _namespace(*, record_id: str | None = "record-1") -> MemoryNamespace:
    return MemoryNamespace(
        scope=ChatMemoryScope(
            tenant_id="tenant-1", user_id="user@example.com", session_id="session-1"
        ),
        memory_type=MemoryType.EPISODIC,
        record_id=record_id,
        source_id="gmail-message-1",
    )


def _episode() -> TaskEpisode:
    return TaskEpisode(
        episode_id="episode-1",
        record_id="record-1",
        tenant_id="tenant-1",
        user_id="user@example.com",
        run_id="run-1",
        chat_session_id="session-1",
        chat_turn_id="turn-1",
        source_tool="@Email",
        gmail_message_id="gmail-message-1",
        gmail_url="https://mail.google.com/mail/u/0/#all/gmail-message-1",
        task_title="Submit the report",
        minimal_request_paraphrase="The sender requests the quarterly report.",
        action_plan=("Open the approved template.", "Submit the report."),
        rag_citations=(
            EpisodeCitation(
                document_id="doc-1",
                document_title="Quarterly Report Procedure",
                section="Submission",
                source_url="https://hub.example.com/docs/quarterly-report",
            ),
        ),
        missing_information=("The report deadline is not stated.",),
        validation_status=ValidationStatus.SYSTEM_GENERATED,
        retrieval_eligible=False,
        source_type=EpisodeSourceType.SYSTEM_GENERATED_CHAT_TOOL_OUTPUT,
        created_at=datetime(2026, 8, 10, 9, 0, tzinfo=UTC),
        updated_at=datetime(2026, 8, 10, 9, 0, tzinfo=UTC),
        pipeline_version="2",
        model_id=None,
        prompt_version=None,
        confidence=0.87,
    )


def _context_request() -> MemoryContextRequest:
    return MemoryContextRequest(
        session_id="session-1",
        scope=ChatMemoryScope(
            tenant_id="tenant-1", user_id="user@example.com", session_id="session-1"
        ),
        reads=MemoryReadOptions(
            short_term=True,
            long_term=True,
            episodic=EpisodicMemoryRead(enabled=True, retrieval_eligible_only=True, max_items=3),
            semantic=SemanticMemoryRead(enabled=True),
        ),
    )


def _context_response() -> MemoryContextResponse:
    return MemoryContextResponse(
        turns=(_chat_turn(),),
        profile=_profile(),
        episodes=(_episode(),),
        semantic_context={"citation_ids": ["doc-1#section-2"]},
        degraded=True,
        degraded_sources=(DegradedMemorySource.LONG_TERM,),
    )


def _chat_turn() -> ChatTurn:
    return ChatTurn(
        turn_id="turn-1",
        session_id="session-1",
        user_message="What should I do next?",
        assistant_message="I can help you prioritize the request.",
        created_at=datetime(2026, 8, 10, 9, 0, tzinfo=UTC),
    )


def _profile() -> DeclarativeProfile:
    return DeclarativeProfile(
        profile_id="profile-1",
        tenant_id="tenant-1",
        user_id="user@example.com",
        language="en",
        timezone="Asia/Bangkok",
        assistant_persona="Helpful coworker",
        response_tone="direct",
        created_at=datetime(2026, 8, 10, 9, 0, tzinfo=UTC),
        updated_at=datetime(2026, 8, 10, 9, 0, tzinfo=UTC),
    )


def _transition() -> EpisodeTransition:
    return EpisodeTransition(
        episode_id="episode-1",
        namespace=_namespace(),
        from_status=ValidationStatus.SYSTEM_GENERATED,
        to_status=ValidationStatus.USER_APPROVED,
        retrieval_eligible=True,
        transitioned_at=datetime(2026, 8, 10, 9, 1, tzinfo=UTC),
    )


def _provenance() -> MemoryProvenance:
    return MemoryProvenance(
        source_type=MemoryProvenanceSource.SYSTEM_GENERATED_CHAT_TOOL_OUTPUT,
        source_id="gmail-message-1",
        source_tool=ChatToolChoice.EMAIL,
        run_id="run-1",
        chat_turn_id="turn-1",
        pipeline_version="2",
        model_id=None,
        prompt_version=None,
    )


@pytest.mark.parametrize(
    "build",
    [
        _namespace,
        _episode,
        _context_request,
        _context_response,
        _chat_turn,
        _profile,
        _transition,
        _provenance,
    ],
    ids=[
        "namespace",
        "episode",
        "context_request",
        "context_response",
        "chat_turn",
        "profile",
        "transition",
        "provenance",
    ],
)
def test_contracts_round_trip_through_json(build) -> None:
    instance = build()
    payload = instance.to_dict()

    assert type(instance).from_dict(payload) == instance
    assert type(instance).from_dict(json.loads(json.dumps(payload))) == instance


def test_chat_message_request_round_trips_and_uses_typed_tool_choices() -> None:
    request = ChatMessageRequest(
        session_id="session-1",
        user_message="Please use @Email for unread requests.",
        tool_choices=(ChatToolChoice.EMAIL,),
        idempotency_key="idem-1",
    )

    assert ChatMessageRequest.from_dict(json.loads(json.dumps(request.to_dict()))) == request
    assert request.to_dict()["tool_choices"] == ["@Email"]


@pytest.mark.parametrize(
    "event",
    [
        ChatMessageStreamEvent.delta(
            event_id="event-1", session_id="session-1", turn_id="turn-1", text="Hello"
        ),
        ChatMessageStreamEvent.tool_call(
            event_id="event-2",
            session_id="session-1",
            turn_id="turn-1",
            name=ChatToolChoice.EMAIL,
            call_id="call-1",
        ),
        ChatMessageStreamEvent.tool_output(
            event_id="event-3",
            session_id="session-1",
            turn_id="turn-1",
            action_plan={"task_id": "task-1"},
        ),
        ChatMessageStreamEvent.memory_citation(
            event_id="event-4",
            session_id="session-1",
            turn_id="turn-1",
            memory_type=MemoryCitationType.SEMANTIC,
            source_id="doc-1",
        ),
        ChatMessageStreamEvent.completed(
            event_id="event-5", session_id="session-1", turn_id="turn-1"
        ),
        ChatMessageStreamEvent.error(
            event_id="event-6",
            session_id="session-1",
            turn_id="turn-1",
            code="memory_degraded",
            safe_message="Some optional context was unavailable.",
        ),
    ],
    ids=["delta", "tool_call", "tool_output", "memory_citation", "completed", "error"],
)
def test_typed_stream_event_variants_round_trip(event: ChatMessageStreamEvent) -> None:
    payload = event.to_dict()

    assert stream_event_from_dict(json.loads(json.dumps(payload))) == event


def test_stream_event_rejects_payload_for_the_wrong_variant() -> None:
    with pytest.raises(ValueError, match="delta"):
        ChatMessageStreamEvent(
            event_id="event-1",
            session_id="session-1",
            turn_id="turn-1",
            event_type=ChatEventType.DELTA,
            text=None,
        )


def test_namespace_constructs_a_stable_logical_key() -> None:
    namespace = _namespace()

    assert namespace.feature == AI_CHAT_FEATURE
    assert (
        namespace.logical_key()
        == "tenant-1/user@example.com/session-1/ai_chat/episodic/record-1"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {
            "scope": {
                "tenant_id": "",
                "user_id": "user@example.com",
                "session_id": "session-1",
                "feature": "ai_chat",
            }
        },
        {
            "scope": {
                "tenant_id": "tenant-1",
                "user_id": "   ",
                "session_id": "session-1",
                "feature": "ai_chat",
            }
        },
        {
            "scope": {
                "tenant_id": "tenant-1",
                "user_id": "user@example.com",
                "session_id": "",
                "feature": "ai_chat",
            }
        },
        {
            "scope": {
                "tenant_id": "tenant-1",
                "user_id": "user@example.com",
                "session_id": "session-1",
                "feature": "email_action_plan",
            }
        },
    ],
    ids=["missing_tenant", "missing_user", "missing_session", "wrong_feature"],
)
def test_namespace_fails_closed_for_missing_or_inconsistent_scope(changes: dict[str, str]) -> None:
    payload = _namespace().to_dict()
    payload.update(changes)

    with pytest.raises(ValueError):
        MemoryNamespace.from_dict(payload)


def test_namespace_refuses_to_construct_a_logical_key_without_a_record_id() -> None:
    with pytest.raises(ValueError, match="record_id"):
        _namespace(record_id=None).logical_key()


def test_context_request_requires_its_session_to_match_the_namespace() -> None:
    payload = _context_request().to_dict()
    payload["session_id"] = "other-session"

    with pytest.raises(ValueError, match="session_id"):
        MemoryContextRequest.from_dict(payload)


def test_context_request_uses_a_memory_type_free_chat_scope() -> None:
    payload = _context_request().to_dict()

    assert set(payload["scope"]) == {"tenant_id", "user_id", "session_id", "feature"}


def test_context_response_requires_degraded_sources_to_match_degraded_flag() -> None:
    with pytest.raises(ValueError, match="degraded"):
        MemoryContextResponse(
            turns=(),
            profile=None,
            episodes=(),
            semantic_context=None,
            degraded=False,
            degraded_sources=(DegradedMemorySource.EPISODIC,),
        )


def test_context_response_returns_typed_working_turns_and_profile() -> None:
    response = _context_response()

    assert response.turns == (_chat_turn(),)
    assert response.profile == _profile()


def test_context_response_defensively_freezes_nested_semantic_context() -> None:
    source = {"citation_ids": ["doc-1#section-2"]}
    response = MemoryContextResponse(
        turns=(),
        profile=None,
        episodes=(),
        semantic_context=source,
        degraded=False,
        degraded_sources=(),
    )

    source["citation_ids"].append("doc-2#section-1")

    assert response.to_dict()["semantic_context"] == {
        "citation_ids": ["doc-1#section-2"]
    }


def test_context_response_rejects_non_json_semantic_context_values() -> None:
    with pytest.raises(TypeError, match="JSON-compatible"):
        MemoryContextResponse(
            turns=(),
            profile=None,
            episodes=(),
            semantic_context={"citation_ids": {"doc-1#section-2"}},
            degraded=False,
            degraded_sources=(),
        )


@pytest.mark.parametrize(
    ("status", "eligible"),
    [
        (ValidationStatus.SYSTEM_GENERATED, True),
        (ValidationStatus.USER_APPROVED, False),
        (ValidationStatus.COMPLETED, False),
        (ValidationStatus.REJECTED, True),
    ],
)
def test_task_episode_rejects_an_inconsistent_retrieval_eligibility(
    status: ValidationStatus, eligible: bool
) -> None:
    payload = _episode().to_dict()
    payload["validation_status"] = status.value
    payload["retrieval_eligible"] = eligible

    with pytest.raises(ValueError, match="retrieval_eligible"):
        TaskEpisode.from_dict(payload)


def test_task_episode_has_no_raw_email_body_field() -> None:
    forbidden = {"body", "raw_email", "normalized_body", "attachment_content"}

    assert not forbidden.intersection(field.name for field in fields(TaskEpisode))


@pytest.mark.parametrize("build", [_episode, _profile, _provenance, _chat_turn])
def test_durable_contracts_have_no_raw_email_shaped_fields(build) -> None:
    forbidden = {"body", "raw_email", "normalized_body", "attachment_content", "tool_payload"}

    assert not forbidden.intersection(field.name for field in fields(type(build())))


def test_task_episode_requires_the_explicit_email_tool_source() -> None:
    payload = _episode().to_dict()
    payload.pop("source_tool")

    with pytest.raises(KeyError):
        TaskEpisode.from_dict(payload)


def test_namespace_rejects_slash_in_logical_key_components() -> None:
    with pytest.raises(ValueError, match="must not contain"):
        ChatMemoryScope(tenant_id="tenant/one", user_id="user@example.com", session_id="session-1")


def test_namespace_requires_an_explicit_source_id_key_even_when_null() -> None:
    payload = _namespace().to_dict()
    payload.pop("source_id")

    with pytest.raises(KeyError):
        MemoryNamespace.from_dict(payload)

    payload = _namespace().to_dict()
    payload["source_id"] = None
    assert MemoryNamespace.from_dict(payload).source_id is None


@pytest.mark.parametrize(
    "payload",
    [
        {**_episode().to_dict(), "raw_email_body": "must never persist"},
        {
            **_episode().to_dict(),
            "rag_citations": [
                {**_episode().to_dict()["rag_citations"][0], "normalized_body": "forbidden"}
            ],
        },
    ],
    ids=["top_level", "nested"],
)
def test_episode_from_dict_rejects_raw_email_shaped_keys_recursively(
    payload: dict[str, object]
) -> None:
    with pytest.raises(ValueError, match="raw email"):
        TaskEpisode.from_dict(payload)


def test_contracts_are_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        _namespace().scope = ChatMemoryScope(  # type: ignore[misc]
            tenant_id="other-tenant", user_id="user@example.com", session_id="session-1"
        )


def test_contract_version_is_declared() -> None:
    assert CHAT_CONTRACTS_VERSION == "1.0.0"
