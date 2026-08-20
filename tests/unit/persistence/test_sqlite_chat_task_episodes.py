from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    EpisodeSourceType,
    EpisodeTransition,
    MemoryNamespace,
    MemoryType,
    TaskEpisode,
)
from cowork_agent.domain.target_contracts import ValidationStatus
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
