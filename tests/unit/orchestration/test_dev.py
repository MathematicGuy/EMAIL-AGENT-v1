from __future__ import annotations

from collections.abc import Sequence

from cowork_agent.orchestration.dev import run_dev


class FakeProcess:
    def __init__(self, exit_code: int | None) -> None:
        self.exit_code = exit_code
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.exit_code

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.exit_code = self.exit_code if self.exit_code is not None else 0
        return self.exit_code

    def terminate(self) -> None:
        self.terminated = True
        self.exit_code = 0

    def kill(self) -> None:
        self.killed = True
        self.exit_code = 0


def test_run_dev_stops_the_other_service_when_one_exits() -> None:
    api = FakeProcess(2)
    worker = FakeProcess(None)
    commands: list[Sequence[str]] = []

    def spawn(command: Sequence[str]) -> FakeProcess:
        commands.append(command)
        return (api, worker)[len(commands) - 1]

    assert run_dev(spawn=spawn, sleep=lambda _: None) == 2
    assert commands[0][-1] == "cowork_agent.app"
    assert commands[1][-1] == "cowork_agent.orchestration.worker"
    assert worker.terminated
