"""Bounded local and Redis-backed Chat Session Working Memory adapters."""

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from redis.exceptions import RedisError

from cowork_agent.domain.chat_contracts import ChatTurn, MemoryNamespace, MemoryType


class _RedisPipeline(Protocol):
    def lpush(self, key: str, value: str) -> "_RedisPipeline": ...

    def ltrim(self, key: str, start: int, stop: int) -> "_RedisPipeline": ...

    def expire(self, key: str, seconds: int) -> "_RedisPipeline": ...

    def execute(self) -> object: ...


class RedisClient(Protocol):
    def pipeline(self, transaction: bool = True) -> _RedisPipeline: ...

    def lrange(self, key: str, start: int, stop: int) -> list[str]: ...

    def delete(self, key: str) -> object: ...

    def close(self) -> None: ...


class ChatSessionBufferUnavailable(RuntimeError):
    """Redis cannot safely provide ephemeral chat-session working memory."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class _SessionEntry:
    turns: tuple[ChatTurn, ...]
    expires_at: datetime


class InMemoryChatSessionBuffer:
    """Newest-N logical turns with an inactivity TTL refreshed by appends."""

    def __init__(
        self,
        *,
        max_turns: int,
        ttl_seconds: int,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if max_turns <= 0 or ttl_seconds <= 0:
            raise ValueError("max_turns and ttl_seconds must be positive")
        self._max_turns = max_turns
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock
        self._entries: dict[str, _SessionEntry] = {}
        self._lock = threading.Lock()

    def append(self, namespace: MemoryNamespace, turn: ChatTurn) -> None:
        key = self._validated_key(namespace)
        if turn.session_id != namespace.session_id:
            raise ValueError("turn session_id must match the memory namespace")
        with self._lock:
            now = self._clock()
            self._sweep_locked(now)
            existing = self._entries.get(key)
            turns = (() if existing is None else existing.turns) + (turn,)
            self._entries[key] = _SessionEntry(
                turns=turns[-self._max_turns :],
                expires_at=now + self._ttl,
            )

    def read(self, namespace: MemoryNamespace) -> tuple[ChatTurn, ...]:
        key = self._validated_key(namespace)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return ()
            if entry.expires_at <= self._clock():
                del self._entries[key]
                return ()
            return entry.turns

    def clear(self, namespace: MemoryNamespace) -> None:
        key = self._validated_key(namespace)
        with self._lock:
            self._entries.pop(key, None)

    def sweep(self) -> int:
        with self._lock:
            return self._sweep_locked(self._clock())

    @staticmethod
    def _validated_key(namespace: MemoryNamespace) -> str:
        if namespace.memory_type is not MemoryType.SHORT_TERM:
            raise ValueError("Chat Session Buffer requires short_term memory")
        if namespace.record_id != namespace.session_id:
            raise ValueError("short_term record_id must equal session_id")
        if namespace.source_id is not None:
            raise ValueError("short_term session namespaces do not carry source_id")
        return namespace.logical_key()

    def _sweep_locked(self, now: datetime) -> int:
        expired = [key for key, entry in self._entries.items() if entry.expires_at <= now]
        for key in expired:
            del self._entries[key]
        return len(expired)


class RedisChatSessionBuffer:
    """Bounded newest-N turns with Redis-backed sliding expiry."""

    def __init__(self, redis: RedisClient, *, max_turns: int, ttl_seconds: int) -> None:
        if max_turns <= 0 or ttl_seconds <= 0:
            raise ValueError("max_turns and ttl_seconds must be positive")
        self._redis = redis
        self._max_turns = max_turns
        self._ttl_seconds = ttl_seconds

    def append(self, namespace: MemoryNamespace, turn: ChatTurn) -> None:
        key = self.redis_key(namespace)
        if turn.session_id != namespace.session_id:
            raise ValueError("turn session_id must match the memory namespace")
        try:
            pipeline = self._redis.pipeline(transaction=True)
            pipeline.lpush(key, json.dumps(turn.to_dict(), separators=(",", ":")))
            pipeline.ltrim(key, 0, self._max_turns - 1)
            pipeline.expire(key, self._ttl_seconds)
            pipeline.execute()
        except RedisError as exc:
            raise ChatSessionBufferUnavailable("short-term chat memory unavailable") from exc

    def read(self, namespace: MemoryNamespace) -> tuple[ChatTurn, ...]:
        key = self.redis_key(namespace)
        try:
            values = self._redis.lrange(key, 0, -1)
        except RedisError as exc:
            raise ChatSessionBufferUnavailable("short-term chat memory unavailable") from exc
        return tuple(ChatTurn.from_dict(json.loads(value)) for value in reversed(values))

    def clear(self, namespace: MemoryNamespace) -> None:
        try:
            self._redis.delete(self.redis_key(namespace))
        except RedisError as exc:
            raise ChatSessionBufferUnavailable("short-term chat memory unavailable") from exc

    @staticmethod
    def redis_key(namespace: MemoryNamespace) -> str:
        return f"cowork:chat:short-term:{InMemoryChatSessionBuffer._validated_key(namespace)}"
