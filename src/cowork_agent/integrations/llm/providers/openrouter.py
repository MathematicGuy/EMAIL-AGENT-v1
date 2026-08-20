"""OpenRouter OpenAI-compatible adapters for classification and action plans."""

import asyncio
import json
import logging
from collections.abc import Mapping, Sequence
from datetime import datetime
from http.client import IncompleteRead
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from langfuse import observe

from cowork_agent.config import OpenRouterSettings
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

OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_USER_AGENT = "module-mail/0.1.0"
OPENROUTER_SITE_URL = "https://github.com/cowork-agent"
OPENROUTER_SITE_NAME = "Cowork Agent"

_CLASSIFIER_LOGGER = logging.getLogger(__name__)
_Thread = tuple[EphemeralEmailEnvelope, ...]


class OpenRouterAPIError(RuntimeError):
    """OpenRouter returned an error or an unusable completion."""

    error_code = "OPENROUTER_API_ERROR"

    def __init__(self, detail: str, *, safe_message: str | None = None) -> None:
        super().__init__(detail)
        self.safe_message = safe_message or "OpenRouter could not process the email."


class OpenRouterActionPlanGenerator:
    """ActionPlanGeneratorPort adapter for the OpenRouter chat-completions API."""

    def __init__(self, settings: OpenRouterSettings) -> None:
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
            raise OpenRouterAPIError(
                "OpenRouter response did not match the generation schema",
                safe_message="OpenRouter returned invalid task schema.",
            ) from exc

    async def _complete(self, prompt: str) -> Mapping[str, Any]:
        return await execute_chat_completion(
            self._settings.api_key,
            self._settings.model,
            GENERATOR_SYSTEM_INSTRUCTION,
            prompt,
            GENERATION_SCHEMA,
            self._settings.max_output_tokens,
            self._settings.timeout_seconds,
        )


class OpenRouterRouteClassifier:
    """RouteClassifierPort adapter with the existing conservative fallback."""

    def __init__(self, settings: OpenRouterSettings) -> None:
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
                "provider": "openrouter",
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
            }
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
                "OpenRouter classifier fallback for %d of %d batch messages",
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
            payload = await execute_chat_completion(
                self._settings.api_key,
                self._settings.model,
                CLASSIFIER_SYSTEM_INSTRUCTION,
                prompt,
                CLASSIFICATION_SCHEMA,
                self._settings.max_output_tokens,
                self._settings.timeout_seconds,
            )
        except OpenRouterAPIError as exc:
            _CLASSIFIER_LOGGER.warning("OpenRouter classifier transport failed: %s", exc.error_code)
            return None
        _update_current_generation(
            input_data=trace_input,
            output_data={
                "response_type": "structured_json",
                "top_level_fields": sorted(str(field) for field in payload),
            },
            metadata={
                "provider": "openrouter",
                    "prompt_version": EMAIL_INTENT_PROMPT_VERSION,
            },
            model=self._settings.model,
        )
        return payload


async def execute_chat_completion(
    api_key: str,
    model: str,
    system_instruction: str,
    prompt: str,
    schema: Mapping[str, object],
    max_output_tokens: int,
    timeout_seconds: int,
) -> Mapping[str, Any]:
    response = await asyncio.to_thread(
        _post_json,
        OPENROUTER_CHAT_COMPLETIONS_URL,
        api_key,
        _request_body(model, system_instruction, prompt, schema, max_output_tokens),
        timeout_seconds,
    )
    return _completion_json(response)


def _request_body(
    model: str,
    system_instruction: str,
    prompt: str,
    schema: Mapping[str, object],
    max_output_tokens: int,
) -> dict[str, object]:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system_instruction},
            {
                "role": "user",
                "content": (
                    f"{prompt}\nReturn only a valid JSON object matching this schema exactly:\n"
                    f"{json.dumps(schema, ensure_ascii=False)}"
                ),
            },
        ],
        "temperature": 0.7,
        "max_tokens": max_output_tokens,
        "response_format": {"type": "json_object"},
    }


def _completion_json(response: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        content = response["choices"][0]["message"]["content"]
    except (IndexError, KeyError, TypeError) as exc:
        raise OpenRouterAPIError("OpenRouter response did not contain a chat completion") from exc
    try:
        payload = json.loads(str(content))
    except json.JSONDecodeError as exc:
        raise OpenRouterAPIError("OpenRouter response was not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise OpenRouterAPIError("OpenRouter response JSON must be an object")
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
            "User-Agent": OPENROUTER_USER_AGENT,
            "HTTP-Referer": OPENROUTER_SITE_URL,
            "X-Title": OPENROUTER_SITE_NAME,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 -- fixed HTTPS URL
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise OpenRouterAPIError(
            f"OpenRouter API returned HTTP {exc.code}",
            safe_message=f"OpenRouter rejected the request (HTTP {exc.code}).",
        ) from exc
    except (TimeoutError, URLError, IncompleteRead, OSError) as exc:
        raise OpenRouterAPIError(
            "OpenRouter API request failed",
            safe_message="Could not connect to OpenRouter or the request timed out.",
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpenRouterAPIError("OpenRouter API response was not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise OpenRouterAPIError("OpenRouter API response must be a JSON object")
    return cast(Mapping[str, Any], payload)
