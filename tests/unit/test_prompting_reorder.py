"""Tests for U-Shaped Chunk Re-ordering (Lost-in-the-Middle mitigation)."""

from cowork_agent.prompting import reorder_u_shaped


def test_reorder_u_shaped_empty_and_small() -> None:
    assert reorder_u_shaped(()) == ()
    assert reorder_u_shaped([]) == ()
    assert reorder_u_shaped(("A",)) == ("A",)
    assert reorder_u_shaped(["A", "B"]) == ("A", "B")


def test_reorder_u_shaped_three_elements() -> None:
    # 1 -> left(0), 2 -> right(2), 3 -> left(1) => [1, 3, 2]
    items = [1, 2, 3]
    assert reorder_u_shaped(items) == (1, 3, 2)


def test_reorder_u_shaped_four_elements() -> None:
    # 1 -> left(0), 2 -> right(3), 3 -> left(1), 4 -> right(2) => [1, 3, 4, 2]
    items = ["top1", "top2", "top3", "top4"]
    assert reorder_u_shaped(items) == ("top1", "top3", "top4", "top2")


def test_reorder_u_shaped_odd_elements() -> None:
    # [1, 2, 3, 4, 5] -> [1, 3, 5, 4, 2]
    items = [1, 2, 3, 4, 5]
    assert reorder_u_shaped(items) == (1, 3, 5, 4, 2)


def test_reorder_u_shaped_even_elements() -> None:
    # [1, 2, 3, 4, 5, 6] -> [1, 3, 5, 6, 4, 2]
    items = [1, 2, 3, 4, 5, 6]
    assert reorder_u_shaped(items) == (1, 3, 5, 6, 4, 2)


def test_reorder_u_shaped_immutability_and_preservation() -> None:
    data = [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}]
    reordered = reorder_u_shaped(data)
    assert [d["id"] for d in reordered] == [1, 3, 4, 2]
    # Ensure original list is unchanged
    assert [d["id"] for d in data] == [1, 2, 3, 4]


def test_email_generation_prompt_actual_chunks_are_u_shaped() -> None:
    import json
    from datetime import UTC, datetime

    from cowork_agent.domain.target_contracts import (
        Actionability,
        BodyFormat,
        EmailRouteDecision,
        EphemeralEmailEnvelope,
        FetchStatus,
        ReasonCode,
        RetrievalStatus,
        Route,
        SemanticChunk,
        SemanticRetrievalResponse,
    )
    from cowork_agent.features.email_action_plan.correlation import TaskCandidate
    from cowork_agent.features.email_action_plan.routing import RouteResolution
    from cowork_agent.integrations.llm.providers.gemini import _build_generation_prompt
    from cowork_agent.prompting import RETRIEVED_CONTEXT_TAG

    chunks = tuple(
        SemanticChunk(
            chunk_id=f"chunk-{i}",
            document_id=f"doc-{i}",
            document_title=f"Doc {i}",
            section=f"Section {i}",
            text=f"Text content for chunk {i}",
            source_url=f"https://example.com/doc/{i}",
            document_version=None,
            relevance_score=1.0 - (i * 0.1),
            rerank_score=None,
            page_start=1,
            page_end=1,
            document_date=None,
        )
        for i in range(1, 6)  # chunk-1, chunk-2, chunk-3, chunk-4, chunk-5
    )
    retrieval = SemanticRetrievalResponse(
        query_id="query-1",
        chunks=chunks,
        retrieval_status=RetrievalStatus.SUCCESS,
        latency_ms=10,
    )
    envelope = EphemeralEmailEnvelope(
        run_id="run-1",
        user_id="user-1",
        gmail_message_id="msg-1",
        gmail_thread_id="th-1",
        gmail_url="https://mail.google.com/1",
        sender_name="Sender",
        sender_email="sender@example.com",
        recipients=("me@example.com",),
        subject="Test Subject",
        received_at=datetime(2026, 8, 1, tzinfo=UTC),
        labels=(),
        normalized_body="Hello",
        body_format=BodyFormat.TEXT,
        attachments_present=False,
        fetch_status=FetchStatus.COMPLETE,
    )
    candidate = TaskCandidate(
        candidate_key="cand-1",
        gmail_thread_id="th-1",
        incident_key=None,
        source_message_ids=("msg-1",),
        decisions=(
            (
                "msg-1",
                EmailRouteDecision(
                    actionability=Actionability.ACTION_REQUIRED,
                    route=Route.RETRIEVE_RAG,
                    candidate_action_item="Do task",
                    email_is_sufficient=False,
                    knowledge_gaps=("gap",),
                    retrieval_query="query",
                    expected_document_types=(),
                    reason_codes=(ReasonCode.POLICY_REQUIRED,),
                    confidence=0.9,
                ),
            ),
        ),
    )
    resolution = RouteResolution(
        route=Route.RETRIEVE_RAG,
        reason_codes=(ReasonCode.POLICY_REQUIRED,),
        forced_by_guard=False,
        mode="full",
    )

    prompt = _build_generation_prompt(
        user_timezone="Asia/Ho_Chi_Minh",
        current_time=datetime(2026, 8, 19, tzinfo=UTC),
        envelopes=(envelope,),
        candidate=candidate,
        resolution=resolution,
        retrieval=retrieval,
    )

    # Extract JSON inside <retrieved_context>...</retrieved_context>
    start_tag = f"<{RETRIEVED_CONTEXT_TAG}>"
    end_tag = f"</{RETRIEVED_CONTEXT_TAG}>"
    start_idx = prompt.index(start_tag) + len(start_tag)
    end_idx = prompt.index(end_tag)
    retrieved_json = json.loads(prompt[start_idx:end_idx].strip())

    chunk_ids = [c["citationId"] for c in retrieved_json["retrievedContext"]]
    # Original: [1, 2, 3, 4, 5] -> U-shaped: [1, 3, 5, 4, 2]
    assert chunk_ids == ["chunk-1", "chunk-3", "chunk-5", "chunk-4", "chunk-2"]


def test_chat_reply_actual_chunks_are_u_shaped() -> None:
    from cowork_agent.domain.chat_contracts import (
        ChatMessageRequest,
        MemoryContextResponse,
    )
    from cowork_agent.features.ai_chat.generation_context import assemble_generation_context
    from cowork_agent.integrations.llm.chat_reply import _company_evidence

    raw_chunks = tuple(
        {"chunk_id": f"chat-chunk-{i}", "text": f"Chat text {i}"}
        for i in range(1, 6)
    )

    semantic_context = {
        "source_label": "current_company_evidence",
        "retrieval_status": "success",
        "chunks": raw_chunks,
        "citations": tuple(
            {
                "chunk_id": f"chat-chunk-{i}",
                "document_id": f"doc-{i}",
                "document_title": f"Doc {i}",
                "section": f"Section {i}",
                "source_url": f"https://example.com/chat/{i}",
            }
            for i in range(1, 6)
        ),
        "scores": tuple(
            {"chunk_id": f"chat-chunk-{i}", "relevance_score": 0.9 - (i * 0.1)}
            for i in range(1, 6)
        ),
    }

    request = ChatMessageRequest("session-1", "Help with policy", "idem-1")
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

    result = _company_evidence(context)
    assert result is not None
    chunk_ids = [c["chunk_id"] for c in result["chunks"]]  # type: ignore[index]
    # Original: [1, 2, 3, 4, 5] -> U-shaped: [1, 3, 5, 4, 2]
    assert chunk_ids == [
        "chat-chunk-1",
        "chat-chunk-3",
        "chat-chunk-5",
        "chat-chunk-4",
        "chat-chunk-2",
    ]


