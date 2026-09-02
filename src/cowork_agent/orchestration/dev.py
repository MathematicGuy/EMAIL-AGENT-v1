"""Run the API and durable worker together for local development."""

from __future__ import annotations

import subprocess
import sys
import time
from argparse import ArgumentParser
from collections.abc import Callable, Sequence
from typing import Protocol


class ManagedProcess(Protocol):
    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


ProcessFactory = Callable[[Sequence[str]], ManagedProcess]


def _stop(process: ManagedProcess) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_dev(
    *,
    reload: bool = True,
    spawn: ProcessFactory = subprocess.Popen,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Keep the API and document worker alive as one local command."""
    import os

    env_reload = os.getenv("APP_RELOAD")
    if env_reload is not None:
        is_reload = env_reload.strip().lower() in {"true", "1", "yes"}
    else:
        is_reload = reload

    api_cmd: Sequence[str] = (
        (sys.executable, "-m", "cowork_agent.app", "--reload")
        if is_reload
        else (sys.executable, "-m", "cowork_agent.app")
    )
    commands = (
        api_cmd,
        (sys.executable, "-m", "cowork_agent.orchestration.worker"),
    )
    processes: list[ManagedProcess] = []

    try:
        for command in commands:
            processes.append(spawn(command))
        print("API and worker started. Press Ctrl+C to stop both.")

        while True:
            for process in processes:
                exit_code = process.poll()
                if exit_code is not None:
                    return exit_code if exit_code != 0 else 1
            sleep(0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        for process in processes:
            _stop(process)


def main() -> None:
    parser = ArgumentParser(
        description="Run the Cowork API and its durable worker for local development."
    )
    parser.add_argument(
        "--reload",
        dest="reload",
        action="store_true",
        default=True,
        help="Enable auto-reloading for the API server on code changes (default: True).",
    )
    parser.add_argument(
        "--no-reload",
        dest="reload",
        action="store_false",
        help="Disable auto-reloading for the API server on code changes.",
    )
    args = parser.parse_args()
    if args.reload:
        import os

        os.environ["APP_RELOAD"] = "true"
    elif args.reload is False:
        import os

        os.environ["APP_RELOAD"] = "false"
    raise SystemExit(run_dev(reload=args.reload))


if __name__ == "__main__":
    main()
