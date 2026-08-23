"""OpenRouter OpenAI-compatible adapters for classification and action plans."""

import asyncio
import json
import logging
from collections.abc import Mapping, Sequence
from http.client import IncompleteRead
from typing import Any
from urllib.error import HTTPError, URLError

from langfuse import observe

from cowork_agent.config import GeminiSettings, OpenRouterSettings
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


class OpenRouterActionPlanGenerator(ConfiguredActionPlanGenerator):
    """ActionPlanGeneratorPort adapter for the OpenRouter chat-completions API."""

    def __init__(
        self, settings: OpenRouterSettings, last_resort: GeminiSettings | None = None
    ) -> None:
        self._settings = settings
        self._last_resort = last_resort

    def _schema_error(self) -> Exception:
        return OpenRouterAPIError(
            "OpenRouter response did not match the generation schema",
            safe_message="OpenRouter returned invalid task schema.",
        )

    async def _complete(self, prompt: str) -> Mapping[str, Any]:
        from cowork_agent.integrations.llm.last_resort import (
            complete_with_gemini_last_resort,
            gemini_json_complete,
        )

        async def primary() -> Mapping[str, Any]:
            return await execute_chat_completion(
                self._settings.api_key,
                self._settings.model,
                GENERATOR_SYSTEM_INSTRUCTION,
                prompt,
                GENERATION_SCHEMA,
                self._settings.max_output_tokens,
                self._settings.timeout_seconds,
                fallback_models=self._settings.fallback_models(),
            )

        last_resort = self._last_resort
        if last_resort is None:
            fallback = None
        else:

            async def fallback(
                _prompt: str = prompt,
                _last_resort: GeminiSettings = last_resort,
            ) -> Mapping[str, Any]:
                return await gemini_json_complete(
                    _last_resort,
                    _prompt,
                    GENERATION_SCHEMA,
                    GENERATOR_SYSTEM_INSTRUCTION,
                    timeout_seconds=self._settings.timeout_seconds,
                )

        return await complete_with_gemini_last_resort(primary, fallback)


class OpenRouterRouteClassifier(ConfiguredRouteClassifier):
    """RouteClassifierPort adapter with the existing conservative fallback."""

    def __init__(
        self, settings: OpenRouterSettings, last_resort: GeminiSettings | None = None
    ) -> None:
        super().__init__(
            provider_name="openrouter",
            max_emails_per_batch=settings.max_emails_per_batch,
        )
        self._settings = settings
        self._last_resort = last_resort

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
        from cowork_agent.integrations.llm.last_resort import (
            complete_with_gemini_last_resort,
            gemini_json_complete,
        )

        async def primary() -> Mapping[str, Any]:
            return await execute_chat_completion(
                self._settings.api_key,
                self._settings.model,
                CLASSIFIER_SYSTEM_INSTRUCTION,
                prompt,
                CLASSIFICATION_SCHEMA,
                self._settings.max_output_tokens,
                self._settings.timeout_seconds,
                fallback_models=self._settings.fallback_models(),
            )

        last_resort = self._last_resort
        if last_resort is None:
            fallback = None
        else:

            async def fallback(
                _prompt: str = prompt,
                _last_resort: GeminiSettings = last_resort,
            ) -> Mapping[str, Any]:
                return await gemini_json_complete(
                    _last_resort,
                    _prompt,
                    CLASSIFICATION_SCHEMA,
                    CLASSIFIER_SYSTEM_INSTRUCTION,
                    timeout_seconds=self._settings.timeout_seconds,
                )

        try:
            payload = await complete_with_gemini_last_resort(primary, fallback)
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
    fallback_models: Sequence[str] = (),
) -> Mapping[str, Any]:
    response = await asyncio.to_thread(
        _post_json,
        OPENROUTER_CHAT_COMPLETIONS_URL,
        api_key,
        _request_body(
            model,
            system_instruction,
            prompt,
            schema,
            max_output_tokens,
            fallback_models=fallback_models,
        ),
        timeout_seconds,
    )
    return _completion_json(response)


def _request_body(
    model: str,
    system_instruction: str,
    prompt: str,
    schema: Mapping[str, object],
    max_output_tokens: int,
    fallback_models: Sequence[str] = (),
) -> dict[str, object]:
    return openai_request_body(
        model,
        system_instruction,
        prompt,
        schema,
        max_output_tokens,
        temperature=0.7,
        fallback_models=fallback_models,
    )


def _completion_json(response: Mapping[str, Any]) -> Mapping[str, Any]:
    return openai_completion_json(
        response,
        error_cls=OpenRouterAPIError,
        missing_completion="OpenRouter response did not contain a chat completion",
        invalid_json="OpenRouter response was not valid JSON",
        not_object="OpenRouter response JSON must be an object",
    )


def _post_json(
    url: str, api_key: str, body: Mapping[str, object], timeout_seconds: int
) -> Mapping[str, Any]:
    try:
        return post_json(
            url,
            api_key,
            body,
            timeout_seconds,
            extra_headers={
                "HTTP-Referer": OPENROUTER_SITE_URL,
                "X-Title": OPENROUTER_SITE_NAME,
            },
            user_agent=OPENROUTER_USER_AGENT,
        )
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
    except ValueError as exc:
        raise OpenRouterAPIError("OpenRouter API response must be a JSON object") from exc
