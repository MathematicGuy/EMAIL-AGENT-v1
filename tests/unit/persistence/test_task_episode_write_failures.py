import asyncio
from datetime import UTC, datetime

import psycopg
import pytest

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    EpisodeSourceType,
    MemoryNamespace,
    MemoryType,
    TaskEpisode,
)
from cowork_agent.domain.target_contracts import ValidationStatus
from cowork_agent.features.ai_chat.memory_gateway import MemorySourceUnavailableError
from cowork_agent.persistence.repositories.postgres import PostgresTaskEpisodeRepository


class _FailingConnection:
    async def execute(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise psycopg.OperationalError("database unavailable")


class _FailingConnectionContext:
    async def __aenter__(self) -> _FailingConnection:
        return _FailingConnection()

    async def __aexit__(self, *args: object) -> None:
        del args


class _FailingPool:
    def connection(self) -> _FailingConnectionContext:
        return _FailingConnectionContext()


def test_task_episode_write_translates_operational_failure_to_memory_unavailable() -> None:
    repository = PostgresTaskEpisodeRepository(_FailingPool())

    with pytest.raises(MemorySourceUnavailableError, match="task episode write unavailable"):
        asyncio.run(repository.write_task_episode(_namespace(), _episode(), expires_at=None))


def _namespace() -> MemoryNamespace:
    return MemoryNamespace(
        scope=ChatMemoryScope("tenant-1", "user-1", "session-1"),
        memory_type=MemoryType.EPISODIC,
        record_id="record-1",
        source_id="turn-1",
    )


def _episode() -> TaskEpisode:
    now = datetime(2026, 8, 11, tzinfo=UTC)
    return TaskEpisode(
        episode_id="episode-1",
        record_id="record-1",
        tenant_id="tenant-1",
        user_id="user-1",
        chat_session_id="session-1",
        chat_turn_id="turn-1",
        creation_reason="explicit_user_task_request",
        task_title="Prepare report",
        minimal_request_paraphrase="Prepare the report",
        action_plan=("Draft the report",),
        rag_citations=(),
        missing_information=(),
        validation_status=ValidationStatus.SYSTEM_GENERATED,
        retrieval_eligible=False,
        source_type=EpisodeSourceType.SYSTEM_GENERATED_CHAT_TASK,
        created_at=now,
        updated_at=now,
        pipeline_version="v2-m4",
        model_id="configured-model",
        prompt_version="chat-v2",
        confidence=0.8,
    )
