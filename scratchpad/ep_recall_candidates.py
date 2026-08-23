"""Does a candidate ep_recall_01 wording still retrieve both CCCD episodes?

Adding words to the question is not free: the SQLite ranker scores
matched_terms / total_terms, so every term the episodes do NOT contain lowers
every episode's score, and EPISODIC_RETRIEVAL_MIN_SCORE=0.6 can drop them all.
This prints, per candidate, the stripped search terms, each episode's score,
and what retrieval actually returns.

No model, no network.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    EpisodeSourceType,
    EpisodeTransition,
    EpisodicMemoryQuery,
    MemoryNamespace,
    MemoryType,
    TaskEpisode,
)
from cowork_agent.domain.target_contracts import ValidationStatus
from cowork_agent.features.ai_chat.retrieval_policy import (
    EPISODIC_RETRIEVAL_MAX_ITEMS,
    EPISODIC_RETRIEVAL_MIN_SCORE,
    episodic_search_text,
)
from cowork_agent.persistence.repositories.sqlite_chat import SQLiteChatRepository

NOW = datetime(2026, 8, 21, 19, 30, tzinfo=UTC)

# Verbatim from what the product wrote for the four v3 episodic seed requests.
SEEDS = [
    (
        "Gia hạn CCCD cho văn phòng Đà Nẵng",
        "Gia hạn CCCD cho văn phòng Đà Nẵng",
        (
            "Xác định danh sách nhân sự cần gia hạn CCCD tại văn phòng Đà Nẵng.",
            "Lập lịch hẹn và chuẩn bị hồ sơ cần thiết.",
            "Thực hiện thủ tục gia hạn và cập nhật trạng thái.",
        ),
    ),
    (
        "Cấp lại hộ chiếu văn phòng Cần Thơ",
        "Cấp lại hộ chiếu văn phòng Cần Thơ",
        (
            "Xác nhận thông tin hồ sơ cấp lại hộ chiếu cho văn phòng Cần Thơ.",
            "Thiết lập lịch nộp hồ sơ vào ngày 5 tháng 9.",
            "Giao anh Phạm Quốc Huy theo dõi tiến độ.",
        ),
    ),
    (
        "Dời ngày nộp hồ sơ hộ chiếu Cần Thơ",
        "Dời ngày nộp hồ sơ hộ chiếu Cần Thơ",
        ("Cập nhật lịch nộp hồ sơ hộ chiếu của văn phòng Cần Thơ thành ngày 12 tháng 9.",),
    ),
    (
        "Gia hạn CCCD cho văn phòng Hải Phòng",
        "Gia hạn CCCD cho văn phòng Hải Phòng",
        (
            "Xác định danh sách nhân sự cần gia hạn CCCD tại văn phòng Hải Phòng.",
            "Lập lịch hẹn và chuẩn bị hồ sơ cần thiết.",
            "Thực hiện thủ tục gia hạn và cập nhật trạng thái.",
        ),
    ),
]

CANDIDATES = {
    "A shipped": "Tác vụ trước về gia hạn CCCD là cho văn phòng nào?",
    "B newest-inline": "Tác vụ trước về gia hạn CCCD mới nhất là cho văn phòng nào?",
    "C newest-framed": (
        "Trong các tác vụ trước về gia hạn CCCD, tác vụ mới nhất là cho văn phòng nào?"
    ),
    "D newest-plain-word": "Tác vụ trước gần đây nhất về gia hạn CCCD là cho văn phòng nào?",
}


def _namespace(record_id: str, turn_id: str) -> MemoryNamespace:
    return MemoryNamespace(
        scope=ChatMemoryScope(tenant_id="t", user_id="u@example.com", session_id="s"),
        memory_type=MemoryType.EPISODIC,
        record_id=record_id,
        source_id=turn_id,
    )


def _episode(index: int, title: str, para: str, plan: tuple[str, ...]) -> TaskEpisode:
    stamp = NOW + timedelta(seconds=30 * index)
    return TaskEpisode(
        episode_id=f"ep-{index}",
        record_id=f"rec-{index}",
        user_id="u@example.com",
        chat_session_id="s",
        chat_turn_id=f"turn-{index}",
        creation_reason="explicit_user_task_request",
        task_title=title,
        minimal_request_paraphrase=para,
        action_plan=plan,
        rag_citations=(),
        missing_information=(),
        validation_status=ValidationStatus.SYSTEM_GENERATED,
        retrieval_eligible=False,
        source_type=EpisodeSourceType.SYSTEM_GENERATED_CHAT_TASK,
        created_at=stamp,
        updated_at=stamp,
        pipeline_version="2",
        prompt_version="1",
        model_id="m",
        confidence=0.9,
    )


def _score(terms: str, title: str, para: str, plan: tuple[str, ...]) -> float:
    """Mirror the SQLite ranker: fraction of query terms present in the haystack."""
    haystack = " ".join((title, para, *plan)).casefold()
    words = [w for w in terms.split() if w]
    if not words:
        return 0.0
    return sum(1 for w in words if w.casefold() in haystack) / len(words)


async def main() -> None:
    repo = SQLiteChatRepository(Path(tempfile.mkdtemp()) / "loop.db")
    await repo.initialize()
    for index, (title, para, plan) in enumerate(SEEDS, start=1):
        await repo.write_task_episode(
            _namespace(f"rec-{index}", f"turn-{index}"),
            _episode(index, title, para, plan),
            expires_at=NOW + timedelta(days=30),
        )
        await repo.transition_task_episode(
            EpisodeTransition(
                namespace=_namespace(f"rec-{index}", f"turn-{index}"),
                episode_id=f"ep-{index}",
                from_status=ValidationStatus.SYSTEM_GENERATED,
                to_status=ValidationStatus.USER_APPROVED,
                retrieval_eligible=True,
                transitioned_at=NOW + timedelta(seconds=30 * 5),
            ),
        )

    print(f"min_score={EPISODIC_RETRIEVAL_MIN_SCORE}  max_items={EPISODIC_RETRIEVAL_MAX_ITEMS}\n")
    for label, question in CANDIDATES.items():
        terms = episodic_search_text(question)
        print(f"=== {label}")
        print(f"    q     : {question}")
        print(f"    terms : {terms!r}  ({len(terms.split())} terms)")
        for index, (title, para, plan) in enumerate(SEEDS, start=1):
            mark = "  " if _score(terms, title, para, plan) >= EPISODIC_RETRIEVAL_MIN_SCORE else " x"
            print(f"     {mark} ep-{index} score={_score(terms, title, para, plan):.3f}  {title}")
        retrieved = await repo.read_episodes(
            _namespace("rec-1", "turn-1"),
            EpisodicMemoryQuery(
                query=terms,
                max_items=EPISODIC_RETRIEVAL_MAX_ITEMS,
                min_score=EPISODIC_RETRIEVAL_MIN_SCORE,
                timeout_ms=2000,
            ),
        )
        got = [f"{e.episode_id}:{e.task_title}" for e in (retrieved.value if hasattr(retrieved, "value") else retrieved)]
        print(f"    RETURNED {len(got)}: {got}\n")


asyncio.run(main())
