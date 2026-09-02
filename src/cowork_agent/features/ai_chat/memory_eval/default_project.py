"""Make harness-built scopes writable against the PostgreSQL episode schema.

`ChatMemoryScope.project_id` defaults to the legacy sentinel `"default-project"`.
The running app never writes that value: `api/chat.py` resolves the caller's
default project through `PostgresProjectRepository`, and `chat_sessions.create`
returns a scope already carrying the real project UUID. The evaluation harness
has no workspace or project rows to resolve against, so it constructs its scopes
directly and inherits the sentinel.

`task_episodes.project_id` is `uuid` with a foreign key to `projects(id)`, so the
sentinel is rejected outright and every episodic write fails. SQLite stores the
same column untyped, which is why the evaluation never saw this while it ran
only on SQLite.

`None` is the honest value here: the harness has no project, and the column is
nullable precisely so an episode need not be project-scoped. The sentinel cannot
simply be dropped at the scope instead - `ChatMemoryScope` runs `project_id`
through `_require_key_component`, which rejects `None`.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

#: The placeholder `ChatMemoryScope` uses when no project has been resolved.
DEFAULT_PROJECT_SENTINEL = "default-project"


class NullDefaultProjectEpisodes:
    """Delegates to a task-episode repository, writing the sentinel as NULL.

    Every other method passes straight through: the point of the harness is to
    measure the real repository, so this wrapper must not narrow it.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def write_task_episode(self, namespace: Any, episode: Any, **kwargs: Any) -> Any:
        if getattr(episode, "project_id", None) == DEFAULT_PROJECT_SENTINEL:
            episode = replace(episode, project_id=None)
        return await self._inner.write_task_episode(namespace, episode, **kwargs)

    def __getattr__(self, name: str) -> Any:
        # Everything the repository exposes and this wrapper does not override.
        return getattr(self._inner, name)
