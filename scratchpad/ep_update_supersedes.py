"""Does a correct supersedes link actually retire the ancestor at read time?

`ep_update_01` passed once and failed once with the link written every time, so
the question is whether the read path is sound and only the model's choice of
ordinal varies. This seeds the four v3 episodes into the real SQLite store, sets
the link the model is supposed to set (revision -> passport create), and reads
through the real MemoryGateway.

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
    MemoryNamespace,
    MemoryType,
    TaskEpisode,
)
from cowork_agent.domain.target_contracts import ValidationStatus
from cowork_agent.features.ai_chat.memory_gateway import MemoryGateway
from cowork_agent.features.ai_chat.retrieval_policy import select_memory_reads
from cowork_agent.domain.chat_contracts import ChatMessageRequest, MemoryContextRequest
from cowork_agent.persistence.repositories.sqlite_chat import SQLiteChatRepository

sys.path.insert(0, "scratchpad")
from ep_update_retrieval import SEEDS  # noqa: E402

NOW = datetime(2026, 8, 21, 19, 30, tzinfo=UTC)
QUESTION = "Ngày nộp hồ sơ hộ chiếu trên tác vụ trước là ngày nào?"
SCOPE = ChatMemoryScope(tenant_id="t", user_id="u@example.com", session_id="s")


def _namespace(index: int, memory_type: MemoryType = MemoryType.EPISODIC) -> MemoryNamespace:
    return MemoryNamespace(
        scope=SCOPE,
        memory_type=memory_type,
        record_id=f"rec-{index}",
        source_id=f"turn-{index}",
    )


def _episode(index: int, seed: tuple[str, str, tuple[str, ...]], supersedes: str | None) -> TaskEpisode:
    title, para, plan = seed
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
        supersedes=supersedes,
    )


class _Buffer:
    def read(self, namespace: object) -> tuple[()]:
        del namespace
        return ()


async def _run(link: str | None) -> list[str]:
    repo = SQLiteChatRepository(Path(tempfile.mkdtemp()) / "sup.db")
    await repo.initialize()
    for index, seed in enumerate(SEEDS, start=1):
        # Seed 3 is the revision: "Dời ngày nộp hồ sơ hộ chiếu Cần Thơ".
        await repo.write_task_episode(
            _namespace(index),
            _episode(index, seed, link if index == 3 else None),
            expires_at=NOW + timedelta(days=30),
        )
        await repo.transition_task_episode(
            EpisodeTransition(
                namespace=_namespace(index),
                episode_id=f"ep-{index}",
                from_status=ValidationStatus.SYSTEM_GENERATED,
                to_status=ValidationStatus.USER_APPROVED,
                retrieval_eligible=True,
                transitioned_at=NOW + timedelta(seconds=150),
            ),
        )
    gateway = MemoryGateway(scope=SCOPE, session_buffer=_Buffer(), episodic_memory=repo)
    message = ChatMessageRequest("s", QUESTION, "idem-1")
    request = MemoryContextRequest(
        session_id=SCOPE.session_id, scope=SCOPE, reads=select_memory_reads(message)
    )
    response = await gateway.read_context(request)
    return [f"{e.episode_id}:{e.task_title}" for e in response.episodes]


async def main() -> None:
    reads = select_memory_reads(ChatMessageRequest("s", QUESTION, "idem-1"))
    print(f"episodic read enabled: {getattr(reads.episodic, 'query', None)!r}\n")
    for label, link in (
        ("no link (pre-fix behaviour)", None),
        ("link ep-3 -> ep-2 (correct)", "ep-2"),
        ("link ep-3 -> ep-1 (wrong ordinal)", "ep-1"),
    ):
        print(f"{label:36} -> {await _run(link)}")


asyncio.run(main())


async def advisory_at_write_turn() -> None:
    """What the model can name when it writes the revision.

    A wrong ordinal is only half the story: if retrieval does not show the
    passport episode at the write turn, no ordinal exists that would be right.
    """

    repo = SQLiteChatRepository(Path(tempfile.mkdtemp()) / "write.db")
    await repo.initialize()
    for index, seed in enumerate(SEEDS[:2], start=1):
        await repo.write_task_episode(
            _namespace(index), _episode(index, seed, None), expires_at=NOW + timedelta(days=30)
        )
        await repo.transition_task_episode(
            EpisodeTransition(
                namespace=_namespace(index),
                episode_id=f"ep-{index}",
                from_status=ValidationStatus.SYSTEM_GENERATED,
                to_status=ValidationStatus.USER_APPROVED,
                retrieval_eligible=True,
                transitioned_at=NOW + timedelta(seconds=150),
            ),
        )
    write_message = "Tạo một tác vụ dời ngày nộp hồ sơ hộ chiếu Cần Thơ sang ngày 12 tháng 9."
    message = ChatMessageRequest("s", write_message, "idem-w")
    reads = select_memory_reads(message)
    gateway = MemoryGateway(scope=SCOPE, session_buffer=_Buffer(), episodic_memory=repo)
    response = await gateway.read_context(
        MemoryContextRequest(session_id=SCOPE.session_id, scope=SCOPE, reads=reads)
    )
    print(f"\nwrite-turn terms  : {getattr(reads.episodic, 'query', None)!r}")
    for position, episode in enumerate(response.episodes):
        print(f"  index {position} -> {episode.episode_id}:{episode.task_title}")


asyncio.run(advisory_at_write_turn())
