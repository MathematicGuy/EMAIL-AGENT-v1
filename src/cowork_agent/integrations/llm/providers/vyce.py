"""Vyce (VyceAI) adapters for classification and action plans with key rotation."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError, URLError

from cowork_agent.config import VyceSettings
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


class VyceActionPlanGenerator(ConfiguredActionPlanGenerator):
    """ActionPlanGeneratorPort adapter for Vyce with key rotation."""

    def __init__(self, settings: VyceSettings) -> None:
        self._settings = settings

    def _schema_error(self) -> Exception:
        return VyceAPIError(
            "Vyce response did not match the generation schema",
            safe_message=(
                "Vyce trả về dữ liệu không đúng cấu trúc task yêu cầu. "
                "Vui lòng thử lại hoặc kiểm tra schema generation."
            ),
        )

    async def _complete(self, prompt: str) -> Mapping[str, Any]:
        return await execute_chat_completion(
            self._settings,
            GENERATOR_SYSTEM_INSTRUCTION,
            prompt,
            GENERATION_SCHEMA,
        )


class VyceRouteClassifier(ConfiguredRouteClassifier):
    """RouteClassifierPort adapter for Vyce with key rotation."""

    def __init__(self, settings: VyceSettings) -> None:
        super().__init__(
            provider_name="vyce",
            max_emails_per_batch=settings.max_emails_per_batch,
        )
        self._settings = settings

    async def _complete(
        self,
        prompt: str,
        *,
        trace_input: Mapping[str, object] | None = None,
    ) -> Mapping[str, Any] | None:
        del trace_input
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
            _CLASSIFIER_LOGGER.warning("Vyce rate limit hit, rotating key: %s", exc)
            await asyncio.sleep(0.5)
            continue
        except Exception as exc:
            last_error = exc
            if not settings.rotate_on_rate_limit:
                raise
            _CLASSIFIER_LOGGER.warning("Vyce request failed, rotating key: %s", exc)
            await asyncio.sleep(0.5)
            continue

    if isinstance(last_error, VyceAPIError):
        raise last_error
    raise VyceAPIError(f"All Vyce candidate API keys failed: {last_error}") from last_error


def _request_body(
    model: str,
    system_instruction: str,
    prompt: str,
    schema: Mapping[str, object],
    max_output_tokens: int,
) -> dict[str, object]:
    return openai_request_body(
        model,
        system_instruction,
        prompt,
        schema,
        max_output_tokens,
        schema_in_system=True,
        task_proposal_guard=True,
    )


def _completion_json(response: Mapping[str, Any]) -> Mapping[str, Any]:
    """Parse and normalize completion using Instructor-style schema coercion."""
    return openai_completion_json(
        response,
        error_cls=VyceAPIError,
        missing_completion="Vyce response did not contain a chat completion",
        invalid_json="Vyce API response was not valid JSON",
        not_object="Vyce API response must be a JSON object",
        coerce_plain_text=True,
        empty_response="Vyce returned an empty response",
    )


def _post_json(
    url: str, api_key: str, body: Mapping[str, object], timeout_seconds: int
) -> Mapping[str, Any]:
    try:
        return post_json(url, api_key, body, timeout_seconds, user_agent=VYCE_USER_AGENT)
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
            safe_message=("Không thể kết nối tới Vyce hoặc yêu cầu đã hết thời gian chờ."),
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VyceGatewayError("Vyce API response was not valid JSON") from exc
    except ValueError as exc:
        raise VyceAPIError("Vyce API response must be a JSON object") from exc


# Backwards compatibility aliases
VYNE_USER_AGENT = VYCE_USER_AGENT
VyneAPIError = VyceAPIError
VyneRateLimitError = VyceRateLimitError
VyneGatewayError = VyceGatewayError
VyneActionPlanGenerator = VyceActionPlanGenerator
VyneRouteClassifier = VyceRouteClassifier
