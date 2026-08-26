"""Run at most one tool for one chat turn: bind, fill arguments, dispatch."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from cowork_agent.domain.chat_contracts import ChatTurn

from .arguments import ToolArgumentCompletion, fill_arguments
from .registry import Tool, ToolRegistry, ToolResult


@dataclass(frozen=True, slots=True)
class ToolTurnContext:
    """Everything a tool needs to know about the turn it is being bound to.

    One parameter object rather than three arguments: the next writing tool
    will want the same three, and a binder signature that grows once will grow
    again. `user_id` is explicitly nullable — `None` is local development with no
    principal, and the binder decides what that means rather than the runner
    guessing on its behalf.
    """

    idempotency_key: str
    now: datetime
    user_id: str | None = None


# A tool bound to one turn. Async because a per-user grant is a repository read
# (ADR-019): the credential belongs to whoever is speaking, so it cannot be
# resolved once at composition time.
ToolBinder = Callable[[ToolTurnContext], Awaitable[Tool]]


class ChatToolRunner:
    """The controller's whole view of tools: one call in, one result out.

    Tools are bound per turn rather than per process because the calendar tool
    needs the turn's idempotency key, the current time, and — since ADR-019 —
    the grant belonging to the user whose turn it is. `names` is stable across
    turns and across users, which is what lets the router narrow on it before
    any binding happens: whether a tool *runs* is per-user, whether it *exists*
    is not.
    """

    def __init__(
        self,
        binders: Mapping[str, ToolBinder],
        *,
        complete: ToolArgumentCompletion,
    ) -> None:
        self._binders = dict(binders)
        self._complete = complete

    @property
    def names(self) -> frozenset[str]:
        """Tool names, for `finalize_route(available_tools=...)`."""

        return frozenset(self._binders)

    async def run_for_turn(
        self,
        tool_name: str,
        *,
        user_message: str,
        recent_turns: Sequence[ChatTurn] = (),
        idempotency_key: str,
        now: datetime,
        user_id: str | None = None,
    ) -> ToolResult:
        """Run the named tool for this turn. Never raises, for the same reason
        `ToolRegistry.run` does not: the controller is mid-stream."""

        binder = self._binders.get(tool_name)
        if binder is None:
            # The router narrows unknown names before this point, so reaching
            # here means the two disagree -- report it rather than guessing.
            return ToolResult(ok=False, text=f"No tool named {tool_name!r} is available.")
        tool = await binder(
            ToolTurnContext(idempotency_key=idempotency_key, now=now, user_id=user_id)
        )
        arguments = await fill_arguments(
            self._complete,
            tool,
            user_message=user_message,
            recent_turns=recent_turns,
            now=now,
        )
        if isinstance(arguments, str):
            return ToolResult(ok=False, text=arguments)
        return await ToolRegistry([tool]).run(tool_name, arguments)
