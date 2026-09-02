"""Mistral OpenAI-compatible adapters for classification and action plans."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from email.message import Message
from typing import Any
from urllib.error import HTTPError, URLError

from langfuse import observe

from cowork_agent.config import MistralSettings
from cowork_agent.domain.target_contracts import (
    EphemeralEmailEnvelope,
)

from .base import ConfiguredActionPlanGenerator, ConfiguredRouteClassifier
from .openai_transport import openai_completion_json, openai_request_body, post_json
from .prompts import (
    CLASSIFICATION_SCHEMA,
    CLASSIFIER_SYSTEM_INSTRUCTION,
    EMAIL_INTENT_PROMPT_VERSION,
    GENERATION_SCHEMA,
    GENERATOR_SYSTEM_INSTRUCTION,
)
from .tracing import _update_current_generation

MISTRAL_CHAT_COMPLETIONS_URL = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_USER_AGENT = "module-mail/0.1.0"

_CLASSIFIER_LOGGER = logging.getLogger(__name__)
_Thread = tuple[EphemeralEmailEnvelope, ...]


class MistralAPIError(RuntimeError):
    """Mistral returned an error or an unusable completion."""

    error_code = "MISTRAL_API_ERROR"

    def __init__(
        self,
        detail: str,
        *,
        safe_message: str | None = None,
        status_code: int | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(detail)
        self.safe_message = safe_message or (
            "Mistral không thể phân tích email. Vui lòng kiểm tra cấu hình model và thử lại."
        )
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class MistralActionPlanGenerator(ConfiguredActionPlanGenerator):
    """ActionPlanGeneratorPort adapter for the Mistral chat-completions API."""

    def __init__(self, settings: MistralSettings) -> None:
        self._settings = settings

    def _schema_error(self) -> Exception:
        return MistralAPIError(
            "Mistral response did not match the generation schema",
            safe_message=(
                "Mistral trả về dữ liệu không đúng cấu trúc task yêu cầu. "
                "Vui lòng thử lại hoặc kiểm tra schema generation."
            ),
        )

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


class MistralRouteClassifier(ConfiguredRouteClassifier):
    """RouteClassifierPort adapter with conservative fallback."""

    def __init__(self, settings: MistralSettings) -> None:
        super().__init__(
            provider_name="mistral",
            max_emails_per_batch=settings.max_emails_per_batch,
        )
        self._settings = settings

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
    *,
    reasoning_effort: str | None = None,
) -> dict[str, object]:
    body = openai_request_body(
        model,
        system_instruction,
        prompt,
        schema,
        max_output_tokens,
        task_proposal_guard=True,
    )
    if reasoning_effort is not None:
        body["reasoning_effort"] = reasoning_effort
        if reasoning_effort == "high":
            body["top_p"] = 1.0
    return body


def _completion_json(response: Mapping[str, Any]) -> Mapping[str, Any]:
    return openai_completion_json(
        response,
        error_cls=MistralAPIError,
        missing_completion="Mistral response did not contain a chat completion",
        invalid_json="Mistral response was not valid JSON",
        not_object="Mistral response JSON must be an object",
    )


def _post_json(
    url: str, api_key: str, body: Mapping[str, object], timeout_seconds: int
) -> Mapping[str, Any]:
    try:
        return post_json(url, api_key, body, timeout_seconds, user_agent=MISTRAL_USER_AGENT)
    except HTTPError as exc:
        raise MistralAPIError(
            f"Mistral API returned HTTP {exc.code}",
            safe_message=(
                f"Mistral từ chối yêu cầu (HTTP {exc.code}). Vui lòng kiểm tra model rồi thử lại."
            ),
            status_code=exc.code,
            retry_after_seconds=_retry_after_seconds(exc.headers),
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
    except ValueError as exc:
        raise MistralAPIError("Mistral API response must be a JSON object") from exc


def _retry_after_seconds(headers: Message[str, str] | None) -> int | None:
    """Extract only a non-negative integer Retry-After value from provider headers."""

    if headers is None:
        return None
    raw = headers.get("Retry-After")
    if raw is None:
        return None
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None
