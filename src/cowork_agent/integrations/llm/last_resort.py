"""OpenRouter-to-Gemini last-resort completion helper."""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from cowork_agent.config import GeminiSettings, load_runtime_environment
from cowork_agent.integrations.llm.providers.gemini import (
    GeminiKeyRotator,
    GeminiRateLimitError,
    GoogleGenAITransport,
)
from cowork_agent.integrations.llm.providers.openrouter import OpenRouterAPIError
from cowork_agent.integrations.llm.providers.tracing import _update_current_generation

_LOGGER = logging.getLogger(__name__)

Completion = Callable[[], Awaitable[Mapping[str, Any]]]


def _usable_numbered_gemini_keys(environ: Mapping[str, str]) -> tuple[str, ...]:
    numbered_keys = sorted(
        (int(name.removeprefix("GEMINI_API_KEY_")), value)
        for name, value in environ.items()
        if name.startswith("GEMINI_API_KEY_") and name.removeprefix("GEMINI_API_KEY_").isdecimal()
    )
    return tuple(
        value.strip()
        for _, value in numbered_keys
        if value.strip() and not value.strip().startswith("replace-with-")
    )


def load_optional_gemini_settings(
    environ: Mapping[str, str] | None = None,
    *,
    load_env_file: bool = True,
) -> GeminiSettings | None:
    if environ is None:
        if load_env_file:
            load_runtime_environment()
        environ = os.environ
    if not _usable_numbered_gemini_keys(environ):
        return None
    return GeminiSettings.from_env(environ, load_env_file=False)


async def complete_with_gemini_last_resort(
    primary: Completion,
    fallback: Completion | None,
) -> Mapping[str, Any]:
    try:
        return await primary()
    except OpenRouterAPIError as exc:
        if fallback is None:
            raise
        _LOGGER.warning(
            "OpenRouter failed with %s; retrying on Gemini last-resort",
            exc.error_code,
        )
        _update_current_generation(metadata={"last_resort_used": True})
        return await fallback()


async def gemini_json_complete(
    settings: GeminiSettings,
    prompt: str,
    schema: Mapping[str, object],
    system_instruction: str,
    *,
    timeout_seconds: int | None = None,
    transport: Any | None = None,
) -> Mapping[str, Any]:
    resolved_transport = transport or GoogleGenAITransport()
    rotator = GeminiKeyRotator(settings.api_keys)
    resolved_timeout = settings.timeout_seconds if timeout_seconds is None else timeout_seconds
    keys = await rotator.candidates(settings.max_attempts)
    last_error: GeminiRateLimitError | None = None
    for key in keys:
        try:
            return await resolved_transport.generate(
                api_key=key,
                model=settings.model,
                prompt=prompt,
                schema=schema,
                timeout_seconds=resolved_timeout,
                system_instruction=system_instruction,
            )
        except GeminiRateLimitError as error:
            last_error = error
            if not settings.rotate_on_rate_limit:
                raise
    raise last_error or RuntimeError("no Gemini API key was attempted")
