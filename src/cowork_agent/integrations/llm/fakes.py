"""Deterministic LLM fakes for local tests."""

from collections.abc import Mapping, Sequence
from datetime import datetime

from cowork_agent.domain.target_contracts import (
    Actionability,
    ActionPlanOutput,
    EmailRouteDecision,
    EphemeralEmailEnvelope,
    ReasonCode,
    Route,
    SemanticRetrievalResponse,
    Task,
    ValidationStatus,
)
from cowork_agent.features.email_action_plan.correlation import TaskCandidate
from cowork_agent.features.email_action_plan.routing import RouteResolution
from cowork_agent.features.email_action_plan.schemas import (
    ClassificationResult,
    ClassifiedMessage,
    GenerationContext,
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
    """Deterministic ActionPlanGeneratorPort fake serving canned Tasks.

    ``generate`` returns the canned Task whose ``gmail_message_id`` matches
    one of the candidate's source message ids (first canned match wins);
    otherwise it builds a deterministic default Task for the candidate.
    One call per candidate mirrors the production cardinality (frozen
    contract rule 6).
    """

    def __init__(self, tasks: tuple[Task, ...] = ()) -> None:
        self.tasks = tasks
        self.received_candidates: tuple[TaskCandidate, ...] = ()
        self.call_count = 0

    async def generate(
        self,
        *,
        user_timezone: str,
        current_time: datetime,
        run_context: GenerationContext,
        candidate: TaskCandidate,
        envelopes: Sequence[EphemeralEmailEnvelope],
        resolution: RouteResolution,
        retrieval: SemanticRetrievalResponse | None,
    ) -> ActionPlanOutput:
        del user_timezone, retrieval
        self.received_candidates += (candidate,)
        self.call_count += 1
        source_ids = frozenset(candidate.source_message_ids)
        for task in self.tasks:
            if task.gmail_message_id in source_ids:
                return ActionPlanOutput(task=task)
        first_message_id = candidate.source_message_ids[0]
        return ActionPlanOutput(
            task=Task(
                task_id=f"task_fake_{first_message_id}",
                run_id=run_context.run_id,
                gmail_message_id=first_message_id,
                gmail_url=envelopes[0].gmail_url,
                source_message_ids=candidate.source_message_ids,
                incident_key=candidate.incident_key,
                title="Công việc từ email",
                request_summary="Yêu cầu từ email.",
                actionability=Actionability.ACTION_REQUIRED,
                route=resolution.route,
                priority=None,
                deadline=None,
                action_plan=(),
                supporting_documents=(),
                missing_information=(),
                classifier_confidence=0.9,
                generation_confidence=0.9,
                validation_status=ValidationStatus.SYSTEM_GENERATED,
                created_at=current_time,
            )
        )
