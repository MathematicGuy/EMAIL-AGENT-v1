from dataclasses import fields
from datetime import UTC, datetime

from cowork_agent.domain.chat_contracts import (
    ChatMessageRequest,
    ChatTurn,
    DeclarativeProfile,
    EpisodeCitation,
    EpisodeSourceType,
    MemoryContextResponse,
    TaskEpisode,
)
from cowork_agent.domain.target_contracts import ValidationStatus
from cowork_agent.features.ai_chat.generation_context import (
    ContextSource,
    GenerationContext,
    assemble_generation_context,
)

NOW = datetime(2026, 8, 10, tzinfo=UTC)


def _request() -> ChatMessageRequest:
    return ChatMessageRequest("session-1", "Current instruction", (), "idem-1")


def _episode() -> TaskEpisode:
    return TaskEpisode(
        episode_id="episode-1", record_id="record-1", tenant_id="tenant-1",
        user_id="user@example.com", run_id="run-1", chat_session_id="session-1",
        chat_turn_id="turn-1", source_tool="@Email", gmail_message_id="message-1",
        gmail_url="https://mail.google.com/message-1", task_title="Submit report",
        minimal_request_paraphrase="Submit report", action_plan=("Submit it",),
        rag_citations=(EpisodeCitation("doc-1", "Procedure", None, "https://docs.example.com"),),
        missing_information=(), validation_status=ValidationStatus.USER_APPROVED,
        retrieval_eligible=True,
        source_type=EpisodeSourceType.SYSTEM_GENERATED_CHAT_TOOL_OUTPUT,
        created_at=NOW, updated_at=NOW, pipeline_version="2", model_id=None,
        prompt_version=None, confidence=None,
    )


def _context(*, semantic_context: object = None) -> MemoryContextResponse:
    return MemoryContextResponse(
        turns=(ChatTurn("turn-1", "session-1", "Earlier", "Earlier reply", NOW),),
        profile=DeclarativeProfile(
            "profile-1",
            "tenant-1",
            "user@example.com",
            "en",
            None,
            "Concise",
            None,
            NOW,
            NOW,
        ),
        episodes=(_episode(),), semantic_context=semantic_context, degraded=False,
        degraded_sources=(),
    )


def test_assembler_labels_all_sources_and_declares_exact_conflict_precedence() -> None:
    context = assemble_generation_context(
        _request(),
        _context(semantic_context={
            "source_label": "current_company_evidence", "retrieval_status": "success",
            "chunks": ({"chunk_id": "chunk-1"},),
            "citations": ({"document_id": "doc-1", "source_url": "https://docs.example.com"},),
            "scores": (),
        }),
    )

    assert context.current_instruction.label is ContextSource.CURRENT_INSTRUCTION
    assert context.active_session_turns is not None
    assert context.active_session_turns.label is ContextSource.ACTIVE_SESSION_TURNS
    assert context.stored_preference is not None
    assert context.stored_preference.label is ContextSource.STORED_PREFERENCE
    assert context.advisory_episodes is not None
    assert context.advisory_episodes.advisory is True
    assert context.current_company_evidence is not None
    assert context.current_company_evidence.label is ContextSource.CURRENT_COMPANY_EVIDENCE
    assert context.current_company_evidence.value.citations == (
        {"document_id": "doc-1", "source_url": "https://docs.example.com"},
    )
    assert context.conflict_precedence == (
        ContextSource.CURRENT_INSTRUCTION,
        ContextSource.CURRENT_COMPANY_EVIDENCE,
        ContextSource.STORED_PREFERENCE,
        ContextSource.ADVISORY_EPISODE,
    )


def test_assembler_omits_missing_or_malformed_sources_without_inventing_content() -> None:
    context = assemble_generation_context(_request(), _context(semantic_context={"chunks": ()}))

    assert context.current_company_evidence is None
    assert context.active_session_turns is not None
    assert context.stored_preference is not None
    assert context.advisory_episodes is not None
    assert "email_body" not in {field.name for field in fields(GenerationContext)}
    assert "raw_email" not in {field.name for field in fields(GenerationContext)}
