import asyncio


class Source:
    def __init__(self, identifiers: tuple[str, ...]) -> None:
        self.identifiers = list(identifiers)

    async def next_id(self) -> str | None:
        return self.identifiers.pop(0) if self.identifiers else None


class Executor:
    def __init__(self) -> None:
        self.executed: list[str] = []

    async def execute(self, identifier: str) -> None:
        self.executed.append(identifier)


def test_postgres_poller_executes_one_claimable_identifier() -> None:
    async def scenario() -> None:
        from cowork_agent.orchestration.postgres_poller import PostgresPoller

        source = Source(("document-1",))
        executor = Executor()
        poller = PostgresPoller(source, executor)

        assert await poller.poll_once() is True
        assert executor.executed == ["document-1"]

    asyncio.run(scenario())


def test_postgres_poller_is_idle_without_a_claimable_identifier() -> None:
    async def scenario() -> None:
        from cowork_agent.orchestration.postgres_poller import PostgresPoller

        executor = Executor()
        poller = PostgresPoller(Source(()), executor)

        assert await poller.poll_once() is False
        assert executor.executed == []

    asyncio.run(scenario())
