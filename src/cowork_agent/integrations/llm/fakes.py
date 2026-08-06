"""Deterministic LLM adapter for local tests."""

from collections.abc import Sequence
from datetime import datetime

from cowork_agent.features.email_action_plan.schemas import ExtractionBatch, ThreadContext


class FakeActionExtractor:
    def __init__(self, batch: ExtractionBatch) -> None:
        self.batch = batch
        self.received_threads: tuple[ThreadContext, ...] = ()

    async def extract(
        self, user_timezone: str, current_time: datetime, threads: Sequence[ThreadContext]
    ) -> ExtractionBatch:
        del user_timezone, current_time
        self.received_threads = tuple(threads)
        return self.batch
