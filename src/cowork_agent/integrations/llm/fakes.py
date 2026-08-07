"""Deterministic LLM fakes for local tests."""

from collections.abc import Mapping, Sequence
from datetime import datetime

from cowork_agent.domain.target_contracts import (
    Actionability,
    EmailRouteDecision,
    EphemeralEmailEnvelope,
    ReasonCode,
    Route,
)
from cowork_agent.features.email_action_plan.schemas import (
    ClassificationResult,
    ClassifiedMessage,
    ExtractionBatch,
)

#: Default Route Decision for fake classification: actionable and
#: self-contained, resolving to DIRECT_PLAN through the Route Resolver.
DEFAULT_FAKE_DECISION = EmailRouteDecision(
    actionability=Actionability.ACTION_REQUIRED,
    route=Route.DIRECT_PLAN,
    candidate_action_item=None,
    email_is_sufficient=True,
    knowledge_gaps=(),
    retrieval_query=None,
    expected_document_types=(),
    reason_codes=(ReasonCode.EMAIL_SELF_CONTAINED,),
    confidence=0.9,
)


class FakeRouteClassifier:
    """Deterministic RouteClassifierPort fake: one decision per message."""

    def __init__(self, decisions: Mapping[str, EmailRouteDecision] | None = None) -> None:
        self._decisions = dict(decisions or {})
        self.received_envelopes: tuple[EphemeralEmailEnvelope, ...] = ()

    async def classify(
        self,
        user_timezone: str,
        current_time: datetime,
        messages: Sequence[EphemeralEmailEnvelope],
    ) -> ClassificationResult:
        del user_timezone, current_time
        self.received_envelopes = tuple(messages)
        return ClassificationResult(
            decisions=tuple(
                ClassifiedMessage(
                    gmail_message_id=message.gmail_message_id,
                    decision=self._decisions.get(message.gmail_message_id, DEFAULT_FAKE_DECISION),
                )
                for message in messages
            ),
            batch_count=1 if messages else 0,
        )


class FakePlanGenerator:
    """Deterministic ActionPlanGeneratorPort fake serving a canned batch.

    ``generate`` returns the canned batch filtered to the requested
    envelopes' Gmail message ids, preserving canned order — mirroring the
    one-call-per-candidate cardinality of the production pipeline.
    """

    def __init__(self, batch: ExtractionBatch) -> None:
        self.batch = batch
        self.received_envelopes: tuple[EphemeralEmailEnvelope, ...] = ()
        self.call_count = 0

    async def generate(
        self,
        user_timezone: str,
        current_time: datetime,
        messages: Sequence[EphemeralEmailEnvelope],
    ) -> ExtractionBatch:
        del user_timezone, current_time
        self.received_envelopes = tuple(messages)
        self.call_count += 1
        requested = {message.gmail_message_id for message in messages}
        return ExtractionBatch(
            emails=tuple(
                email for email in self.batch.emails if email.provider_message_id in requested
            )
        )
