"""Deep Email Action Plan base classes: workflow lives here, transport does not."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from langfuse import observe

from cowork_agent.domain.target_contracts import (
    ActionPlanOutput,
    EphemeralEmailEnvelope,
    SemanticRetrievalResponse,
)
from cowork_agent.features.email_action_plan.correlation import TaskCandidate
from cowork_agent.features.email_action_plan.routing import RouteResolution
from cowork_agent.features.email_action_plan.schemas import (
    ClassificationResult,
    ClassifiedMessage,
    GenerationContext,
)
from cowork_agent.features.email_action_plan.shaping import batch_messages, group_by_thread

from .parsers import (
    _classified_messages_for,
    _generate_with_schema_repair,
    _parse_action_plan_output,
    _task_source_links,
    _validated_decisions,
)
from .prompts import (
    CLASSIFIER_REPAIR_INSTRUCTION,
    EMAIL_INTENT_PROMPT_VERSION,
    _build_generation_prompt,
    _build_prompt,
)
from .tracing import _update_current_span

_LOGGER = logging.getLogger(__name__)
_Thread = tuple[EphemeralEmailEnvelope, ...]


class ConfiguredRouteClassifier(ABC):
    """Batch classification with one schema-repair retry and conservative fallback.

    Subclasses implement ``_complete`` (provider transport). This module owns the
    batch loop, Langfuse span, repair retry, and fallback filling.
    """

    def __init__(self, *, provider_name: str, max_emails_per_batch: int) -> None:
        self._provider_name = provider_name
        self._max_emails_per_batch = max_emails_per_batch

    @observe(
        as_type="span",
        name="classify-email-intent",
        capture_input=False,
        capture_output=False,
    )
    async def classify(
        self,
        user_timezone: str,
        current_time: datetime,
        messages: Sequence[EphemeralEmailEnvelope],
    ) -> ClassificationResult:
        classified: list[ClassifiedMessage] = []
        batch_count = 0
        _update_current_span(
            input_data={
                "message_count": len(messages),
                "prompt_version": EMAIL_INTENT_PROMPT_VERSION,
            },
            metadata=self._classify_span_metadata(messages),
        )
        for batch in batch_messages(group_by_thread(messages), self._max_emails_per_batch):
            batch_ids = tuple(message.gmail_message_id for thread in batch for message in thread)
            if not batch_ids:
                continue
            batch_count += 1
            classified.extend(
                await self._classify_batch(user_timezone, current_time, batch, batch_ids)
            )
        result = ClassificationResult(
            tuple(classified),
            batch_count,
            await self._filtered_summary(messages, classified),
        )
        _update_current_span(
            output_data={
                "classified_count": len(result.decisions),
                "batch_count": result.batch_count,
                "fallback_count": sum(1 for item in result.decisions if item.is_fallback),
            },
            metadata=self._classify_output_metadata(result),
        )
        return result

    def _classify_span_metadata(
        self, messages: Sequence[EphemeralEmailEnvelope]
    ) -> Mapping[str, object]:
        return {
            "feature": "email-intent-router",
            "provider": self._provider_name,
        }

    def _classify_output_metadata(
        self, result: ClassificationResult
    ) -> Mapping[str, object] | None:
        del result
        return None

    async def _filtered_summary(
        self,
        messages: Sequence[EphemeralEmailEnvelope],
        classified: Sequence[ClassifiedMessage],
    ) -> str | None:
        del messages, classified
        return None

    async def _classify_batch(
        self,
        user_timezone: str,
        current_time: datetime,
        threads: Sequence[_Thread],
        batch_ids: tuple[str, ...],
    ) -> tuple[ClassifiedMessage, ...]:
        expected = frozenset(batch_ids)
        prompt = _build_prompt(user_timezone, current_time, threads)
        trace_input: dict[str, object] = {
            "operation": "classify-email-intent",
            "message_count": len(batch_ids),
            "prompt_version": EMAIL_INTENT_PROMPT_VERSION,
        }
        decisions = _validated_decisions(
            await self._complete(prompt, trace_input=trace_input), expected
        )
        if not expected <= decisions.keys():
            repaired = _validated_decisions(
                await self._complete(
                    prompt + CLASSIFIER_REPAIR_INSTRUCTION,
                    trace_input={**trace_input, "repair_attempt": True},
                ),
                expected,
            )
            decisions = {**repaired, **decisions}
        if not expected <= decisions.keys():
            missing = sorted(expected - decisions.keys())
            _LOGGER.warning(
                "%s classifier fallback for %d of %d batch messages: %s",
                self._provider_name,
                len(missing),
                len(batch_ids),
                missing,
            )
        return _classified_messages_for(batch_ids, decisions)

    @abstractmethod
    async def _complete(
        self,
        prompt: str,
        *,
        trace_input: Mapping[str, object] | None = None,
    ) -> Mapping[str, Any] | None:
        """Provider transport. Return parsed JSON, or None on a transport failure."""


class ConfiguredActionPlanGenerator(ABC):
    """Action-plan generation with one schema-repair retry.

    Subclasses implement ``_complete`` (provider transport) and
    ``_schema_error`` (provider-safe exception wrapping).
    """

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
        prompt = _build_generation_prompt(
            user_timezone, current_time, envelopes, candidate, resolution, retrieval
        )
        try:
            return await _generate_with_schema_repair(
                self._complete,
                prompt,
                lambda payload: _parse_action_plan_output(
                    payload,
                    run_context=run_context,
                    candidate=candidate,
                    first_envelope=envelopes[0],
                    source_links=_task_source_links(envelopes, candidate.source_message_ids),
                    current_time=current_time,
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise self._schema_error() from exc

    @abstractmethod
    def _schema_error(self) -> Exception:
        """Provider-specific error raised after the schema-repair retry fails."""

    @abstractmethod
    async def _complete(self, prompt: str) -> Mapping[str, Any]:
        """Provider transport. Return parsed JSON or raise a transport error."""
