from datetime import UTC, datetime, timedelta

import pytest

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatTurn,
    MemoryNamespace,
    MemoryType,
)
from cowork_agent.features.ai_chat.session_buffer import InMemoryChatSessionBuffer

pytestmark = pytest.mark.extended

START = datetime(2026, 8, 10, 9, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


def _namespace(*, session_id: str = "session-1") -> MemoryNamespace:
    return MemoryNamespace(
        scope=ChatMemoryScope(
            user_id="user@example.com",
            session_id=session_id,
        ),
        memory_type=MemoryType.SHORT_TERM,
        record_id=session_id,
        source_id=None,
    )


def _turn(number: int, *, session_id: str = "session-1") -> ChatTurn:
    return ChatTurn(
        turn_id=f"turn-{number}",
        session_id=session_id,
        user_message=f"Question {number}",
        assistant_message=f"Answer {number}",
        created_at=START + timedelta(seconds=number),
    )


def test_buffer_compacts_to_the_newest_complete_turns() -> None:
    buffer = InMemoryChatSessionBuffer(max_turns=2, ttl_seconds=60, clock=FakeClock())
    namespace = _namespace()

    buffer.append(namespace, _turn(1))
    buffer.append(namespace, _turn(2))
    buffer.append(namespace, _turn(3))

    assert buffer.read(namespace) == (_turn(2), _turn(3))


def test_buffer_expires_at_the_exact_inactivity_ttl_and_evicts_permanently() -> None:
    clock = FakeClock()
    buffer = InMemoryChatSessionBuffer(max_turns=2, ttl_seconds=60, clock=clock)
    namespace = _namespace()
    buffer.append(namespace, _turn(1))

    clock.advance(59)
    assert buffer.read(namespace) == (_turn(1),)
    clock.advance(1)
    assert buffer.read(namespace) == ()

    clock.now = START
    assert buffer.read(namespace) == ()


def test_append_refreshes_inactivity_ttl_and_expired_session_starts_fresh() -> None:
    clock = FakeClock()
    buffer = InMemoryChatSessionBuffer(max_turns=3, ttl_seconds=60, clock=clock)
    namespace = _namespace()
    buffer.append(namespace, _turn(1))
    clock.advance(30)
    buffer.append(namespace, _turn(2))
    clock.advance(59)
    assert buffer.read(namespace) == (_turn(1), _turn(2))

    clock.advance(1)
    buffer.append(namespace, _turn(3))
    assert buffer.read(namespace) == (_turn(3),)


def test_clear_is_idempotent_and_scoped_to_one_session() -> None:
    buffer = InMemoryChatSessionBuffer(max_turns=2, ttl_seconds=60, clock=FakeClock())
    first = _namespace(session_id="session-1")
    second = _namespace(session_id="session-2")
    buffer.append(first, _turn(1))
    buffer.append(second, _turn(2, session_id="session-2"))

    buffer.clear(first)
    buffer.clear(first)

    assert buffer.read(first) == ()
    assert buffer.read(second) == (_turn(2, session_id="session-2"),)


def test_sweep_removes_only_expired_sessions() -> None:
    clock = FakeClock()
    buffer = InMemoryChatSessionBuffer(max_turns=2, ttl_seconds=60, clock=clock)
    first = _namespace(session_id="session-1")
    second = _namespace(session_id="session-2")
    buffer.append(first, _turn(1))
    clock.advance(30)
    buffer.append(second, _turn(2, session_id="session-2"))
    clock.advance(31)

    assert buffer.sweep() == 1
    assert buffer.read(first) == ()
    assert buffer.read(second) == (_turn(2, session_id="session-2"),)


@pytest.mark.parametrize(
    "namespace",
    [
        MemoryNamespace(
            scope=_namespace().scope,
            memory_type=MemoryType.EPISODIC,
            record_id="session-1",
            source_id=None,
        ),
        MemoryNamespace(
            scope=_namespace().scope,
            memory_type=MemoryType.SHORT_TERM,
            record_id="other-session",
            source_id=None,
        ),
    ],
    ids=["wrong_memory_type", "record_session_mismatch"],
)
def test_buffer_rejects_inconsistent_namespaces(namespace: MemoryNamespace) -> None:
    buffer = InMemoryChatSessionBuffer(max_turns=2, ttl_seconds=60, clock=FakeClock())

    with pytest.raises(ValueError):
        buffer.read(namespace)


def test_buffer_rejects_a_turn_for_another_session() -> None:
    buffer = InMemoryChatSessionBuffer(max_turns=2, ttl_seconds=60, clock=FakeClock())

    with pytest.raises(ValueError, match="session"):
        buffer.append(_namespace(), _turn(1, session_id="session-2"))


@pytest.mark.parametrize(
    ("max_turns", "ttl_seconds"),
    [(0, 60), (-1, 60), (2, 0), (2, -1)],
)
def test_buffer_rejects_non_positive_bounds(max_turns: int, ttl_seconds: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        InMemoryChatSessionBuffer(
            max_turns=max_turns,
            ttl_seconds=ttl_seconds,
            clock=FakeClock(),
        )
