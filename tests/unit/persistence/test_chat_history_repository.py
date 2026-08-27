import asyncio
from datetime import UTC, datetime

from cowork_agent.domain.chat_contracts import (
    ChatActivity,
    ChatActivityCode,
    ChatActivityOutcome,
    ChatActivityStatus,
    ChatMemoryScope,
    ChatTurn,
    ChatTurnStatus,
)
from cowork_agent.persistence.repositories.chat_history import PostgresChatHistoryRepository


class _Cursor:
    def __init__(
        self,
        row: tuple[object, ...] | None,
        rows: list[tuple[object, ...]] | None = None,
    ) -> None:
        self._row = row
        self._rows = rows or []

    async def fetchone(self) -> tuple[object, ...] | None:
        return self._row

    async def fetchall(self) -> list[tuple[object, ...]]:
        return self._rows


class _Connection:
    def __init__(
        self,
        rows: list[tuple[object, ...]],
        latest_rows: list[tuple[object, ...]],
    ) -> None:
        self._rows = rows
        self._latest_rows = latest_rows

    async def execute(self, sql: str, _params: object = None) -> _Cursor:
        if "RETURNING" in sql:
            return _Cursor(self._rows.pop(0))
        if "DISTINCT ON" in sql:
            return _Cursor(None, self._latest_rows)
        return _Cursor(None)


class _ConnectionContext:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    async def __aenter__(self) -> _Connection:
        return self._connection

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Pool:
    def __init__(
        self,
        rows: list[tuple[object, ...]],
        latest_rows: list[tuple[object, ...]] | None = None,
    ) -> None:
        self._connection = _Connection(rows, latest_rows or [])

    def connection(self) -> _ConnectionContext:
        return _ConnectionContext(self._connection)


def _row(
    *,
    status: str,
    assistant: str | None,
    error_code: str | None,
    activities: list[object] | None = None,
    completed_at: datetime | None = None,
) -> tuple[object, ...]:
    return (
        "turn-1",
        "session-1",
        "Keep this prompt.",
        assistant,
        datetime(2026, 8, 17, 9, tzinfo=UTC),
        [],
        [],
        None,
        None,
        status,
        "submission-1",
        error_code,
        activities or [],
        completed_at,
    )


def test_begin_then_update_turn_returns_the_same_durable_turn_identity() -> None:
    async def scenario() -> None:
        repository = PostgresChatHistoryRepository(  # type: ignore[arg-type]
            _Pool(
                [
                    _row(status="generating", assistant=None, error_code=None),
                    _row(status="completed", assistant="Done.", error_code=None),
                ]
            )
        )
        scope = ChatMemoryScope(tenant_id="workspace-1", user_id="user-1", session_id="session-1")
        pending = ChatTurn(
            turn_id="turn-1",
            session_id="session-1",
            user_message="Keep this prompt.",
            assistant_message=None,
            created_at=datetime(2026, 8, 17, 9, tzinfo=UTC),
            status=ChatTurnStatus.GENERATING,
            idempotency_key="submission-1",
        )

        begun = await repository.begin_turn(
            scope, pending, idempotency_key="submission-1", title="Keep this prompt"
        )
        completed = await repository.update_turn(
            scope,
            ChatTurn(
                turn_id=begun.turn_id,
                session_id=begun.session_id,
                user_message=begun.user_message,
                assistant_message="Done.",
                created_at=begun.created_at,
                status=ChatTurnStatus.COMPLETED,
                idempotency_key=begun.idempotency_key,
            ),
            title="Completed title",
        )

        assert begun.status is ChatTurnStatus.GENERATING
        assert completed.turn_id == begun.turn_id
        assert completed.idempotency_key == begun.idempotency_key
        assert completed.status is ChatTurnStatus.COMPLETED
        assert completed.assistant_message == "Done."

    asyncio.run(scenario())


def test_latest_turns_for_returns_one_latest_lifecycle_per_session() -> None:
    async def scenario() -> None:
        latest = _row(status="failed", assistant=None, error_code="provider_error")
        repository = PostgresChatHistoryRepository(  # type: ignore[arg-type]
            _Pool([], latest_rows=[latest])
        )
        scope = ChatMemoryScope(tenant_id="workspace-1", user_id="user-1", session_id="session-1")

        result = await repository.latest_turns_for((scope,))

        assert result["session-1"].status is ChatTurnStatus.FAILED
        assert result["session-1"].error_code == "provider_error"
        assert result["session-1"].idempotency_key == "submission-1"

    asyncio.run(scenario())


def test_latest_turns_for_restores_durable_activity_and_completion_time() -> None:
    async def scenario() -> None:
        started_at = datetime(2026, 8, 17, 9, tzinfo=UTC)
        completed_at = datetime(2026, 8, 17, 9, 0, 4, tzinfo=UTC)
        activity = ChatActivity(
            code=ChatActivityCode.UNDERSTANDING_REQUEST,
            status=ChatActivityStatus.COMPLETED,
            outcome=ChatActivityOutcome.SUCCESS,
            started_at=started_at,
            completed_at=completed_at,
        )
        latest = _row(
            status="completed",
            assistant="Done.",
            error_code=None,
            activities=[activity.to_dict()],
            completed_at=completed_at,
        )
        repository = PostgresChatHistoryRepository(  # type: ignore[arg-type]
            _Pool([], latest_rows=[latest])
        )
        scope = ChatMemoryScope(tenant_id="workspace-1", user_id="user-1", session_id="session-1")

        result = await repository.latest_turns_for((scope,))

        assert result["session-1"].activities == (activity,)
        assert result["session-1"].completed_at == completed_at

    asyncio.run(scenario())
