"""H11 - the harness cannot write episodes to PostgreSQL.

`ChatMemoryScope.project_id` defaults to the legacy sentinel `"default-project"`,
and the harness builds its scopes directly rather than through
`chat_sessions.create`, which is what resolves that sentinel to a real project
UUID in the running app. `task_episodes.project_id` is `uuid`, so every episodic
write failed with:

    InvalidTextRepresentation: invalid input syntax for type uuid: "default-project"

SQLite's column is untyped, so the same write succeeded there and the defect was
invisible for as long as the evaluation only ran on SQLite.

The scope contract will not accept `None` for `project_id` (`_require_key_component`
rejects it), so the sentinel is translated at the write instead.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from cowork_agent.features.ai_chat.memory_eval.default_project import (
    NullDefaultProjectEpisodes,
)

pytestmark = pytest.mark.extended


@dataclass(frozen=True)
class _Episode:
    """Minimal stand-in carrying the one field under test."""

    project_id: str | None


class _RecordingEpisodes:
    """Captures what the real repository would have been asked to write."""

    def __init__(self) -> None:
        self.written: list[Any] = []
        self.calls: list[str] = []

    async def write_task_episode(self, namespace: Any, episode: Any, **kwargs: Any) -> Any:
        self.calls.append("write_task_episode")
        self.written.append(episode)
        return episode

    async def read_episodes(self, *args: Any, **kwargs: Any) -> tuple[Any, ...]:
        self.calls.append("read_episodes")
        return ()


def test_the_default_project_sentinel_is_written_as_null() -> None:
    inner = _RecordingEpisodes()
    episodes = NullDefaultProjectEpisodes(inner)

    asyncio.run(episodes.write_task_episode(object(), _Episode("default-project")))

    assert inner.written[0].project_id is None


def test_a_real_project_id_is_left_alone() -> None:
    # Production resolves the sentinel to a real UUID before the episode is
    # built, and that value must reach the database untouched.
    inner = _RecordingEpisodes()
    episodes = NullDefaultProjectEpisodes(inner)
    real = "0b6d5f2c-6f3a-4a1e-9c3b-7f2f5a1d9e44"

    asyncio.run(episodes.write_task_episode(object(), _Episode(real)))

    assert inner.written[0].project_id == real


def test_an_absent_project_id_is_left_alone() -> None:
    inner = _RecordingEpisodes()
    episodes = NullDefaultProjectEpisodes(inner)

    asyncio.run(episodes.write_task_episode(object(), _Episode(None)))

    assert inner.written[0].project_id is None


def test_other_methods_delegate_untouched() -> None:
    # The wrapper must stay transparent, or it silently narrows the repository
    # the evaluation is supposed to be measuring.
    inner = _RecordingEpisodes()
    episodes = NullDefaultProjectEpisodes(inner)

    assert asyncio.run(episodes.read_episodes()) == ()
    assert inner.calls == ["read_episodes"]
