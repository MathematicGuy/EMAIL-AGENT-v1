"""Framework-free graph node helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from .state import ChatGraphState

Node = Callable[[ChatGraphState], Awaitable[ChatGraphState]]


def retrieval_branch(state: ChatGraphState) -> str:
    return "retrieve" if state.get("route") == "rag" else "assemble"


async def mark_completed(state: ChatGraphState) -> ChatGraphState:
    return {**state, "completed": True}
