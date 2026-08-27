"""P1 - retrieval cues must fire for the language the product actually answers in.

The assistant is Vietnamese-only: the chat system prompt writes Vietnamese
unconditionally and says `stored_preference.language` never overrides it. But
`_EPISODIC_CUES` and `_SEMANTIC_CUES` held English phrases only, so a Vietnamese
user asking for a previous task or for company policy triggered no retrieval at
all. The reply then looked like amnesia while the store was healthy.

`_TASK_DIRECTIVE_VERBS` already carried Vietnamese (`tạo`, `lập`, `lên`), which
made the gap look smaller than it was: the verbs were there, but the task TARGET
nouns were English apart from "kế hoạch", so "tạo một tác vụ" was rejected too.
Both halves of the episodic path were closed to a Vietnamese user.

Diacritics matter. `_contains_cue` casefolds, and casefold does not strip
accents, so an unaccented cue would never meet the accented text a real user
types. The cues are stored accented and the tests below assert on accented text.
"""

from __future__ import annotations

from cowork_agent.domain.chat_contracts import (
    ChatMessageRequest,
    EpisodicMemoryQuery,
    EpisodicMemoryRead,
    SemanticMemoryQuery,
    SemanticMemoryRead,
)
from cowork_agent.features.ai_chat.retrieval_policy import (
    episodic_search_text,
    is_explicit_task_request,
    select_memory_reads,
)


def _request(user_message: str) -> ChatMessageRequest:
    return ChatMessageRequest(
        session_id="session-vi",
        user_message=user_message,
        idempotency_key="idempotency-vi",
    )


def test_vietnamese_episodic_cues_enable_episodic_retrieval() -> None:
    cases = [
        "Tôi còn tác vụ trước nào về bảng lương chưa xong không?",
        "Cho tôi xem công việc trước đó về bảng lương.",
        "Nhiệm vụ trước của tôi về hợp đồng thuê nhà là gì?",
        "Có việc trước đó nào liên quan đến hồ sơ CCCD không?",
    ]
    for user_message in cases:
        reads = select_memory_reads(_request(user_message))
        assert isinstance(reads.episodic, EpisodicMemoryQuery)
        assert isinstance(reads.semantic, SemanticMemoryRead)


def test_a_vietnamese_cue_with_no_subject_starts_no_search() -> None:
    cases = [
        "Tôi còn tác vụ trước nào chưa xong không?",
        "Nhiệm vụ trước của tôi là gì?",
        "Có việc trước đó nào liên quan không?",
    ]
    for user_message in cases:
        assert episodic_search_text(user_message) == ""
        reads = select_memory_reads(_request(user_message))
        assert isinstance(reads.episodic, EpisodicMemoryRead)


def test_vietnamese_semantic_cues_enable_semantic_retrieval() -> None:
    cases = [
        "Chính sách công ty nói gì về làm thêm giờ?",
        "Chính sách của công ty về nghỉ phép ra sao?",
        "Quy định công ty về công tác phí là gì?",
        "Quy định của công ty nói gì về việc này?",
        "Sổ tay nhân viên có nói về làm thêm giờ không?",
    ]
    for user_message in cases:
        reads = select_memory_reads(_request(user_message))
        assert isinstance(reads.semantic, SemanticMemoryQuery)
        assert isinstance(reads.episodic, EpisodicMemoryRead)


def test_truoc_as_the_preposition_before_is_not_an_episodic_cue() -> None:
    cases = [
        "Soạn danh sách bàn giao công việc trước khi nghỉ phép.",
        "Gửi báo cáo công việc trước cuộc họp.",
    ]
    for user_message in cases:
        reads = select_memory_reads(_request(user_message))
        assert isinstance(reads.episodic, EpisodicMemoryRead)


def test_vietnamese_cues_stay_off_without_a_cue() -> None:
    # The policy is deliberately deterministic and narrow. Adding a language
    # must not turn it into "fire on anything Vietnamese".
    reads = select_memory_reads(_request("Hôm nay trời đẹp quá, bạn khỏe không?"))

    assert isinstance(reads.episodic, EpisodicMemoryRead)
    assert isinstance(reads.semantic, SemanticMemoryRead)


def test_vietnamese_semantic_cue_still_respects_the_company_rag_flag() -> None:
    reads = select_memory_reads(
        _request("Chính sách công ty nói gì về làm thêm giờ?"),
        company_rag_enabled=False,
    )

    assert isinstance(reads.semantic, SemanticMemoryRead)


def test_vietnamese_task_directives_are_explicit_task_requests() -> None:
    cases = [
        "Tạo một tác vụ gia hạn CCCD cho văn phòng Đà Nẵng.",
        "Lập nhiệm vụ kiểm tra hợp đồng.",
        "Tạo công việc theo dõi hồ sơ.",
        "Lên kế hoạch cho buổi họp tuần sau.",
    ]
    for user_message in cases:
        assert is_explicit_task_request(_request(user_message))


def test_vietnamese_negation_still_blocks_task_creation() -> None:
    cases = [
        "Không cần tạo tác vụ nào cả.",
        "Đừng tạo nhiệm vụ cho việc này.",
    ]
    for user_message in cases:
        assert not is_explicit_task_request(_request(user_message))


def test_a_bare_vietnamese_noun_is_not_a_task_directive() -> None:
    # The noun must follow a directive verb. Mentioning work is not asking for a
    # task to be created.
    assert not is_explicit_task_request(_request("Công việc hôm nay của tôi nhiều quá."))


def test_unaccented_text_does_not_match_an_accented_cue() -> None:
    # Recorded as a known limit, not an aspiration. `casefold` does not strip
    # diacritics, so "chinh sach cong ty" cannot meet "chính sách công ty".
    # Anyone tempted to "fix" this by adding unaccented duplicates should first
    # decide whether unaccented input is a real user behaviour worth supporting.
    reads = select_memory_reads(_request("chinh sach cong ty noi gi ve lam them gio?"))

    assert isinstance(reads.semantic, SemanticMemoryRead)


def test_a_task_creation_request_reads_episodic_memory_for_a_revision_ancestor() -> None:
    """Concern D: the writer cannot declare a supersedes link it was never shown.

    Task creation carries no episodic cue - "Tạo một tác vụ dời ngày nộp hồ sơ
    hộ chiếu Cần Thơ sang ngày 12 tháng 9" asks for a new task, not for a past
    one - so the cue gate left the write turn with no advisory episodes at all.
    The model had nothing to point at, and every revision was stored as an
    unrelated third fact.
    """

    reads = select_memory_reads(
        ChatMessageRequest(
            "session-1",
            "Tạo một tác vụ dời ngày nộp hồ sơ hộ chiếu Cần Thơ sang ngày 12 tháng 9.",
            "idem-1",
        )
    )

    assert isinstance(reads.episodic, EpisodicMemoryQuery)
    assert "hộ chiếu" in reads.episodic.query
