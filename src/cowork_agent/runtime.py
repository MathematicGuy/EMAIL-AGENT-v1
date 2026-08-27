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


def configure_windows_reload() -> None:
    """Prevent Uvicorn reload on Windows from broadcasting Ctrl+C to parent process."""
    if sys.platform != "win32":
        return
    try:
        import uvicorn.supervisors.basereload as _br
        from uvicorn._subprocess import get_subprocess as _get_subprocess

        def _windows_safe_restart(self: _br.BaseReload) -> None:
            self.process.terminate()
            self.process.join()
            self.process = _get_subprocess(
                config=self.config, target=self.target, sockets=self.sockets
            )
            self.process.start()

        # method-assign is required on Windows; unused on Linux CI (mypy unused-ignore).
        _br.BaseReload.restart = _windows_safe_restart  # type: ignore[method-assign, unused-ignore]
    except Exception:
        pass


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
