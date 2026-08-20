"""The episodic search text is the content of the question, not the question.

`select_memory_reads` sent the WHOLE user message as the episodic search text,
and `plainto_tsquery` ANDs every token of it. Question words are never in an
episode and the 'simple' text-search config has no stopword list, so the match
predicate was false for every natural question at every threshold. Episodic
retrieval returned nothing to anyone, ever.

Measured against the real generated `search_vector` of a seeded episode
(`ts_rank_cd(..., 32)`):

    query construction                right episode   an episode sharing
                                                      only filler words
    whole message, ANDed (before)     0.0000 (no @@)  0.0000 (no @@)
    whole message, ORed               0.4444          0.8214  <- ranks wrong
    content words only, ORed (now)    0.8529          0.1667

Plain OR is not the fix. A question is mostly function words, so ORing all of
them ranks by filler overlap and puts the wrong episode first. Removing the
question frame - and the cue phrase, which is in every episodic question by
construction and so says nothing about WHICH episode - is what separates them.
"""

from __future__ import annotations

import pytest

from cowork_agent.domain.chat_contracts import (
    ChatMessageRequest,
    EpisodicMemoryQuery,
    EpisodicMemoryRead,
)
from cowork_agent.features.ai_chat.retrieval_policy import (
    episodic_search_text,
    select_memory_reads,
)

pytestmark = pytest.mark.extended


def _request(user_message: str) -> ChatMessageRequest:
    return ChatMessageRequest(
        session_id="session-1", user_message=user_message, idempotency_key="idem-1"
    )


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Số hồ sơ trên tác vụ trước về gia hạn CCCD là bao nhiêu?", "Số hồ sơ gia hạn CCCD"),
        ("Cho tôi xem công việc trước đó về bảng lương.", "xem bảng lương"),
        # "xong" goes the same way as "mở": an episode's indexed text says
        # what the task is, never what state it is in.
        ("Tác vụ trước về hợp đồng thuê nhà đã xong chưa?", "hợp đồng thuê nhà"),
    ],
)
def test_the_question_frame_and_the_cue_are_not_search_terms(question: str, expected: str) -> None:
    assert episodic_search_text(question) == expected


def test_a_question_that_names_no_episode_has_no_search_text() -> None:
    # "Do I have any previous tasks still open?" - every word is either the cue
    # or the frame. There is nothing to search for, because the question is not
    # asking for a particular episode; it is asking to be shown the open ones.
    assert episodic_search_text("Tôi còn tác vụ trước nào đang mở không?") == ""


def test_a_question_that_names_no_episode_fires_no_episodic_search() -> None:
    # Searching anyway is worse than not searching: with nothing to match on,
    # the only episodes that can score are the ones that happen to share a
    # filler word, so the retrieval returns confidently wrong material.
    #
    # Answering this question properly means ENUMERATING the open episodes
    # rather than searching for one. `MemoryGateway.list_task_episodes` already
    # does that and already backs the frontend task list; routing this intent to
    # it is a contract change and is deliberately not made here.
    reads = select_memory_reads(_request("Tôi còn tác vụ trước nào đang mở không?"))

    assert isinstance(reads.episodic, EpisodicMemoryRead)
    assert reads.episodic.enabled is False


def test_a_question_that_names_its_episode_searches_for_that_episode() -> None:
    reads = select_memory_reads(
        _request("Số hồ sơ trên tác vụ trước về gia hạn CCCD là bao nhiêu?")
    )

    assert isinstance(reads.episodic, EpisodicMemoryQuery)
    assert reads.episodic.query == "Số hồ sơ gia hạn CCCD"


def test_the_semantic_query_is_still_the_whole_message() -> None:
    # Semantic retrieval is embedding similarity over a corpus, not term
    # matching against a tsvector. Stripping words there would remove signal the
    # embedding uses, so this narrowing is deliberately episodic-only.
    reads = select_memory_reads(_request("Chính sách công ty nói gì về làm thêm giờ?"))

    assert reads.semantic.query == "Chính sách công ty nói gì về làm thêm giờ?"  # type: ignore[union-attr]
