import asyncio
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cowork_agent.domain.chat_contracts import (
    ChatActivity,
    ChatActivityCode,
    ChatActivityOutcome,
    ChatActivityStatus,
    ChatMemoryScope,
    ChatTurn,
    ChatTurnStatus,
    DeclarativeProfile,
    EpisodeSourceType,
    MemoryNamespace,
    MemoryType,
    TaskEpisode,
)
from cowork_agent.domain.target_contracts import ValidationStatus
from cowork_agent.persistence.repositories.local import InMemoryChatHistoryRepository
from cowork_agent.persistence.repositories.sqlite_chat import SQLiteChatRepository


def test_local_chat_history_persists_idempotent_lifecycle_and_latest_turn() -> None:
    async def scenario() -> None:
        repository = InMemoryChatHistoryRepository()
        scope = ChatMemoryScope(tenant_id="local", user_id="owner", session_id="session-1")
        pending = ChatTurn(
            turn_id="turn-1",
            session_id=scope.session_id,
            user_message="Keep this prompt.",
            assistant_message=None,
            created_at=datetime(2026, 8, 17, 9, tzinfo=UTC),
            status=ChatTurnStatus.GENERATING,
            idempotency_key="submission-1",
        )

        begun = await repository.begin_turn(
            scope, pending, idempotency_key="submission-1", title="Keep this prompt"
        )
        replay = await repository.begin_turn(
            scope,
            replace(pending, turn_id="turn-replay"),
            idempotency_key="submission-1",
            title="Do not replace title",
        )
        with pytest.raises(ValueError, match="idempotency key"):
            await repository.begin_turn(
                scope,
                replace(pending, turn_id="turn-conflict", user_message="Different prompt"),
                idempotency_key="submission-1",
                title="Conflict",
            )
        completed = await repository.update_turn(
            scope,
            replace(
                begun,
                assistant_message="Done.",
                status=ChatTurnStatus.COMPLETED,
                activities=(
                    ChatActivity(
                        code=ChatActivityCode.UNDERSTANDING_REQUEST,
                        status=ChatActivityStatus.COMPLETED,
                        outcome=ChatActivityOutcome.SUCCESS,
                        started_at=begun.created_at,
                        completed_at=begun.created_at + timedelta(seconds=1),
                    ),
                ),
                completed_at=begun.created_at + timedelta(seconds=1),
            ),
            title="Generated title",
        )
        second = await repository.begin_turn(
            scope,
            replace(
                pending,
                turn_id="turn-2",
                user_message="Second prompt",
                created_at=pending.created_at + timedelta(seconds=1),
                idempotency_key="submission-2",
            ),
            idempotency_key="submission-2",
            title="Ignored because a title exists",
        )

        assert replay == begun
        assert await repository.list_turns(scope) == (completed, second)
        assert await repository.latest_turns_for((scope,)) == {scope.session_id: second}
        assert await repository.titles_for((scope,)) == {scope.session_id: "Generated title"}

    asyncio.run(scenario())


def test_local_chat_history_is_scoped_to_the_session_owner() -> None:
    async def scenario() -> None:
        repository = InMemoryChatHistoryRepository()
        owner = ChatMemoryScope(tenant_id="local", user_id="owner", session_id="session-1")
        stranger = ChatMemoryScope(tenant_id="local", user_id="stranger", session_id="session-1")
        pending = ChatTurn(
            turn_id="turn-1",
            session_id=owner.session_id,
            user_message="Private prompt",
            assistant_message=None,
            created_at=datetime(2026, 8, 17, 9, tzinfo=UTC),
            status=ChatTurnStatus.GENERATING,
            idempotency_key="submission-1",
        )
        await repository.begin_turn(
            owner, pending, idempotency_key="submission-1", title="Private prompt"
        )

        assert await repository.list_turns(stranger) == ()
        assert await repository.latest_turns_for((stranger,)) == {}
        assert await repository.titles_for((stranger,)) == {}

    asyncio.run(scenario())


def test_sqlite_chat_repository_survives_restart_with_history_and_memory(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "chat.db"
        repository = SQLiteChatRepository(path)
        await repository.initialize()
        scope = await repository.create(user_id="owner")
        now = datetime(2026, 8, 18, 9, tzinfo=UTC)
        pending = ChatTurn(
            turn_id="turn-1",
            session_id=scope.session_id,
            user_message="Prepare the report.",
            assistant_message=None,
            created_at=now,
            status=ChatTurnStatus.GENERATING,
            idempotency_key="submission-1",
        )
        begun = await repository.begin_turn(
            scope, pending, idempotency_key="submission-1", title="Prepare report"
        )
        completed = await repository.update_turn(
            scope,
            replace(
                begun,
                assistant_message="Done.",
                status=ChatTurnStatus.COMPLETED,
                activities=(
                    ChatActivity(
                        code=ChatActivityCode.UNDERSTANDING_REQUEST,
                        status=ChatActivityStatus.COMPLETED,
                        outcome=ChatActivityOutcome.SUCCESS,
                        started_at=now,
                        completed_at=now + timedelta(seconds=1),
                    ),
                ),
                completed_at=now + timedelta(seconds=1),
            ),
        )
        profile_namespace = MemoryNamespace(
            scope=scope,
            memory_type=MemoryType.LONG_TERM,
            record_id=None,
            source_id=None,
        )
        profile = DeclarativeProfile(
            profile_id="profile-1",
            user_id="owner",
            language="vi",
            timezone="Asia/Ho_Chi_Minh",
            assistant_persona=None,
            response_tone=None,
            created_at=now,
            updated_at=now,
        )
        await repository.write_profile(profile_namespace, profile)
        episode_namespace = MemoryNamespace(
            scope=scope,
            memory_type=MemoryType.EPISODIC,
            record_id="record-1",
            source_id="turn-1",
        )
        episode = TaskEpisode(
            episode_id="episode-1",
            record_id="record-1",
            user_id="owner",
            chat_session_id=scope.session_id,
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
        await repository.write_task_episode(episode_namespace, episode, expires_at=None)

        restarted = SQLiteChatRepository(path)
        await restarted.initialize()

        assert await restarted.require(scope.session_id, user_id="owner") == scope
        assert await restarted.list_turns(scope) == (completed,)
        assert await restarted.read_profile(profile_namespace) == profile
        assert (
            await restarted.read_task_episode(episode_namespace, episode_id="episode-1") == episode
        )
        assert await restarted.list_episodes(episode_namespace) == (episode,)
        assert (
            await restarted.list_turns(
                ChatMemoryScope(tenant_id="local", user_id="stranger", session_id=scope.session_id)
            )
            == ()
        )

    asyncio.run(scenario())


def test_sqlite_chat_repository_loads_turn_payload_from_before_activity_fields(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        path = tmp_path / "legacy-chat.db"
        repository = SQLiteChatRepository(path)
        await repository.initialize()
        scope = await repository.create(user_id="owner")
        turn = ChatTurn(
            turn_id="turn-legacy",
            session_id=scope.session_id,
            user_message="Legacy prompt",
            assistant_message="Legacy reply",
            created_at=datetime(2026, 8, 18, 9, tzinfo=UTC),
            idempotency_key="legacy-submission",
        )
        await repository.begin_turn(
            scope,
            turn,
            idempotency_key="legacy-submission",
            title="Legacy prompt",
        )

        with sqlite3.connect(path) as database:
            row = database.execute(
                "SELECT payload FROM chat_turns WHERE turn_id = ?", (turn.turn_id,)
            ).fetchone()
            assert row is not None
            payload = json.loads(str(row[0]))
            payload.pop("activities")
            payload.pop("completed_at")
            database.execute(
                "UPDATE chat_turns SET payload = ? WHERE turn_id = ?",
                (json.dumps(payload), turn.turn_id),
            )

        restarted = SQLiteChatRepository(path)
        await restarted.initialize()

        stored = await restarted.list_turns(scope)
        assert stored[0].activities == ()
        assert stored[0].completed_at is None

    asyncio.run(scenario())
