import asyncio
from datetime import UTC, datetime

import pytest
from redis.exceptions import RedisError

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatTurn,
    DegradedMemorySource,
    EpisodicMemoryRead,
    MemoryContextRequest,
    MemoryNamespace,
    MemoryReadOptions,
    MemoryType,
    SemanticMemoryRead,
)
from cowork_agent.features.ai_chat.memory_gateway import MemoryGateway
from cowork_agent.features.ai_chat.session_buffer import (
    ChatSessionBufferUnavailable,
    RedisChatSessionBuffer,
)

NOW = datetime(2026, 8, 12, 12, tzinfo=UTC)


class FakePipeline:
    def __init__(self, redis: "FakeRedis") -> None:
        self.redis = redis
        self.operations: list[tuple[object, ...]] = []

    def lpush(self, key: str, value: str) -> "FakePipeline":
        self.operations.append(("lpush", key, value))
        return self

    def ltrim(self, key: str, start: int, stop: int) -> "FakePipeline":
        self.operations.append(("ltrim", key, start, stop))
        return self

    def expire(self, key: str, seconds: int) -> "FakePipeline":
        self.operations.append(("expire", key, seconds))
        return self

    def execute(self) -> None:
        for operation in self.operations:
            command, key, *arguments = operation
            if command == "lpush":
                self.redis.values.setdefault(key, []).insert(0, str(arguments[0]))
            elif command == "ltrim":
                values = self.redis.values.get(key, [])
                start, stop = int(arguments[0]), int(arguments[1])
                self.redis.values[key] = values[start : stop + 1]
            elif command == "expire":
                self.redis.expiries[key] = int(arguments[0])


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, list[str]] = {}
        self.expiries: dict[str, int] = {}

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        assert transaction is True
        return FakePipeline(self)

    def lrange(self, key: str, start: int, stop: int) -> list[str]:
        values = self.values.get(key, [])
        return values[start:] if stop == -1 else values[start : stop + 1]

    def delete(self, key: str) -> None:
        self.values.pop(key, None)
        self.expiries.pop(key, None)

    def ttl(self, key: str) -> int:
        return self.expiries.get(key, -2)


class UnavailableRedis(FakeRedis):
    def lrange(self, key: str, start: int, stop: int) -> list[str]:
        del key, start, stop
        raise RedisError("connection lost")


def _scope() -> ChatMemoryScope:
    return ChatMemoryScope(tenant_id="workspace-1", user_id="user-1", session_id="session-1")


def _namespace() -> MemoryNamespace:
    return MemoryNamespace(
        scope=_scope(),
        memory_type=MemoryType.SHORT_TERM,
        record_id="session-1",
        source_id=None,
    )


def _turn(number: int) -> ChatTurn:
    return ChatTurn(
        turn_id=f"turn-{number}",
        session_id="session-1",
        user_message=f"Question {number}",
        assistant_message=f"Answer {number}",
        created_at=NOW,
    )


def test_redis_buffer_keeps_newest_turns_and_refreshes_its_ttl() -> None:
    redis = FakeRedis()
    buffer = RedisChatSessionBuffer(redis, max_turns=2, ttl_seconds=60)
    namespace = _namespace()

    buffer.append(namespace, _turn(1))
    buffer.append(namespace, _turn(2))
    buffer.append(namespace, _turn(3))

    assert buffer.read(namespace) == (_turn(2), _turn(3))
    assert redis.ttl(buffer.redis_key(namespace)) == 60


def test_redis_buffer_translates_a_redis_outage_without_exposing_turn_contents() -> None:
    buffer = RedisChatSessionBuffer(UnavailableRedis(), max_turns=2, ttl_seconds=60)

    with pytest.raises(ChatSessionBufferUnavailable) as caught:
        buffer.read(_namespace())

    assert "Question" not in str(caught.value)


def test_gateway_reports_short_term_degradation_when_redis_is_unavailable() -> None:
    scope = _scope()
    gateway = MemoryGateway(
        scope=scope,
        session_buffer=RedisChatSessionBuffer(UnavailableRedis(), max_turns=2, ttl_seconds=60),
    )
    request = MemoryContextRequest(
        session_id=scope.session_id,
        scope=scope,
        reads=MemoryReadOptions(
            short_term=True,
            long_term=False,
            episodic=EpisodicMemoryRead(False, True, 1),
            semantic=SemanticMemoryRead(False),
        ),
    )

    response = asyncio.run(gateway.read_context(request))

    assert response.turns == ()
    assert response.degraded_sources == (DegradedMemorySource.SHORT_TERM,)
