"""The durable trail of one chat turn, and the guard that stops it.

``stream_message`` used to thread the evolving :class:`ChatTurn` through the
whole phase sequence by hand and re-derive the same four steps -- transition
the activity snapshot, persist it, refresh the live-turn registry, build the
event -- at every phase boundary. Both concerns live here instead: a
:class:`TurnJournal` owns the turn while it is being generated, and a
:class:`CancellationGuard` owns the single question "must this turn stop?".
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, MutableMapping
from collections.abc import Set as AbstractSet
from dataclasses import replace
from datetime import datetime

from cowork_agent.domain.chat_contracts import (
    ChatActivity,
    ChatActivityCode,
    ChatActivityDetail,
    ChatActivityOutcome,
    ChatActivityStatus,
    ChatMemoryScope,
    ChatMessageStreamEvent,
    ChatTurn,
    transition_activity_snapshot,
)

from .ports import ChatHistoryPort

CancellationCheck = Callable[[], Awaitable[bool]]
IdFactory = Callable[[], str]
Clock = Callable[[], datetime]
logger = logging.getLogger(__name__)


async def never_cancelled() -> bool:
    """The default check for callers with no client to disconnect."""

    return False


class CancellationGuard:
    """The one place that answers whether the running turn must stop.

    Two independent signals mean the same thing to the driver: an explicit
    ``cancel_turn`` on this turn id, and the client having gone away. Phases
    ask once (``await guard.tripped()``) rather than re-spelling the pair, so a
    third signal would only have to be taught here.
    """

    __slots__ = ("_cancelled_turn_ids", "_is_cancelled", "_turn_id")

    def __init__(
        self,
        is_cancelled: CancellationCheck,
        cancelled_turn_ids: AbstractSet[str],
    ) -> None:
        self._is_cancelled = is_cancelled
        self._cancelled_turn_ids = cancelled_turn_ids
        self._turn_id: str | None = None

    def watch(self, turn_id: str) -> None:
        """Start honouring ``cancel_turn`` once the durable turn id exists."""

        self._turn_id = turn_id

    async def tripped(self) -> bool:
        """An explicit cancel short-circuits before the client is consulted."""

        if self._turn_id is not None and self._turn_id in self._cancelled_turn_ids:
            return True
        return await self._is_cancelled()


class TurnJournal:
    """The activity trail of one turn: transition, persist, publish.

    ``record`` is the single call a phase boundary makes. It transitions the
    activity snapshot, persists the turn, refreshes the caller's live-turn
    registry and returns the event to yield -- the phase never touches the
    snapshot, the history port or the registry itself. ``turn`` is always the
    latest durable value, so no caller has to thread it forward.
    """

    __slots__ = ("_clock", "_history", "_new_id", "_registry", "_scope", "_turn")

    def __init__(
        self,
        turn: ChatTurn,
        *,
        scope: ChatMemoryScope,
        history: ChatHistoryPort | None,
        clock: Clock,
        new_id: IdFactory,
        registry: MutableMapping[str, ChatTurn],
    ) -> None:
        self._scope = scope
        self._history = history
        self._clock = clock
        self._new_id = new_id
        self._registry = registry
        # Registering on construction is what lets ``cancel_turn`` find this
        # turn from the moment generation starts.
        self.adopt(turn)

    @property
    def turn(self) -> ChatTurn:
        """The latest known state of the turn being generated."""

        return self._turn

    def adopt(self, turn: ChatTurn) -> None:
        """Take over a turn value the caller built outside the journal."""

        self._turn = turn
        self._registry[turn.turn_id] = turn

    async def record(
        self,
        code: ChatActivityCode,
        status: ChatActivityStatus,
        *,
        outcome: ChatActivityOutcome | None = None,
        detail: ChatActivityDetail | None = None,
        append: tuple[ChatActivityCode, ...] = (),
    ) -> ChatMessageStreamEvent:
        """Move one activity to ``status`` and hand back the event to yield."""

        activities = transition_activity_snapshot(
            self._turn.activities,
            code,
            status,
            at=self._clock(),
            outcome=outcome,
            detail=detail,
        )
        activities = (*activities, *(ChatActivity.pending(item) for item in append))
        updated = replace(self._turn, activities=activities)
        if self._history is not None:
            try:
                updated = await self._history.update_turn(self._scope, updated)
            except Exception:
                logger.exception("Unable to persist chat activity progress")
        self._turn = updated
        self._registry[updated.turn_id] = updated
        return self.activity_event()

    def activity_event(self, turn: ChatTurn | None = None) -> ChatMessageStreamEvent:
        """Publish a snapshot without recording a transition.

        Terminal paths (``_fail_turn``) have already written their own turn and
        only need it announced, so they pass it in explicitly.
        """

        published = self._turn if turn is None else turn
        return ChatMessageStreamEvent.activity(
            event_id=self._new_id(),
            session_id=self._scope.session_id,
            turn_id=published.turn_id,
            activities=published.activities,
        )
