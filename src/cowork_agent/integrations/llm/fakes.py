"""Deterministic LLM adapter for local tests."""

from collections.abc import Sequence
from datetime import datetime

from cowork_agent.domain.target_contracts import EphemeralEmailEnvelope
from cowork_agent.features.email_action_plan.schemas import ExtractionBatch


class FakeActionExtractor:
    def __init__(self, batch: ExtractionBatch) -> None:
        self.batch = batch
        self.received_envelopes: tuple[EphemeralEmailEnvelope, ...] = ()

    async def extract(
        self,
        user_timezone: str,
        current_time: datetime,
        messages: Sequence[EphemeralEmailEnvelope],
    ) -> ExtractionBatch:
        del user_timezone, current_time
        self.received_envelopes = tuple(messages)
        return self.batch
