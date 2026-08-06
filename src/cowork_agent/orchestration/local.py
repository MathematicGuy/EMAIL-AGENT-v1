"""In-memory queue and outbox adapters for local execution."""

from collections.abc import Sequence

from cowork_agent.domain import DigestCompletedEvent


class InMemoryQueue:
    def __init__(self) -> None:
        self.run_ids: list[str] = []

    async def enqueue_digest_run(self, run_id: str) -> None:
        if run_id not in self.run_ids:
            self.run_ids.append(run_id)


class InMemoryOutbox:
    def __init__(self) -> None:
        self.events: dict[str, DigestCompletedEvent] = {}
        self.published: set[str] = set()

    async def add(self, event: DigestCompletedEvent) -> None:
        self.events.setdefault(event.run_id, event)

    async def pending(self) -> Sequence[DigestCompletedEvent]:
        return tuple(event for key, event in self.events.items() if key not in self.published)

    async def mark_published(self, run_id: str) -> None:
        self.published.add(run_id)
