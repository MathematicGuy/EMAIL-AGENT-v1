"""Runtime setup shared by API and worker entry points."""

from __future__ import annotations

import asyncio
import sys


def configure_windows_event_loop_policy() -> None:
    """Select the loop implementation supported by psycopg's async driver."""
    if sys.platform != "win32":
        return
    from asyncio import windows_events

    asyncio.set_event_loop_policy(windows_events.WindowsSelectorEventLoopPolicy())


def run_app_coroutine(coro: object) -> None:
    """Run an async entry point with the SelectorEventLoop required by psycopg on Windows."""
    if sys.platform == "win32":
        from asyncio import windows_events

        asyncio.run(
            coro,  # type: ignore[arg-type]
            loop_factory=windows_events.WindowsSelectorEventLoopPolicy().new_event_loop,
        )
    else:
        asyncio.run(coro)  # type: ignore[arg-type]

