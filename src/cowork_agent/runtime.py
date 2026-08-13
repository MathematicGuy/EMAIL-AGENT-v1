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
