import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from cowork_agent.domain.chat_contracts import ChatMemoryScope, ChatTurn, ChatTurnStatus
from cowork_agent.persistence.repositories.local import InMemoryChatHistoryRepository


def test_local_chat_history_persists_idempotent_lifecycle_and_latest_turn() -> None:
    async def scenario() -> None:
        repository = InMemoryChatHistoryRepository()
        scope = ChatMemoryScope(
            tenant_id="local", user_id="owner", session_id="session-1"
        )
        pending = ChatTurn(
            turn_id="turn-1", session_id=scope.session_id,
            user_message="Keep this prompt.", assistant_message=None,
            created_at=datetime(2026, 8, 17, 9, tzinfo=UTC),
            status=ChatTurnStatus.GENERATING, idempotency_key="submission-1",
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
                replace(
                    pending, turn_id="turn-conflict", user_message="Different prompt"
                ),
                idempotency_key="submission-1",
                title="Conflict",
            )
        completed = await repository.update_turn(
            scope,
            replace(begun, assistant_message="Done.", status=ChatTurnStatus.COMPLETED),
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
        assert await repository.latest_turns_for((scope,)) == {
            scope.session_id: second
        }
        assert await repository.titles_for((scope,)) == {
            scope.session_id: "Generated title"
        }

    asyncio.run(scenario())


def test_local_chat_history_is_scoped_to_the_session_owner() -> None:
    async def scenario() -> None:
        repository = InMemoryChatHistoryRepository()
        owner = ChatMemoryScope(
            tenant_id="local", user_id="owner", session_id="session-1"
        )
        stranger = ChatMemoryScope(
            tenant_id="local", user_id="stranger", session_id="session-1"
        )
        pending = ChatTurn(
            turn_id="turn-1", session_id=owner.session_id,
            user_message="Private prompt", assistant_message=None,
            created_at=datetime(2026, 8, 17, 9, tzinfo=UTC),
            status=ChatTurnStatus.GENERATING, idempotency_key="submission-1",
        )
        await repository.begin_turn(
            owner, pending, idempotency_key="submission-1", title="Private prompt"
        )

        assert await repository.list_turns(stranger) == ()
        assert await repository.latest_turns_for((stranger,)) == {}
        assert await repository.titles_for((stranger,)) == {}

    asyncio.run(scenario())
