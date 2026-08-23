from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

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

NOW = datetime(2026, 8, 19, 9, tzinfo=UTC)


def _namespace() -> MemoryNamespace:
    return MemoryNamespace(
        scope=ChatMemoryScope(
            tenant_id="tenant-ep", user_id="ep-user@example.com", session_id="session-ep"
        ),
        memory_type=MemoryType.EPISODIC,
        record_id="rec-ep-1",
        source_id="turn-ep-1",
    )


def _episode() -> TaskEpisode:
    return TaskEpisode(
        episode_id="ep-1",
        record_id="rec-ep-1",
        user_id="ep-user@example.com",
        chat_session_id="session-ep",
        chat_turn_id="turn-ep-1",
        creation_reason="explicit_user_task_request",
        task_title="Renew the CCCD",
        minimal_request_paraphrase="Create a task to renew the CCCD.",
        action_plan=("Collect documents.",),
        rag_citations=(),
        missing_information=(),
        validation_status=ValidationStatus.SYSTEM_GENERATED,
        retrieval_eligible=False,
        source_type=EpisodeSourceType.SYSTEM_GENERATED_CHAT_TASK,
        created_at=NOW,
        updated_at=NOW,
        pipeline_version="1",
        model_id="model-ep",
        prompt_version="prompt-ep",
        confidence=0.9,
    )


def test_approving_a_task_episode_makes_it_retrieval_eligible(tmp_path: Path) -> None:
    # A freshly written episode is retrieval_eligible=false by policy; only the
    # approval makes it readable. If this transition cannot run, no episode ever
    # becomes retrievable and episodic memory is dead in the SQLite deployment.
    async def scenario() -> None:
        repository = SQLiteChatRepository(tmp_path / "chat.db")
        await repository.initialize()
        namespace = _namespace()
        await repository.write_task_episode(namespace, _episode(), expires_at=None)

        approved = await repository.transition_task_episode(
            EpisodeTransition(
                episode_id="ep-1",
                namespace=namespace,
                from_status=ValidationStatus.SYSTEM_GENERATED,
                to_status=ValidationStatus.USER_APPROVED,
                retrieval_eligible=True,
                transitioned_at=NOW + timedelta(minutes=1),
            )
        )

        assert approved is not None
        assert approved.validation_status is ValidationStatus.USER_APPROVED
        assert approved.retrieval_eligible is True

        stored = await repository.read_task_episode(namespace, episode_id="ep-1")
        assert stored is not None
        assert stored.validation_status is ValidationStatus.USER_APPROVED
        assert stored.retrieval_eligible is True

    asyncio.run(scenario())


def test_a_transition_naming_another_episode_id_is_refused(tmp_path: Path) -> None:
    # The stored row is addressed by its identity AND its episode_id. A
    # transition quoting a different episode must not silently approve the row
    # that happens to occupy that identity.
    async def scenario() -> None:
        repository = SQLiteChatRepository(tmp_path / "chat.db")
        await repository.initialize()
        namespace = _namespace()
        await repository.write_task_episode(namespace, _episode(), expires_at=None)

        result = await repository.transition_task_episode(
            EpisodeTransition(
                episode_id="ep-does-not-exist",
                namespace=namespace,
                from_status=ValidationStatus.SYSTEM_GENERATED,
                to_status=ValidationStatus.USER_APPROVED,
                retrieval_eligible=True,
                transitioned_at=NOW + timedelta(minutes=1),
            )
        )

        assert result is None

    asyncio.run(scenario())


def _passport_episode(
    *, episode_id: str, title: str, plan: tuple[str, ...], at: datetime
) -> TaskEpisode:
    return TaskEpisode(
        episode_id=episode_id,
        record_id=f"rec-{episode_id}",
        user_id="ep-user@example.com",
        chat_session_id="session-ep",
        chat_turn_id=f"turn-{episode_id}",
        creation_reason="explicit_user_task_request",
        task_title=title,
        minimal_request_paraphrase=title,
        action_plan=plan,
        rag_citations=(),
        missing_information=(),
        validation_status=ValidationStatus.SYSTEM_GENERATED,
        retrieval_eligible=False,
        source_type=EpisodeSourceType.SYSTEM_GENERATED_CHAT_TASK,
        created_at=at,
        updated_at=at,
        pipeline_version="1",
        model_id="model-ep",
        prompt_version="prompt-ep",
        confidence=0.9,
    )


def test_two_episodes_about_one_task_come_back_with_the_later_one_first(
    tmp_path: Path,
) -> None:
    # The measurement behind the v3 ep_update_01 finding. Two approved episodes
    # about the same passport submission — a create naming 5 September and a
    # reschedule moving it to 12 September — tie on term overlap, so updated_at
    # is what decides, and the store hands back the SUPERSEDING one first.
    #
    # It matters that this is pinned here. The diagnosis was that retrieval
    # prefers the older create; it does not, and the ordering it produces is
    # the only thing the reply layer has to tell the two dates apart.
    async def scenario() -> None:
        repository = SQLiteChatRepository(tmp_path / "chat.db")
        await repository.initialize()

        seeds = (
            (
                "ep-create",
                "Cấp lại hộ chiếu cho văn phòng Cần Thơ",
                ("Nộp hồ sơ theo kế hoạch vào ngày 5 tháng 9.",),
                NOW,
            ),
            (
                "ep-reschedule",
                "Dời ngày nộp hồ sơ hộ chiếu Cần Thơ",
                ("Thực hiện điều chỉnh lịch hẹn nộp hồ sơ sang ngày 12 tháng 9.",),
                NOW + timedelta(minutes=1),
            ),
        )
        for episode_id, title, plan, at in seeds:
            namespace = replace(
                _namespace(), record_id=f"rec-{episode_id}", source_id=f"turn-{episode_id}"
            )
            await repository.write_task_episode(
                namespace,
                _passport_episode(episode_id=episode_id, title=title, plan=plan, at=at),
                expires_at=None,
            )
            await repository.transition_task_episode(
                EpisodeTransition(
                    episode_id=episode_id,
                    namespace=namespace,
                    from_status=ValidationStatus.SYSTEM_GENERATED,
                    to_status=ValidationStatus.USER_APPROVED,
                    retrieval_eligible=True,
                    transitioned_at=at,
                )
            )

        terms = episodic_search_text("Ngày nộp hồ sơ hộ chiếu trên tác vụ trước là ngày nào?")
        retrieved = await repository.read_episodes(
            _namespace(),
            EpisodicMemoryQuery(
                query=terms,
                max_items=EPISODIC_RETRIEVAL_MAX_ITEMS,
                min_score=EPISODIC_RETRIEVAL_MIN_SCORE,
                timeout_ms=2000,
            ),
        )

        assert [episode.episode_id for episode in retrieved] == ["ep-reschedule", "ep-create"]

    asyncio.run(scenario())
