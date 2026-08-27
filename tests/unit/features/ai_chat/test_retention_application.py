"""Tests-first RED scaffold for V2-M6 operational retention.

Planned symbol: cowork_agent.features.ai_chat.retention.compute_expires_at
(not yet implemented).
"""

import asyncio
from datetime import UTC, datetime

import pytest


class FakePurgePort:
    def __init__(self, count: int = 0, error: Exception | None = None) -> None:
        self.count = count
        self.error = error
        self.calls: int = 0

    async def purge_expired(self, now: datetime) -> int:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.count


def test_compute_expires_at_none_when_retention_unset() -> None:
    from cowork_agent.features.ai_chat.retention import compute_expires_at

    now = datetime(2026, 8, 11, tzinfo=UTC)
    assert compute_expires_at(now, None) is None


def test_compute_expires_at_deterministic_utc() -> None:
    from cowork_agent.features.ai_chat.retention import compute_expires_at

    now = datetime(2026, 8, 11, tzinfo=UTC)
    assert compute_expires_at(now, 3600) == datetime(2026, 8, 11, 1, 0, tzinfo=UTC)


def test_compute_expires_at_rejects_naive_now() -> None:
    from cowork_agent.features.ai_chat.retention import compute_expires_at

    naive = datetime(2026, 8, 11)
    with pytest.raises(ValueError):
        compute_expires_at(naive, 3600)


def test_compute_expires_at_rejects_nonpositive_retention() -> None:
    from cowork_agent.features.ai_chat.retention import compute_expires_at

    now = datetime(2026, 8, 11, tzinfo=UTC)
    with pytest.raises(ValueError):
        compute_expires_at(now, 0)
    with pytest.raises(ValueError):
        compute_expires_at(now, -1)


def test_purge_requires_utc_aware_now() -> None:
    from cowork_agent.features.ai_chat.retention import MemoryPurgeCoordinator

    profiles = FakePurgePort(0)
    episodes = FakePurgePort(0)
    coordinator = MemoryPurgeCoordinator(profiles, episodes)
    naive_now = datetime(2026, 8, 11)
    with pytest.raises(ValueError):
        asyncio.run(coordinator.purge_expired(naive_now))


def test_purge_propagates_profile_port_failure() -> None:
    from cowork_agent.features.ai_chat.retention import MemoryPurgeCoordinator

    profiles = FakePurgePort(error=RuntimeError("profile failure"))
    episodes = FakePurgePort(0)
    coordinator = MemoryPurgeCoordinator(profiles, episodes)
    now = datetime(2026, 8, 11, tzinfo=UTC)
    with pytest.raises(RuntimeError, match="profile failure"):
        asyncio.run(coordinator.purge_expired(now))
    assert episodes.calls == 0


def test_purge_reports_both_counts() -> None:
    from cowork_agent.features.ai_chat.retention import (
        MemoryPurgeCoordinator,
        MemoryPurgeReport,
    )

    profiles = FakePurgePort(2)
    episodes = FakePurgePort(3)
    coordinator = MemoryPurgeCoordinator(profiles, episodes)
    now = datetime(2026, 8, 11, tzinfo=UTC)
    result = asyncio.run(coordinator.purge_expired(now))
    assert result == MemoryPurgeReport(2, 3, True)


def test_retry_reuses_same_expiry_boundary() -> None:
    """Controller computes expires_at once; retry reuses the identical boundary."""

    from collections.abc import AsyncIterator

    from cowork_agent.domain.chat_contracts import (
        ChatMemoryScope,
        ChatMessageRequest,
        TaskEpisode,
    )
    from cowork_agent.features.ai_chat.controller import ChatController
    from cowork_agent.features.ai_chat.generation_context import GenerationContext
    from cowork_agent.features.ai_chat.memory_gateway import (
        MemoryGateway,
        MemorySourceUnavailableError,
    )
    from cowork_agent.features.ai_chat.ports import ChatReplyChunk, ChatTaskProposal
    from cowork_agent.features.ai_chat.session_buffer import InMemoryChatSessionBuffer

    fixed_now = datetime(2026, 8, 11, tzinfo=UTC)
    scope = ChatMemoryScope(user_id="user@example.com", session_id="session-1")

    class RecordingEpisodicPort:
        def __init__(self) -> None:
            self.expires_at_values: list[object] = []
            self.attempts = 0

        async def read_episodes(self, namespace: object, query: object) -> tuple[()]:
            return ()

        async def write_chat_summary(self, namespace: object, episode: object) -> object:
            return episode

        async def write_task_episode(
            self, namespace: object, episode: TaskEpisode, *, expires_at: object
        ) -> TaskEpisode:
            self.expires_at_values.append(expires_at)
            self.attempts += 1
            if self.attempts == 1:
                raise MemorySourceUnavailableError("simulated outage")
            return episode

        async def transition_task_episode(self, transition: object) -> None:
            return None

        async def delete_task_episode(self, namespace: object, *, episode_id: str) -> bool:
            return False

        async def delete_chat_summary(self, namespace: object) -> bool:
            return False

        async def delete_all_for_user(self, namespace: object) -> int:
            return 0

    class TaskReply:
        async def stream_reply(
            self, request: ChatMessageRequest, context: GenerationContext
        ) -> AsyncIterator[str | ChatReplyChunk]:
            yield ChatReplyChunk(
                "task",
                ChatTaskProposal(
                    task_title="Do something",
                    minimal_request_paraphrase="Do it",
                    action_plan=("Step 1",),
                    rag_citations=(),
                    missing_information=(),
                    model_id="m",
                    prompt_version="p",
                    confidence=0.5,
                ),
            )

    port = RecordingEpisodicPort()
    buffer = InMemoryChatSessionBuffer(max_turns=4, ttl_seconds=60)
    gateway = MemoryGateway(scope=scope, session_buffer=buffer, episodic_memory=port)
    ids = iter(f"id-{n}" for n in range(1, 30))
    controller = ChatController(
        scope=scope,
        memory=gateway,
        reply=TaskReply(),
        new_id=lambda: next(ids),
        clock=lambda: fixed_now,
        episode_retention_seconds=3600,
    )
    request = ChatMessageRequest(
        session_id="session-1",
        user_message="Please create a task for this.",
        idempotency_key="idem-retry",
    )

    async def run_both_calls() -> list[object]:
        first = [event async for event in controller.stream_message(request)]
        second = [event async for event in controller.stream_message(request)]
        return first + second

    events = asyncio.run(run_both_calls())
    assert any(getattr(e, "code", None) == "task_episode_unavailable" for e in events)

    expected_expiry = datetime(2026, 8, 11, 1, 0, tzinfo=UTC)
    assert len(port.expires_at_values) == 2
    assert port.expires_at_values[0] == expected_expiry
    assert port.expires_at_values[1] == expected_expiry


def test_purge_coordinator_with_sink_emits_two_purge_expired_events() -> None:
    from cowork_agent.features.ai_chat.memory_observability import (
        MemoryOperation,
        MemoryOutcome,
        RecordingMemoryOperationSink,
    )
    from cowork_agent.features.ai_chat.retention import MemoryPurgeCoordinator

    profiles = FakePurgePort(2)
    episodes = FakePurgePort(3)
    sink = RecordingMemoryOperationSink()
    coordinator = MemoryPurgeCoordinator(profiles, episodes, sink=sink)
    now = datetime(2026, 8, 11, tzinfo=UTC)
    result = asyncio.run(coordinator.purge_expired(now))

    assert result.profile_count == 2
    assert result.episode_count == 3
    assert len(sink.events) == 2
    long_term_event = sink.events[0]
    episodic_event = sink.events[1]
    assert long_term_event.operation == MemoryOperation.DELETE
    assert long_term_event.outcome == MemoryOutcome.SUCCESS
    assert long_term_event.result_count == 2
    assert long_term_event.reason_code == "purge_expired"
    assert episodic_event.operation == MemoryOperation.DELETE
    assert episodic_event.outcome == MemoryOutcome.SUCCESS
    assert episodic_event.result_count == 3
    assert episodic_event.reason_code == "purge_expired"


def test_purge_coordinator_sink_failure_does_not_break_purge() -> None:
    from cowork_agent.features.ai_chat.retention import MemoryPurgeCoordinator

    class RaisingSink:
        def emit(self, event: object) -> None:
            raise RuntimeError("sink failure")

    profiles = FakePurgePort(2)
    episodes = FakePurgePort(3)
    coordinator = MemoryPurgeCoordinator(profiles, episodes, sink=RaisingSink())
    now = datetime(2026, 8, 11, tzinfo=UTC)
    result = asyncio.run(coordinator.purge_expired(now))

    assert result.profile_count == 2
    assert result.episode_count == 3
    assert result.complete is True
