"""Run at most one tool for one chat turn: bind, fill arguments, dispatch."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime

from cowork_agent.domain.chat_contracts import ChatTurn

from .arguments import ToolArgumentCompletion, fill_arguments
from .registry import Tool, ToolRegistry, ToolResult

# A tool bound to one turn. `idempotency_key` becomes the created resource's id,
# which is what makes a retried turn idempotent; `now` is what resolves "ngày
# mai" into a date.
ToolBinder = Callable[[str, datetime], Tool]


class ChatToolRunner:
    """The controller's whole view of tools: one call in, one result out.

    Tools are bound per turn rather than per process because the calendar tool
    needs the turn's idempotency key and the current time. `names` is stable
    across turns, which is what lets the router narrow on it before any binding
    happens.
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
    ) -> ToolResult:
        """Run the named tool for this turn. Never raises, for the same reason
        `ToolRegistry.run` does not: the controller is mid-stream."""

        binder = self._binders.get(tool_name)
        if binder is None:
            # The router narrows unknown names before this point, so reaching
            # here means the two disagree -- report it rather than guessing.
            return ToolResult(ok=False, text=f"No tool named {tool_name!r} is available.")
        tool = binder(idempotency_key, now)
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
