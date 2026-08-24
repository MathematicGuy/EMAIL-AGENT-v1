"""Framework-free V2-M1 chat and memory contract tests."""

import json
from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime

import pytest

import cowork_agent.domain.chat_contracts as chat_contracts
from cowork_agent.domain._chat_contracts_memory import ChatRagEvidence
from cowork_agent.domain.chat_contracts import (
    AI_CHAT_FEATURE,
    CHAT_CONTRACTS_VERSION,
    MAX_CHAT_MESSAGE_LENGTH,
    MAX_CHAT_RAG_EVIDENCE_ITEMS,
    MAX_CHAT_SUMMARY_LENGTH,
    MAX_EPISODE_CITATION_DOCUMENT_ID_LENGTH,
    MAX_EPISODE_CITATION_DOCUMENT_TITLE_LENGTH,
    MAX_EPISODE_CITATION_SECTION_LENGTH,
    MAX_EPISODE_CITATION_SOURCE_URL_LENGTH,
    MAX_EPISODIC_RETRIEVAL_ITEMS,
    MAX_RETRIEVAL_QUERY_LENGTH,
    MAX_RETRIEVAL_TIMEOUT_MS,
    MAX_SEMANTIC_RETRIEVAL_ITEMS,
    MAX_TASK_ACTION_PLAN_ITEM_LENGTH,
    MAX_TASK_ACTION_PLAN_ITEMS,
    MAX_TASK_MISSING_INFORMATION_ITEM_LENGTH,
    MAX_TASK_MISSING_INFORMATION_ITEMS,
    MAX_TASK_RAG_CITATIONS,
    MAX_TASK_REQUEST_PARAPHRASE_LENGTH,
    MAX_TASK_TITLE_LENGTH,
    ChatActivity,
    ChatActivityCode,
    ChatActivityDetail,
    ChatActivityOutcome,
    ChatActivityStatus,
    ChatEventType,
    ChatMemoryScope,
    ChatMessageRequest,
    ChatMessageStreamEvent,
    ChatSummaryEpisode,
    ChatTurn,
    DeclarativeProfile,
    DegradedMemorySource,
    EpisodeCitation,
    EpisodeSourceType,
    EpisodeTransition,
    EpisodicMemoryQuery,
    EpisodicMemoryRead,
    MailScanSummary,
    MemoryCitationType,
    MemoryContextRequest,
    MemoryContextResponse,
    MemoryNamespace,
    MemoryProvenance,
    MemoryProvenanceSource,
    MemoryReadOptions,
    MemoryType,
    SemanticMemoryQuery,
    SemanticMemoryRead,
    TaskEpisode,
    stream_event_from_dict,
    transition_activity_snapshot,
)
from cowork_agent.domain.target_contracts import ValidationStatus

ACTIVITY_NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


def _namespace(*, record_id: str | None = "record-1") -> MemoryNamespace:
    return MemoryNamespace(
        scope=ChatMemoryScope(
            user_id="user@example.com", session_id="session-1"
        ),
        memory_type=MemoryType.EPISODIC,
        record_id=record_id,
        source_id="gmail-message-1",
    )


def _episode() -> TaskEpisode:
    return TaskEpisode(
        episode_id="episode-1",
        record_id="record-1",
        user_id="user@example.com",
        chat_session_id="session-1",
        chat_turn_id="turn-1",
        creation_reason="explicit_user_task_request",
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
        source_type=EpisodeSourceType.SYSTEM_GENERATED_CHAT_TASK,
        created_at=datetime(2026, 8, 10, 9, 0, tzinfo=UTC),
        updated_at=datetime(2026, 8, 10, 9, 0, tzinfo=UTC),
        pipeline_version="2",
        model_id=None,
        prompt_version=None,
        confidence=0.87,
    )


def test_task_episode_round_trips_a_supersedes_link_and_rejects_a_self_reference() -> None:
    """Concern D: the link is what lets retrieval retire a corrected episode."""
    revision = replace(_episode(), episode_id="episode-2", supersedes="episode-1")

    assert revision.to_dict()["supersedes"] == "episode-1"
    assert TaskEpisode.from_dict(revision.to_dict()) == revision
    assert _episode().supersedes is None

    with pytest.raises(ValueError, match="supersedes must not reference the episode itself"):
        replace(_episode(), supersedes="episode-1")


def _task_proposal_payload() -> dict[str, object]:
    episode = _episode()
    return {
        "episode_id": episode.episode_id,
        "task_title": episode.task_title,
        "minimal_request_paraphrase": episode.minimal_request_paraphrase,
        "action_plan": list(episode.action_plan),
        "missing_information": list(episode.missing_information),
        "rag_citations": [citation.to_dict() for citation in episode.rag_citations],
        "validation_status": episode.validation_status.value,
        "retrieval_eligible": episode.retrieval_eligible,
    }


def _chat_summary_episode() -> ChatSummaryEpisode:
    return ChatSummaryEpisode(
        episode_id="chat-summary-1",
        record_id="record-1",
        user_id="user@example.com",
        chat_session_id="session-1",
        chat_turn_id="turn-1",
        summary="The user asked for help prioritizing the approved procedure.",
        validation_status=ValidationStatus.SYSTEM_GENERATED,
        retrieval_eligible=False,
        source_type=EpisodeSourceType.SYSTEM_GENERATED_CHAT_SUMMARY,
        created_at=datetime(2026, 8, 10, 9, 0, tzinfo=UTC),
        updated_at=datetime(2026, 8, 10, 9, 0, tzinfo=UTC),
        expires_at=None,
        pipeline_version="2",
        model_id="test-model",
        prompt_version="chat-summary-v1",
        confidence=0.87,
    )


def _context_request() -> MemoryContextRequest:
    return MemoryContextRequest(
        session_id="session-1",
        scope=ChatMemoryScope(
            user_id="user@example.com", session_id="session-1"
        ),
        reads=MemoryReadOptions(
            short_term=True,
            long_term=True,
            episodic=EpisodicMemoryQuery(
                query="Find related approved report tasks.",
                max_items=3,
                min_score=0.6,
                timeout_ms=500,
            ),
            semantic=SemanticMemoryQuery(
                query="Find the current report submission procedure.",
                max_items=4,
                min_score=0.7,
                timeout_ms=750,
            ),
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


def test_chat_turn_round_trips_durable_generation_lifecycle() -> None:
    turn = ChatTurn(
        turn_id="turn-pending",
        session_id="session-1",
        user_message="Keep this prompt while the reply is generating.",
        assistant_message=None,
        created_at=datetime(2026, 8, 17, 9, 0, tzinfo=UTC),
        status="generating",
        idempotency_key="submission-1",
    )

    assert ChatTurn.from_dict(json.loads(json.dumps(turn.to_dict()))) == turn
    assert turn.to_dict()["status"] == "generating"
    assert turn.to_dict()["idempotency_key"] == "submission-1"


def test_chat_turn_round_trips_terminal_failure_code() -> None:
    turn = ChatTurn(
        turn_id="turn-failed",
        session_id="session-1",
        user_message="Try a provider-limited request.",
        assistant_message=None,
        created_at=datetime(2026, 8, 17, 9, 1, tzinfo=UTC),
        status="rate_limited",
        idempotency_key="submission-2",
        error_code="provider_rate_limit",
    )

    assert ChatTurn.from_dict(json.loads(json.dumps(turn.to_dict()))) == turn


def _rag_evidence():
    return ChatRagEvidence(
        source="company_knowledge",
        retrieval_status="success",
        chunk_id="chunk-27",
        document_id="citizen-id-law-2023",
        document_title="Luật Căn cước 2023",
        section="Điều 27",
        source_url="https://example.gov.vn/luat-can-cuoc-2023",
        relevance_score=0.842,
        rerank_score=0.913,
        preview="Từ ngày 01/07/2026, người dân có thể làm thủ tục cấp thẻ Căn cước.",
        content=(
            "Từ ngày 01/07/2026, người dân có thể làm thủ tục cấp thẻ Căn cước "
            "tại cơ quan quản lý căn cước theo Điều 27."
        ),
    )


def test_chat_turn_round_trips_bounded_rag_evidence_with_scores_and_content() -> None:
    turn = ChatTurn(
        turn_id="turn-1",
        session_id="session-1",
        user_message="Làm thẻ Căn cước từ ngày 01/07/2026 ở đâu?",
        assistant_message="Bạn có thể làm thủ tục tại cơ quan quản lý căn cước.",
        created_at=datetime(2026, 8, 13, 14, 0, tzinfo=UTC),
        rag_evidence=(_rag_evidence(),),
        retrieval_status="success",
    )

    assert ChatTurn.from_dict(json.loads(json.dumps(turn.to_dict()))) == turn
    assert turn.to_dict()["rag_evidence"] == [
        {
            "source": "company_knowledge",
            "retrieval_status": "success",
            "chunk_id": "chunk-27",
            "document_id": "citizen-id-law-2023",
            "document_title": "Luật Căn cước 2023",
            "section": "Điều 27",
            "source_url": "https://example.gov.vn/luat-can-cuoc-2023",
            "relevance_score": 0.842,
            "rerank_score": 0.913,
            "preview": "Từ ngày 01/07/2026, người dân có thể làm thủ tục cấp thẻ Căn cước.",
            "content": (
                "Từ ngày 01/07/2026, người dân có thể làm thủ tục cấp thẻ Căn cước "
                "tại cơ quan quản lý căn cước theo Điều 27."
            ),
        }
    ]


def test_chat_turn_round_trips_safe_mail_scan_aggregates() -> None:
    turn = ChatTurn(
        turn_id="mail-turn-1",
        session_id="session-1",
        user_message="@mail",
        assistant_message="Đã quét xong: đã quét 10 email và tạo 5 action item.",
        created_at=datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
        mail_scan=MailScanSummary(
            status="succeeded",
            emails_matched=201,
            emails_processed=10,
            emails_to_process=10,
            action_items_count=5,
        ),
    )

    assert ChatTurn.from_dict(json.loads(json.dumps(turn.to_dict()))) == turn
    assert turn.to_dict()["mail_scan"] == {
        "status": "succeeded",
        "emails_matched": 201,
        "emails_processed": 10,
        "emails_to_process": 10,
        "action_items_count": 5,
    }


def test_completed_stream_event_round_trips_rag_evidence_with_retrieval_status() -> None:
    event = ChatMessageStreamEvent.completed(
        event_id="event-4",
        session_id="session-1",
        turn_id="turn-1",
        rag_evidence=(_rag_evidence(),),
        retrieval_status="success",
    )

    assert stream_event_from_dict(json.loads(json.dumps(event.to_dict()))) == event
    assert event.to_dict()["retrieval_status"] == "success"


@pytest.mark.parametrize(
    "event",
    [
        ChatMessageStreamEvent.delta(
            event_id="event-1", session_id="session-1", turn_id="turn-1", text="Hello"
        ),
        ChatMessageStreamEvent.memory_citation(
            event_id="event-2",
            session_id="session-1",
            turn_id="turn-1",
            memory_type=MemoryCitationType.SEMANTIC,
            source_id="doc-1",
        ),
        ChatMessageStreamEvent.task_proposal(
            event_id="event-3",
            session_id="session-1",
            turn_id="turn-1",
            proposal=_task_proposal_payload(),
        ),
        ChatMessageStreamEvent.error(
            event_id="event-5",
            session_id="session-1",
            turn_id="turn-1",
            code="memory_degraded",
            safe_message="Some optional context was unavailable.",
        ),
    ],
)
def test_non_completed_stream_events_reject_rag_evidence(event: ChatMessageStreamEvent) -> None:
    with pytest.raises(ValueError, match="completed"):
        replace(event, rag_evidence=(_rag_evidence(),), retrieval_status="success")


def test_rag_evidence_rejects_scores_outside_the_unit_interval() -> None:
    with pytest.raises(ValueError, match="relevance_score"):
        replace(_rag_evidence(), relevance_score=1.001)


def test_chat_turn_rejects_more_rag_evidence_than_retrieval_can_return() -> None:
    with pytest.raises(ValueError, match="rag_evidence"):
        replace(
            _chat_turn(),
            rag_evidence=(_rag_evidence(),) * (MAX_CHAT_RAG_EVIDENCE_ITEMS + 1),
            retrieval_status="success",
        )


def test_chat_turn_accepts_a_whole_section_widened_evidence_list() -> None:
    turn = replace(
        _chat_turn(),
        rag_evidence=(_rag_evidence(),) * MAX_CHAT_RAG_EVIDENCE_ITEMS,
        retrieval_status="success",
    )

    assert len(turn.rag_evidence) == MAX_CHAT_RAG_EVIDENCE_ITEMS


def test_chat_stream_event_accepts_max_rag_evidence() -> None:
    event = ChatMessageStreamEvent.completed(
        event_id="e1",
        session_id="s1",
        turn_id="t1",
        rag_evidence=(_rag_evidence(),) * MAX_CHAT_RAG_EVIDENCE_ITEMS,
        retrieval_status="success",
    )
    assert len(event.rag_evidence) == MAX_CHAT_RAG_EVIDENCE_ITEMS


def test_chat_stream_event_rejects_exceeded_rag_evidence() -> None:
    with pytest.raises(ValueError, match="rag_evidence"):
        ChatMessageStreamEvent.completed(
            event_id="e1",
            session_id="s1",
            turn_id="t1",
            rag_evidence=(_rag_evidence(),) * (MAX_CHAT_RAG_EVIDENCE_ITEMS + 1),
            retrieval_status="success",
        )


def _profile() -> DeclarativeProfile:
    return DeclarativeProfile(
        profile_id="profile-1",
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
        source_type=MemoryProvenanceSource.SYSTEM_GENERATED_CHAT_TASK,
        source_id="episode-1",
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


def test_chat_message_request_round_trips_without_a_tool_choice_contract() -> None:
    request = ChatMessageRequest(
        session_id="session-1",
        user_message="Help me make an action plan.",
        idempotency_key="idem-1",
    )

    assert ChatMessageRequest.from_dict(json.loads(json.dumps(request.to_dict()))) == request
    assert "tool_choices" not in request.to_dict()
    assert not hasattr(chat_contracts, "ChatToolChoice")
    assert "ChatToolChoice" not in chat_contracts.__all__


def test_chat_message_request_accepts_the_exact_message_length_limit() -> None:
    request = ChatMessageRequest(
        session_id="session-1",
        user_message="x" * MAX_CHAT_MESSAGE_LENGTH,
        idempotency_key="idem-1",
    )

    assert request.user_message == "x" * MAX_CHAT_MESSAGE_LENGTH


def test_chat_message_request_rejects_a_message_above_the_contract_limit() -> None:
    with pytest.raises(ValueError, match="user_message"):
        ChatMessageRequest(
            session_id="session-1",
            user_message="x" * (MAX_CHAT_MESSAGE_LENGTH + 1),
            idempotency_key="idem-1",
        )


def test_chat_message_request_rejects_an_oversized_idempotency_key() -> None:
    with pytest.raises(ValueError, match="idempotency_key"):
        ChatMessageRequest(
            session_id="session-1",
            user_message="Help me plan.",
            idempotency_key="x" * 129,
        )


def test_chat_message_request_from_dict_rejects_retired_tool_choices() -> None:
    payload = {**ChatMessageRequest("session-1", "Help me plan.", "idem-1").to_dict()}
    payload["tool_choices"] = ["@Email"]

    with pytest.raises(ValueError, match="unexpected field"):
        ChatMessageRequest.from_dict(payload)


def test_enabled_retrieval_requests_are_query_scoped_and_round_trip() -> None:
    reads = MemoryReadOptions(
        short_term=True,
        long_term=True,
        episodic=EpisodicMemoryQuery(
            query="Find related approved report tasks.",
            max_items=3,
            min_score=0.6,
            timeout_ms=500,
        ),
        semantic=SemanticMemoryQuery(
            query="Find the current report submission procedure.",
            max_items=4,
            min_score=0.7,
            timeout_ms=750,
        ),
    )

    payload = json.loads(json.dumps(reads.to_dict()))

    assert reads.episodic.enabled is True
    assert reads.semantic.enabled is True
    assert payload["episodic"]["retrieval_eligible_only"] is True
    assert payload["semantic"]["condition"] == "chat_intent_requires_enterprise_context"
    assert MemoryReadOptions.from_dict(payload) == reads


def test_disabled_retrieval_requests_keep_the_legacy_shape_and_round_trip() -> None:
    reads = MemoryReadOptions(
        short_term=False,
        long_term=False,
        episodic=EpisodicMemoryRead(enabled=False, retrieval_eligible_only=True, max_items=3),
        semantic=SemanticMemoryRead(enabled=False),
    )

    assert MemoryReadOptions.from_dict(reads.to_dict()) == reads


@pytest.mark.parametrize(
    ("build", "match"),
    [
        (
            lambda: EpisodicMemoryQuery(query=" ", max_items=1, min_score=0.0, timeout_ms=1),
            "query",
        ),
        (
            lambda: SemanticMemoryQuery(
                query="x" * (MAX_RETRIEVAL_QUERY_LENGTH + 1),
                max_items=1,
                min_score=0.0,
                timeout_ms=1,
            ),
            "query",
        ),
        (
            lambda: EpisodicMemoryQuery(
                query="approved work", max_items=True, min_score=0.0, timeout_ms=1
            ),
            "max_items",
        ),
        (
            lambda: SemanticMemoryQuery(
                query="company policy",
                max_items=MAX_SEMANTIC_RETRIEVAL_ITEMS + 1,
                min_score=0.0,
                timeout_ms=1,
            ),
            "max_items",
        ),
        (
            lambda: EpisodicMemoryQuery(
                query="approved work",
                max_items=MAX_EPISODIC_RETRIEVAL_ITEMS + 1,
                min_score=0.0,
                timeout_ms=1,
            ),
            "max_items",
        ),
        (
            lambda: SemanticMemoryQuery(
                query="company policy", max_items=1, min_score=True, timeout_ms=1
            ),
            "min_score",
        ),
        (
            lambda: EpisodicMemoryQuery(
                query="approved work", max_items=1, min_score=-0.1, timeout_ms=1
            ),
            "min_score",
        ),
        (
            lambda: SemanticMemoryQuery(
                query="company policy", max_items=1, min_score=1.1, timeout_ms=1
            ),
            "min_score",
        ),
        (
            lambda: EpisodicMemoryQuery(
                query="approved work", max_items=1, min_score=0.0, timeout_ms=False
            ),
            "timeout_ms",
        ),
        (
            lambda: SemanticMemoryQuery(
                query="company policy",
                max_items=1,
                min_score=0.0,
                timeout_ms=MAX_RETRIEVAL_TIMEOUT_MS + 1,
            ),
            "timeout_ms",
        ),
    ],
)
def test_enabled_retrieval_requests_reject_untrusted_or_unbounded_values(
    build: object, match: str
) -> None:
    with pytest.raises((TypeError, ValueError), match=match):
        build()  # type: ignore[operator]


@pytest.mark.parametrize(
    "payload",
    [
        {
            "enabled": True,
            "query": "approved work",
            "retrieval_eligible_only": False,
            "max_items": 1,
            "min_score": 0.0,
            "timeout_ms": 1,
        },
        {
            "enabled": True,
            "query": "company policy",
            "condition": "all_context",
            "max_items": 1,
            "min_score": 0.0,
            "timeout_ms": 1,
        },
    ],
)
def test_enabled_retrieval_deserialization_rejects_non_contract_fixed_filters(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        if "retrieval_eligible_only" in payload:
            MemoryReadOptions.from_dict(
                {
                    "short_term": False,
                    "long_term": False,
                    "episodic": payload,
                    "semantic": SemanticMemoryRead(enabled=False).to_dict(),
                }
            )
        else:
            MemoryReadOptions.from_dict(
                {
                    "short_term": False,
                    "long_term": False,
                    "episodic": EpisodicMemoryRead(
                        enabled=False, retrieval_eligible_only=True, max_items=1
                    ).to_dict(),
                    "semantic": payload,
                }
            )


@pytest.mark.parametrize(
    "event",
    [
        ChatMessageStreamEvent.delta(
            event_id="event-1", session_id="session-1", turn_id="turn-1", text="Hello"
        ),
        ChatMessageStreamEvent.memory_citation(
            event_id="event-2",
            session_id="session-1",
            turn_id="turn-1",
            memory_type=MemoryCitationType.SEMANTIC,
            source_id="doc-1",
        ),
        ChatMessageStreamEvent.task_proposal(
            event_id="event-3",
            session_id="session-1",
            turn_id="turn-1",
            proposal=_task_proposal_payload(),
        ),
        ChatMessageStreamEvent.completed(
            event_id="event-4", session_id="session-1", turn_id="turn-1"
        ),
        ChatMessageStreamEvent.error(
            event_id="event-5",
            session_id="session-1",
            turn_id="turn-1",
            code="memory_degraded",
            safe_message="Some optional context was unavailable.",
        ),
    ],
    ids=["delta", "memory_citation", "task_proposal", "completed", "error"],
)
def test_typed_stream_event_variants_round_trip(event: ChatMessageStreamEvent) -> None:
    payload = event.to_dict()

    assert stream_event_from_dict(json.loads(json.dumps(payload))) == event


@pytest.mark.parametrize(
    "change",
    [
        {"raw_email": "forbidden"},
        {"tool_payload": {"name": "forbidden"}},
        {"extra": "not in the frontend contract"},
        {"task_title": "x" * (MAX_TASK_TITLE_LENGTH + 1)},
        {"minimal_request_paraphrase": 1},
        {"action_plan": "not-a-sequence"},
        {"action_plan": ["x"] * (MAX_TASK_ACTION_PLAN_ITEMS + 1)},
        {"missing_information": [1]},
        {"rag_citations": [{"document_id": "incomplete"}]},
        {"validation_status": "unknown"},
        {"retrieval_eligible": 1},
    ],
)
def test_task_proposal_event_rejects_untrusted_or_unbounded_payloads(
    change: dict[str, object],
) -> None:
    proposal = {**_task_proposal_payload(), **change}

    with pytest.raises((KeyError, TypeError, ValueError)):
        ChatMessageStreamEvent.task_proposal(
            event_id="event-1",
            session_id="session-1",
            turn_id="turn-1",
            proposal=proposal,
        )


def test_task_proposal_event_requires_the_exact_frontend_safe_shape() -> None:
    proposal = _task_proposal_payload()
    proposal.pop("missing_information")

    with pytest.raises((KeyError, ValueError)):
        ChatMessageStreamEvent.task_proposal(
            event_id="event-1",
            session_id="session-1",
            turn_id="turn-1",
            proposal=proposal,
        )


@pytest.mark.parametrize(
    ("status", "retrieval_eligible"),
    [
        (ValidationStatus.SYSTEM_GENERATED.value, True),
        (ValidationStatus.USER_APPROVED.value, False),
        (ValidationStatus.COMPLETED.value, False),
        (ValidationStatus.REJECTED.value, True),
    ],
)
def test_task_proposal_event_rejects_inconsistent_retrieval_eligibility(
    status: str, retrieval_eligible: bool
) -> None:
    proposal = {
        **_task_proposal_payload(),
        "validation_status": status,
        "retrieval_eligible": retrieval_eligible,
    }

    with pytest.raises(ValueError, match="retrieval_eligible"):
        ChatMessageStreamEvent.task_proposal(
            event_id="event-1",
            session_id="session-1",
            turn_id="turn-1",
            proposal=proposal,
        )


def test_stream_contract_has_no_tool_variants() -> None:
    assert all(not event_type.name.startswith("TOOL_") for event_type in ChatEventType)
    assert not hasattr(ChatMessageStreamEvent, "tool_call")
    assert not hasattr(ChatMessageStreamEvent, "tool_output")


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
        namespace.logical_key() == "user@example.com/session-1/ai_chat/episodic/record-1"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {
            "scope": {
                "user_id": "   ",
                "session_id": "session-1",
                "feature": "ai_chat",
            }
        },
        {
            "scope": {
                "user_id": "user@example.com",
                "session_id": "",
                "feature": "ai_chat",
            }
        },
        {
            "scope": {
                "user_id": "user@example.com",
                "session_id": "session-1",
                "feature": "email_action_plan",
            }
        },
    ],
    ids=["missing_user", "missing_session", "wrong_feature"],
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

    assert response.to_dict()["semantic_context"] == {"citation_ids": ["doc-1#section-2"]}


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
    forbidden = {
        "body",
        "raw_email",
        "normalized_body",
        "attachment_content",
        "run_id",
        "source_tool",
        "gmail_message_id",
        "gmail_url",
    }

    assert not forbidden.intersection(field.name for field in fields(TaskEpisode))
    assert not forbidden.intersection(_episode().to_dict())


def test_memory_provenance_has_no_email_or_run_ownership_fields() -> None:
    forbidden = {"source_tool", "run_id", "gmail_message_id", "gmail_url"}

    assert not forbidden.intersection(field.name for field in fields(MemoryProvenance))
    assert not forbidden.intersection(_provenance().to_dict())


@pytest.mark.parametrize("removed_field", ["source_tool", "run_id"])
def test_memory_provenance_from_dict_rejects_removed_ownership_fields(
    removed_field: str,
) -> None:
    payload = {**_provenance().to_dict(), removed_field: "legacy-value"}

    with pytest.raises(ValueError, match="unexpected field"):
        MemoryProvenance.from_dict(payload)


def test_chat_summary_episode_round_trips_with_the_bounded_system_contract() -> None:
    episode = _chat_summary_episode()

    assert ChatSummaryEpisode.from_dict(json.loads(json.dumps(episode.to_dict()))) == episode
    assert MAX_CHAT_SUMMARY_LENGTH == 500


@pytest.mark.parametrize(
    "change",
    [
        {"episode_id": ""},
        {"summary": "x" * (MAX_CHAT_SUMMARY_LENGTH + 1)},
        {"validation_status": ValidationStatus.USER_APPROVED.value},
        {"retrieval_eligible": True},
        {"source_type": EpisodeSourceType.SYSTEM_GENERATED_CHAT_TASK.value},
        {"updated_at": "2026-08-10T08:59:00+00:00"},
        {"expires_at": "2026-08-10T09:00:00+00:00"},
        {"confidence": True},
    ],
)
def test_chat_summary_episode_rejects_invalid_lifecycle_or_untrusted_shape(
    change: dict[str, object],
) -> None:
    payload = {**_chat_summary_episode().to_dict(), **change}

    with pytest.raises((TypeError, ValueError)):
        ChatSummaryEpisode.from_dict(payload)


def test_chat_summary_episode_from_dict_recursively_rejects_raw_email_shaped_keys() -> None:
    payload = {**_chat_summary_episode().to_dict(), "metadata": {"raw_email": "forbidden"}}

    with pytest.raises(ValueError, match="raw email"):
        ChatSummaryEpisode.from_dict(payload)


def test_chat_summary_episode_has_no_raw_email_or_transcript_fields() -> None:
    forbidden = {
        "body",
        "raw_email",
        "normalized_body",
        "attachment_content",
        "transcript",
        "tool_payload",
    }

    assert not forbidden.intersection(field.name for field in fields(ChatSummaryEpisode))


@pytest.mark.parametrize("build", [_episode, _profile, _provenance, _chat_turn])
def test_durable_contracts_have_no_raw_email_shaped_fields(build) -> None:
    forbidden = {"body", "raw_email", "normalized_body", "attachment_content", "tool_payload"}

    assert not forbidden.intersection(field.name for field in fields(type(build())))


def test_task_episode_requires_the_explicit_user_task_request_creation_reason() -> None:
    payload = _episode().to_dict()
    payload["creation_reason"] = "implicit_model_extraction"

    with pytest.raises(ValueError, match="creation_reason"):
        TaskEpisode.from_dict(payload)


def test_task_episode_contract_version_and_compact_bounds_are_public() -> None:
    assert CHAT_CONTRACTS_VERSION == "2.0.0"
    assert (
        MAX_TASK_TITLE_LENGTH,
        MAX_TASK_REQUEST_PARAPHRASE_LENGTH,
        MAX_TASK_ACTION_PLAN_ITEMS,
        MAX_TASK_ACTION_PLAN_ITEM_LENGTH,
        MAX_TASK_MISSING_INFORMATION_ITEMS,
        MAX_TASK_MISSING_INFORMATION_ITEM_LENGTH,
        MAX_TASK_RAG_CITATIONS,
    ) == (200, 1_000, 20, 500, 20, 500, 20)
    assert (
        MAX_EPISODE_CITATION_DOCUMENT_ID_LENGTH,
        MAX_EPISODE_CITATION_DOCUMENT_TITLE_LENGTH,
        MAX_EPISODE_CITATION_SECTION_LENGTH,
        MAX_EPISODE_CITATION_SOURCE_URL_LENGTH,
    ) == (256, 300, 300, 2_048)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("task_title", "x" * 201, "task_title"),
        ("minimal_request_paraphrase", "x" * 1_001, "minimal_request_paraphrase"),
        ("action_plan", ("x",) * 21, "action_plan"),
        ("action_plan", ("x" * 501,), "action_plan"),
        ("missing_information", ("x",) * 21, "missing_information"),
        ("missing_information", ("x" * 501,), "missing_information"),
        ("rag_citations", (_episode().rag_citations[0],) * 21, "rag_citations"),
    ],
)
def test_task_episode_direct_construction_enforces_compact_bounds(
    field: str, value: object, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        replace(_episode(), **{field: value})


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("task_title", "x" * 201, "task_title"),
        ("minimal_request_paraphrase", "x" * 1_001, "minimal_request_paraphrase"),
        ("action_plan", ["x"] * 21, "action_plan"),
        ("action_plan", ["x" * 501], "action_plan"),
        ("missing_information", ["x"] * 21, "missing_information"),
        ("missing_information", ["x" * 501], "missing_information"),
        ("rag_citations", [_episode().rag_citations[0].to_dict()] * 21, "rag_citations"),
    ],
)
def test_task_episode_from_dict_enforces_compact_bounds(
    field: str, value: object, match: str
) -> None:
    payload = _episode().to_dict()
    payload[field] = value

    with pytest.raises(ValueError, match=match):
        TaskEpisode.from_dict(payload)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("document_id", "x" * 257, "document_id"),
        ("document_title", "x" * 301, "document_title"),
        ("section", "x" * 301, "section"),
        ("source_url", "x" * 2_049, "source_url"),
    ],
)
def test_episode_citation_enforces_compact_bounds_direct_and_from_dict(
    field: str, value: str, match: str
) -> None:
    citation = _episode().rag_citations[0]

    with pytest.raises(ValueError, match=match):
        replace(citation, **{field: value})

    payload = citation.to_dict()
    payload[field] = value
    with pytest.raises(ValueError, match=match):
        EpisodeCitation.from_dict(payload)


def test_task_episode_defensively_freezes_accepted_sequence_inputs() -> None:
    action_plan = ["Open the approved template."]
    citations = [_episode().rag_citations[0]]
    missing_information = ["The report deadline is not stated."]

    episode = replace(
        _episode(),
        action_plan=action_plan,
        rag_citations=citations,
        missing_information=missing_information,
    )
    action_plan.append("Submit the report.")
    citations.clear()
    missing_information.clear()

    assert episode.action_plan == ("Open the approved template.",)
    assert episode.rag_citations == (_episode().rag_citations[0],)
    assert episode.missing_information == ("The report deadline is not stated.",)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("action_plan", "one step", "sequence"),
        ("action_plan", [1], "action_plan item"),
        ("missing_information", "one note", "sequence"),
        ("missing_information", [1], "missing_information item"),
        ("rag_citations", "one citation", "sequence"),
        ("rag_citations", [{"document_id": "doc-1"}], "EpisodeCitation"),
        ("validation_status", "system_generated", "ValidationStatus"),
        ("source_type", "system_generated_chat_task", "EpisodeSourceType"),
        ("created_at", "2026-08-10T09:00:00+00:00", "created_at"),
        ("updated_at", "2026-08-10T09:00:00+00:00", "updated_at"),
    ],
)
def test_task_episode_direct_construction_rejects_untrusted_types(
    field: str, value: object, match: str
) -> None:
    with pytest.raises((TypeError, ValueError), match=match):
        replace(_episode(), **{field: value})


@pytest.mark.parametrize("nested", [{"raw_email": "copied"}, {"tool_payload": {}}])
def test_task_episode_direct_construction_rejects_nested_raw_or_tool_payload_shapes(
    nested: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="raw email|tool payload"):
        replace(_episode(), rag_citations=[nested])


def test_task_episode_requires_the_system_generated_chat_task_source_type() -> None:
    payload = _episode().to_dict()
    payload["source_type"] = EpisodeSourceType.SYSTEM_GENERATED_CHAT_SUMMARY.value

    with pytest.raises(ValueError, match="source_type"):
        TaskEpisode.from_dict(payload)


@pytest.mark.parametrize(
    "removed_field",
    ["run_id", "source_tool", "gmail_message_id", "gmail_url"],
)
def test_task_episode_from_dict_rejects_removed_email_shaped_fields(removed_field: str) -> None:
    payload = {**_episode().to_dict(), removed_field: "legacy-value"}

    with pytest.raises(ValueError, match="unexpected field"):
        TaskEpisode.from_dict(payload)


def test_namespace_rejects_slash_in_logical_key_components() -> None:
    with pytest.raises(ValueError, match="must not contain"):
        ChatMemoryScope(user_id="user/one", session_id="session-1")


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
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="raw email"):
        TaskEpisode.from_dict(payload)


def test_chat_activity_snapshot_round_trips_through_turn_and_stream_event() -> None:
    started = ChatActivity.pending(ChatActivityCode.UNDERSTANDING_REQUEST).transition(
        ChatActivityStatus.RUNNING, at=ACTIVITY_NOW
    )
    activities = transition_activity_snapshot(
        (started,),
        ChatActivityCode.UNDERSTANDING_REQUEST,
        ChatActivityStatus.COMPLETED,
        at=ACTIVITY_NOW,
        outcome=ChatActivityOutcome.SUCCESS,
        detail=ChatActivityDetail(kind="documents_found", current=3),
    )
    turn = ChatTurn(
        turn_id="turn-activity",
        session_id="session-1",
        user_message="Find the policy",
        assistant_message="Here it is",
        created_at=ACTIVITY_NOW,
        activities=activities,
        completed_at=ACTIVITY_NOW,
    )
    event = ChatMessageStreamEvent.activity(
        event_id="event-activity",
        session_id="session-1",
        turn_id="turn-activity",
        activities=activities,
    )

    assert ChatTurn.from_dict(json.loads(json.dumps(turn.to_dict()))) == turn
    assert stream_event_from_dict(json.loads(json.dumps(event.to_dict()))) == event


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "provider_name", "current": 1},
        {"kind": "documents_found", "current": -1},
        {"kind": "documents_found", "current": 2, "total": 1},
        {"kind": "documents_found", "current": 1, "label": "Hybrid Retriever"},
        {"kind": "documents_found", "current": 1, "body": "raw email"},
    ],
)
def test_chat_activity_detail_rejects_unbounded_or_developer_payloads(
    payload: dict[str, object],
) -> None:
    with pytest.raises((TypeError, ValueError)):
        ChatActivityDetail.from_dict(payload)


def test_chat_activity_enforces_unique_bounded_monotonic_snapshot() -> None:
    pending = ChatActivity.pending(ChatActivityCode.REVIEWING_CONTEXT)
    running = pending.transition(ChatActivityStatus.RUNNING, at=ACTIVITY_NOW)
    completed = running.transition(
        ChatActivityStatus.COMPLETED,
        at=ACTIVITY_NOW,
        outcome=ChatActivityOutcome.DEGRADED,
    )
    assert completed.started_at == ACTIVITY_NOW
    assert completed.completed_at == ACTIVITY_NOW

    with pytest.raises(ValueError, match="invalid activity transition"):
        completed.transition(ChatActivityStatus.RUNNING, at=ACTIVITY_NOW)
    with pytest.raises(ValueError, match="unique"):
        ChatTurn(
            turn_id="turn-duplicate",
            session_id="session-1",
            user_message="Hello",
            assistant_message=None,
            created_at=ACTIVITY_NOW,
            activities=(pending, pending),
        )
    with pytest.raises(ValueError, match="8 items"):
        ChatTurn(
            turn_id="turn-too-many",
            session_id="session-1",
            user_message="Hello",
            assistant_message=None,
            created_at=ACTIVITY_NOW,
            activities=tuple(
                ChatActivity.pending(code)
                for code in (*tuple(ChatActivityCode), ChatActivityCode.UNDERSTANDING_REQUEST)
            ),
        )


def test_contracts_are_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        _namespace().scope = ChatMemoryScope(  # type: ignore[misc]
            user_id="other-user", session_id="session-1"
        )


def test_contract_version_is_declared() -> None:
    assert CHAT_CONTRACTS_VERSION == "2.0.0"
