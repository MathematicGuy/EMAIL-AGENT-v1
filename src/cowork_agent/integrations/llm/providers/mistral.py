"""Mistral OpenAI-compatible adapters for classification and action plans."""

import asyncio
import json
import logging
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from langfuse import observe

from cowork_agent.config import MistralSettings
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

from .gemini import (
    CLASSIFICATION_SCHEMA,
    CLASSIFIER_REPAIR_INSTRUCTION,
    CLASSIFIER_SYSTEM_INSTRUCTION,
    EMAIL_INTENT_PROMPT_VERSION,
    GENERATION_SCHEMA,
    GENERATOR_SYSTEM_INSTRUCTION,
    _build_generation_prompt,
    _build_prompt,
    _classified_messages_for,
    _generate_with_schema_repair,
    _parse_action_plan_output,
    _task_source_links,
    _update_current_generation,
    _update_current_span,
    _validated_decisions,
)

MISTRAL_CHAT_COMPLETIONS_URL = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_USER_AGENT = "module-mail/0.1.0"

_CLASSIFIER_LOGGER = logging.getLogger(__name__)
_Thread = tuple[EphemeralEmailEnvelope, ...]


class MistralAPIError(RuntimeError):
    """Mistral returned an error or an unusable completion."""

    error_code = "MISTRAL_API_ERROR"

    def __init__(self, detail: str, *, safe_message: str | None = None) -> None:
        super().__init__(detail)
        self.safe_message = safe_message or (
            "Mistral không thể phân tích email. Vui lòng kiểm tra cấu hình model và thử lại."
        )


class MistralActionPlanGenerator:
    """ActionPlanGeneratorPort adapter for the Mistral chat-completions API."""

    def __init__(self, settings: MistralSettings) -> None:
        self._settings = settings

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
            raise MistralAPIError(
                "Mistral response did not match the generation schema",
                safe_message=(
                    "Mistral trả về dữ liệu không đúng cấu trúc task yêu cầu. "
                    "Vui lòng thử lại hoặc kiểm tra schema generation."
                ),
            ) from exc

    async def _complete(self, prompt: str) -> Mapping[str, Any]:
        response = await asyncio.to_thread(
            _post_json,
            MISTRAL_CHAT_COMPLETIONS_URL,
            self._settings.api_key,
            _request_body(
                self._settings.model,
                GENERATOR_SYSTEM_INSTRUCTION,
                prompt,
                GENERATION_SCHEMA,
                self._settings.max_output_tokens,
            ),
            self._settings.timeout_seconds,
        )
        return _completion_json(response)


class MistralRouteClassifier:
    """RouteClassifierPort adapter with conservative fallback."""

    def __init__(self, settings: MistralSettings) -> None:
        self._settings = settings

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
            metadata={
                "feature": "email-intent-router",
                "provider": "mistral",
            },
        )
        for batch in batch_messages(group_by_thread(messages), self._settings.max_emails_per_batch):
            batch_ids = tuple(message.gmail_message_id for thread in batch for message in thread)
            if not batch_ids:
                continue
            batch_count += 1
            classified.extend(
                await self._classify_batch(user_timezone, current_time, batch, batch_ids)
            )
        result = ClassificationResult(tuple(classified), batch_count)
        _update_current_span(
            output_data={
                "classified_count": len(result.decisions),
                "batch_count": result.batch_count,
                "fallback_count": sum(1 for item in result.decisions if item.is_fallback),
            },
        )
        return result

    async def _classify_batch(
        self,
        user_timezone: str,
        current_time: datetime,
        threads: Sequence[_Thread],
        batch_ids: tuple[str, ...],
    ) -> tuple[ClassifiedMessage, ...]:
        expected = frozenset(batch_ids)
        prompt = _build_prompt(user_timezone, current_time, threads)
        trace_input = {
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
            _CLASSIFIER_LOGGER.warning(
                "Mistral classifier fallback for %d of %d batch messages",
                len(expected - decisions.keys()),
                len(batch_ids),
            )
        return _classified_messages_for(batch_ids, decisions)

    @observe(
        as_type="generation",
        name="email-intent-llm-call",
        capture_input=False,
        capture_output=False,
    )
    async def _complete(
        self,
        prompt: str,
        *,
        trace_input: Mapping[str, object] | None = None,
    ) -> Mapping[str, Any] | None:
        try:
            response = await asyncio.to_thread(
                _post_json,
                MISTRAL_CHAT_COMPLETIONS_URL,
                self._settings.api_key,
                _request_body(
                    self._settings.model,
                    CLASSIFIER_SYSTEM_INSTRUCTION,
                    prompt,
                    CLASSIFICATION_SCHEMA,
                    self._settings.max_output_tokens,
                ),
                self._settings.timeout_seconds,
            )
            payload = _completion_json(response)
            _update_current_generation(
                input_data=trace_input,
                output_data={
                    "response_type": "structured_json",
                    "top_level_fields": sorted(str(field) for field in payload),
                },
                metadata={
                    "provider": "mistral",
                    "prompt_version": EMAIL_INTENT_PROMPT_VERSION,
                },
                model=self._settings.model,
            )
            return payload
        except MistralAPIError as exc:
            _CLASSIFIER_LOGGER.warning("Mistral classifier transport failed: %s", exc.error_code)
            return None


def _request_body(
    model: str,
    system_instruction: str,
    prompt: str,
    schema: Mapping[str, object],
    max_output_tokens: int,
) -> dict[str, object]:
    task_requested = (
        '"task_proposal_requested": true' in prompt
        or '"task_proposal_requested":true' in prompt
    )
    if task_requested:
        user_content = (
            f"{prompt}\n"
            "CRITICAL: Since task_proposal_requested is true, "
            "you MUST populate task_proposal with a full object (NOT null).\n"
            f"Return only a valid JSON object matching this schema exactly:\n"
            f"{json.dumps(schema, ensure_ascii=False)}"
        )
    else:
        user_content = (
            f"{prompt}\nReturn only a valid JSON object matching this schema exactly:\n"
            f"{json.dumps(schema, ensure_ascii=False)}"
        )
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.0,
        "max_tokens": max_output_tokens,
        "response_format": {"type": "json_object"},
    }


def _completion_json(response: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        content = response["choices"][0]["message"]["content"]
    except (IndexError, KeyError, TypeError) as exc:
        raise MistralAPIError("Mistral response did not contain a chat completion") from exc
    try:
        payload = json.loads(str(content))
    except json.JSONDecodeError as exc:
        raise MistralAPIError("Mistral response was not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise MistralAPIError("Mistral response JSON must be an object")
    return cast(Mapping[str, Any], payload)


def _post_json(
    url: str, api_key: str, body: Mapping[str, object], timeout_seconds: int
) -> Mapping[str, Any]:
    request = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": MISTRAL_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 -- fixed HTTPS URL
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise MistralAPIError(
            f"Mistral API returned HTTP {exc.code}",
            safe_message=(
                f"Mistral từ chối yêu cầu (HTTP {exc.code}). Vui lòng kiểm tra model rồi thử lại."
            ),
        ) from exc
    except (TimeoutError, URLError) as exc:
        raise MistralAPIError(
            "Mistral API request failed",
            safe_message=(
                "Không thể kết nối tới Mistral hoặc yêu cầu đã hết thời gian chờ. Vui lòng thử lại."
            ),
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MistralAPIError("Mistral API response was not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise MistralAPIError("Mistral API response must be a JSON object")
    return cast(Mapping[str, Any], payload)
