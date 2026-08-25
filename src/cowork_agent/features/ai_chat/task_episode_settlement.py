"""Landing a task episode on a chat turn — first attempt and retry.

An explicit task request ends its turn by durably writing one `TaskEpisode`
and announcing it as a citation plus a proposal card. When the episodic store
is transiently unavailable the turn still completes, the write is remembered
as a `PendingTaskEpisode`, and the next request carrying the same idempotency
key retries exactly that write and replays the turn's events around it.

Those two halves used to sit ~60 lines apart inside `ChatController`, building
the same citation/proposal pair from the same helper against the same caches —
mirror images that had to be kept in step by hand. They are one module here,
which is also what lets the controller stop owning the pending-episode cache.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, MutableMapping
from dataclasses import dataclass
from datetime import datetime

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatMessageRequest,
    ChatMessageStreamEvent,
    ChatTurn,
    EpisodeSourceType,
    MemoryCitationType,
    TaskEpisode,
)
from cowork_agent.domain.target_contracts import ValidationStatus

from .memory_gateway import MemoryGateway, MemorySourceUnavailableError
from .ports import ChatTaskProposal
from .retention import compute_expires_at
from .turn_journal import CancellationGuard, Clock, IdFactory

_UNAVAILABLE_MESSAGE = "Không thể lưu đề xuất công việc."

CompletedStreams = MutableMapping[
    str, tuple[ChatMessageRequest, tuple[ChatMessageStreamEvent, ...]]
]


class TurnAborted(Exception):
    """A durable write failed and the turn cannot be completed.

    The driver has one job on catching this: yield the carried error event and
    stop. Raising rather than returning a sentinel is what keeps the two
    completion paths — normal turn and task turn — from drifting apart again.
    """

    def __init__(self, event: ChatMessageStreamEvent) -> None:
        super().__init__("chat turn aborted before completion")
        self.event = event


@dataclass(frozen=True, slots=True)
class PendingTaskEpisode:
    """A task-episode write that has to be retried on the next same-key request."""

    request: ChatMessageRequest
    episode: TaskEpisode
    replay_prefix: tuple[ChatMessageStreamEvent, ...]
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TaskEpisodeSettlement:
    """What one settlement attempt produced.

    `events` is ordered and ready to yield; `degraded` decides the final
    activity outcome; `pending` is set only when the write is retryable.
    """

    events: tuple[ChatMessageStreamEvent, ...]
    degraded: bool
    pending: PendingTaskEpisode | None


def proposal_payload(episode: TaskEpisode) -> dict[str, object]:
    """Frontend-safe structured proposal for the task_proposal SSE event."""
    return {
        "episode_id": episode.episode_id,
        "task_title": episode.task_title,
        "minimal_request_paraphrase": episode.minimal_request_paraphrase,
        "action_plan": list(episode.action_plan),
        "missing_information": list(episode.missing_information),
        "rag_citations": [citation.to_dict() for citation in episode.rag_citations],
        "validation_status": episode.validation_status.value,
        "retrieval_eligible": episode.retrieval_eligible,
    }


class TaskEpisodeSettler:
    """Both halves of getting one task episode durably stored and announced.

    Owns the pending-write cache outright. The episode, completed-stream and
    live-turn registries stay the controller's — they are read by paths that
    have nothing to do with settlement — and are passed in as mappings.
    """

    __slots__ = (
        "_clock",
        "_completed",
        "_episode_retention_seconds",
        "_episodes",
        "_memory",
        "_new_id",
        "_pending",
        "_scope",
        "_turns_by_id",
    )

    def __init__(
        self,
        *,
        scope: ChatMemoryScope,
        memory: MemoryGateway,
        new_id: IdFactory,
        clock: Clock,
        episode_retention_seconds: int | None,
        episodes: MutableMapping[str, TaskEpisode],
        completed: CompletedStreams,
        turns_by_id: MutableMapping[str, ChatTurn],
    ) -> None:
        self._scope = scope
        self._memory = memory
        self._new_id = new_id
        self._clock = clock
        self._episode_retention_seconds = episode_retention_seconds
        self._episodes = episodes
        self._completed = completed
        self._turns_by_id = turns_by_id
        self._pending: dict[str, PendingTaskEpisode] = {}

    def pending_for(self, idempotency_key: str) -> PendingTaskEpisode | None:
        """The retryable write left behind by an earlier request, if any."""

        return self._pending.get(idempotency_key)

    def remember_pending(self, idempotency_key: str, pending: PendingTaskEpisode) -> None:
        """Arm the retry for the next request carrying this key."""

        self._pending[idempotency_key] = pending

    async def settle(
        self,
        *,
        request: ChatMessageRequest,
        turn_id: str,
        proposal: ChatTaskProposal | None,
        replay_prefix: tuple[ChatMessageStreamEvent, ...],
    ) -> TaskEpisodeSettlement:
        """Write the episode and produce the events announcing it.

        Every failure here degrades rather than fails the turn: the assistant
        message is already generated and the user must still receive it. Only
        `MemorySourceUnavailableError` is retryable — a `ValueError` is a
        rejected record, and retrying it would fail identically forever.

        The events are returned rather than yielded because no await separates
        them: buffering cannot reorder or delay what reaches the client.
        """

        if proposal is None:
            return TaskEpisodeSettlement((self._unavailable(turn_id),), True, None)

        episode = self._new_episode(turn_id, proposal)
        expires_at = compute_expires_at(self._clock(), self._episode_retention_seconds)
        try:
            episode = await self._memory.write_task_episode(episode, expires_at=expires_at)
        except MemorySourceUnavailableError:
            pending = PendingTaskEpisode(
                request=request,
                episode=episode,
                replay_prefix=replay_prefix,
                expires_at=expires_at,
            )
            return TaskEpisodeSettlement((self._unavailable(turn_id),), True, pending)
        except ValueError:
            return TaskEpisodeSettlement((self._unavailable(turn_id),), True, None)

        self._episodes[episode.episode_id] = episode
        return TaskEpisodeSettlement(
            (
                ChatMessageStreamEvent.memory_citation(
                    event_id=self._new_id(),
                    session_id=self._scope.session_id,
                    turn_id=turn_id,
                    memory_type=MemoryCitationType.EPISODIC,
                    source_id=episode.episode_id,
                ),
                ChatMessageStreamEvent.task_proposal(
                    event_id=self._new_id(),
                    session_id=self._scope.session_id,
                    turn_id=turn_id,
                    proposal=proposal_payload(episode),
                ),
            ),
            False,
            None,
        )

    async def replay(
        self, pending: PendingTaskEpisode, guard: CancellationGuard
    ) -> AsyncIterator[ChatMessageStreamEvent]:
        """Retry the remembered write, then replay the turn around its result.

        A still-unavailable store replays the cached stream unchanged and keeps
        the pending write armed; a rejected record drops it. Only a successful
        write rewrites the cached stream so later replays carry the episode.
        """

        if await guard.tripped():
            return
        key = pending.request.idempotency_key
        try:
            episode = await self._memory.write_task_episode(
                pending.episode, expires_at=pending.expires_at
            )
        except MemorySourceUnavailableError:
            async for event in self._replay_cached(key, guard):
                yield event
            return
        except ValueError:
            del self._pending[key]
            async for event in self._replay_cached(key, guard):
                yield event
            return

        self._episodes[episode.episode_id] = episode
        completed_turn = self._turns_by_id.get(episode.chat_turn_id)
        replay = (
            *pending.replay_prefix,
            ChatMessageStreamEvent.memory_citation(
                event_id=self._new_id(),
                session_id=self._scope.session_id,
                turn_id=episode.chat_turn_id,
                memory_type=MemoryCitationType.EPISODIC,
                source_id=episode.episode_id,
            ),
            ChatMessageStreamEvent.task_proposal(
                event_id=self._new_id(),
                session_id=self._scope.session_id,
                turn_id=episode.chat_turn_id,
                proposal=proposal_payload(episode),
            ),
            ChatMessageStreamEvent.completed(
                event_id=self._new_id(),
                session_id=self._scope.session_id,
                turn_id=episode.chat_turn_id,
                execution_trace=(
                    completed_turn.execution_trace if completed_turn is not None else None
                ),
            ),
        )
        self._completed[key] = (pending.request, replay)
        del self._pending[key]
        for event in replay:
            if await guard.tripped():
                return
            yield event

    async def _replay_cached(
        self, idempotency_key: str, guard: CancellationGuard
    ) -> AsyncIterator[ChatMessageStreamEvent]:
        _, cached_events = self._completed[idempotency_key]
        for event in cached_events:
            if await guard.tripped():
                return
            yield event

    def _unavailable(self, turn_id: str) -> ChatMessageStreamEvent:
        return ChatMessageStreamEvent.error(
            event_id=self._new_id(),
            session_id=self._scope.session_id,
            turn_id=turn_id,
            code="task_episode_unavailable",
            safe_message=_UNAVAILABLE_MESSAGE,
        )

    def _new_episode(self, turn_id: str, proposal: ChatTaskProposal) -> TaskEpisode:
        """Build a body-free task record from trusted scope and turn metadata only."""

        created_at = self._clock()
        record_input = "\x1f".join((self._scope.user_id, self._scope.session_id, turn_id))
        return TaskEpisode(
            episode_id=self._new_id(),
            record_id=hashlib.sha256(record_input.encode("utf-8")).hexdigest(),
            user_id=self._scope.user_id,
            chat_session_id=self._scope.session_id,
            chat_turn_id=turn_id,
            creation_reason="explicit_user_task_request",
            task_title=proposal.task_title,
            minimal_request_paraphrase=proposal.minimal_request_paraphrase,
            action_plan=proposal.action_plan,
            rag_citations=proposal.rag_citations,
            missing_information=proposal.missing_information,
            validation_status=ValidationStatus.SYSTEM_GENERATED,
            retrieval_eligible=False,
            source_type=EpisodeSourceType.SYSTEM_GENERATED_CHAT_TASK,
            created_at=created_at,
            updated_at=created_at,
            pipeline_version="v2-m4",
            model_id=proposal.model_id,
            prompt_version=proposal.prompt_version,
            confidence=proposal.confidence,
            project_id=self._scope.project_id,
            supersedes=proposal.supersedes,
        )
