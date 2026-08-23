"""Vyce (VyceAI) adapters for classification and action plans with key rotation."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from langfuse import observe

from cowork_agent.config import VyceSettings
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
from cowork_agent.features.email_action_plan.shaping import (
    batch_messages,
    group_by_thread,
)

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

VYCE_USER_AGENT = "module-mail/0.1.0"

_CLASSIFIER_LOGGER = logging.getLogger(__name__)

_Thread = tuple[EphemeralEmailEnvelope, ...]


class VyceAPIError(RuntimeError):
    """Vyce returned an error or an unusable completion."""

    error_code = "VYCE_API_ERROR"

    def __init__(self, detail: str, *, safe_message: str | None = None) -> None:
        super().__init__(detail)
        self.safe_message = safe_message or (
            "Vyce không thể phân tích email. Vui lòng kiểm tra cấu hình model và thử lại."
        )


class VyceRateLimitError(VyceAPIError):
    """Vyce API rate-limit reached for a key."""

    error_code = "VYCE_RATE_LIMIT_ERROR"


class VyceGatewayError(VyceAPIError):
    """Vyce API transient gateway error (500, 502, 503, 504, timeout)."""

    error_code = "VYCE_GATEWAY_ERROR"


class VyceActionPlanGenerator:
    """ActionPlanGeneratorPort adapter for Vyce with key rotation."""

    def __init__(self, settings: VyceSettings) -> None:
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
            raise VyceAPIError(
                "Vyce response did not match the generation schema",
                safe_message=(
                    "Vyce trả về dữ liệu không đúng cấu trúc task yêu cầu. "
                    "Vui lòng thử lại hoặc kiểm tra schema generation."
                ),
            ) from exc

    async def _complete(self, prompt: str) -> Mapping[str, Any]:
        return await execute_chat_completion(
            self._settings,
            GENERATOR_SYSTEM_INSTRUCTION,
            prompt,
            GENERATION_SCHEMA,
        )


class VyceRouteClassifier:
    """RouteClassifierPort adapter for Vyce with key rotation."""

    def __init__(self, settings: VyceSettings) -> None:
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
                "provider": "vyce",
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
        batch_ids: Sequence[str],
    ) -> tuple[ClassifiedMessage, ...]:
        prompt = _build_prompt(user_timezone, current_time, threads)
        payload = await self._post_classifier_payload(prompt)
        decisions = _validated_decisions(payload, frozenset(batch_ids))
        missing_ids = [msg_id for msg_id in batch_ids if msg_id not in decisions]
        if missing_ids:
            repaired_payload = await self._post_classifier_payload(
                prompt + CLASSIFIER_REPAIR_INSTRUCTION
            )
            decisions.update(_validated_decisions(repaired_payload, frozenset(missing_ids)))
        return _classified_messages_for(batch_ids, decisions)

    async def _post_classifier_payload(self, prompt: str) -> Mapping[str, Any] | None:
        try:
            payload = await execute_chat_completion(
                self._settings,
                CLASSIFIER_SYSTEM_INSTRUCTION,
                prompt,
                CLASSIFICATION_SCHEMA,
            )
            _update_current_generation(
                input_data={
                    "prompt_version": EMAIL_INTENT_PROMPT_VERSION,
                },
                metadata={
                    "feature": "email-intent-router",
                    "provider": "vyce",
                },
                model=self._settings.model,
            )
            return payload
        except VyceAPIError as exc:
            _CLASSIFIER_LOGGER.warning("Vyce classifier transport failed: %s", exc.error_code)
            return None


async def execute_chat_completion(
    settings: VyceSettings,
    system_instruction: str,
    prompt: str,
    schema: Mapping[str, object],
) -> Mapping[str, Any]:
    """Execute chat completion with Gemini-style immediate key rotation (0.5s pause on failure)."""
    url = f"{settings.base_url.rstrip('/')}/chat/completions"
    keys = await settings.rotator.candidates(settings.max_attempts)
    if not keys:
        raise VyceAPIError("No active Vyce API keys available")

    last_error: Exception | None = None
    body = _request_body(
        settings.model,
        system_instruction,
        prompt,
        schema,
        settings.max_output_tokens,
    )

    for key in keys:
        try:
            response = await asyncio.to_thread(
                _post_json,
                url,
                key,
                body,
                settings.timeout_seconds,
            )
            return _completion_json(response)
        except VyceRateLimitError as exc:
            last_error = exc
            if not settings.rotate_on_rate_limit:
                raise
            _CLASSIFIER_LOGGER.warning(
                "Vyce rate limit hit, rotating key: %s", exc
            )
            await asyncio.sleep(0.5)
            continue
        except Exception as exc:
            last_error = exc
            if not settings.rotate_on_rate_limit:
                raise
            _CLASSIFIER_LOGGER.warning(
                "Vyce request failed, rotating key: %s", exc
            )
            await asyncio.sleep(0.5)
            continue

    if isinstance(last_error, VyceAPIError):
        raise last_error
    raise VyceAPIError(
        f"All Vyce candidate API keys failed: {last_error}"
    ) from last_error


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
    schema_json = json.dumps(schema, ensure_ascii=False)
    system_content = (
        f"{system_instruction}\n\n"
        "CRITICAL JSON FORMAT REQUIREMENT:\n"
        "You must ALWAYS return a valid JSON object matching this schema exactly:\n"
        f"{schema_json}\n"
        "Never output raw plain text. Even for refusals, missing information, or generic replies, "
        "you MUST encapsulate the response inside the JSON object."
    )
    if task_requested:
        user_content = (
            f"{prompt}\n"
            "CRITICAL: Since task_proposal_requested is true, "
            "you MUST populate task_proposal with a full object (NOT null).\n"
            f"Return only a valid JSON object matching this schema exactly:\n"
            f"{schema_json}"
        )
    else:
        user_content = (
            f"{prompt}\nReturn only a valid JSON object matching this schema exactly:\n"
            f"{schema_json}"
        )
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.0,
        "max_tokens": max_output_tokens,
        "response_format": {"type": "json_object"},
    }


def _completion_json(response: Mapping[str, Any]) -> Mapping[str, Any]:
    """Parse and normalize completion using Instructor-style schema coercion."""
    try:
        content = response["choices"][0]["message"]["content"]
    except (IndexError, KeyError, TypeError) as exc:
        raise VyceAPIError("Vyce response did not contain a chat completion") from exc
    content_str = str(content).strip()
    if content_str.startswith("```"):
        lines = content_str.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        content_str = "\n".join(lines).strip()

    try:
        payload = json.loads(content_str)
        if isinstance(payload, Mapping):
            normalized: dict[str, Any] = dict(payload)
            if "assistant_text" in normalized:
                normalized.setdefault("conversation_title", "Phản hồi")
                normalized.setdefault("citation_ids", [])
                normalized.setdefault("task_proposal", None)
            return normalized
    except json.JSONDecodeError:
        pass

    # Instructor normalization: Coerce raw plain text into structured schema
    if content_str:
        first_line = content_str.splitlines()[0] if content_str.splitlines() else "Phản hồi"
        title = first_line[:40] if len(first_line) <= 40 else f"{first_line[:37]}..."
        return {
            "assistant_text": content_str,
            "conversation_title": title,
            "citation_ids": [],
            "task_proposal": None,
        }

    raise VyceAPIError("Vyce returned an empty response")


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
            "User-Agent": VYCE_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 -- fixed HTTPS URL
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 429:
            raise VyceRateLimitError(
                "Vyce API returned HTTP 429 rate limit",
                safe_message="Vyce vượt quá giới hạn tần suất gọi API (HTTP 429).",
            ) from exc
        if exc.code in (500, 502, 503, 504):
            raise VyceGatewayError(
                f"Vyce API returned HTTP {exc.code} gateway error",
                safe_message=f"Vyce gặp sự cố máy chủ tạm thời (HTTP {exc.code}).",
            ) from exc
        raise VyceAPIError(
            f"Vyce API returned HTTP {exc.code}",
            safe_message=(
                f"Vyce từ chối yêu cầu (HTTP {exc.code}). Vui lòng kiểm tra model rồi thử lại."
            ),
        ) from exc
    except (TimeoutError, URLError) as exc:
        raise VyceGatewayError(
            f"Vyce API request failed: {exc}",
            safe_message=(
                "Không thể kết nối tới Vyce hoặc yêu cầu đã hết thời gian chờ."
            ),
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VyceGatewayError("Vyce API response was not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise VyceAPIError("Vyce API response must be a JSON object")
    return cast(Mapping[str, Any], payload)


# Backwards compatibility aliases
VYNE_USER_AGENT = VYCE_USER_AGENT
VyneAPIError = VyceAPIError
VyneRateLimitError = VyceRateLimitError
VyneGatewayError = VyceGatewayError
VyneActionPlanGenerator = VyceActionPlanGenerator
VyneRouteClassifier = VyceRouteClassifier
