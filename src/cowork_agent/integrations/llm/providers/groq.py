"""Groq adapter for structured email action extraction."""

import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cowork_agent.config import GroqSettings
from cowork_agent.domain.target_contracts import EphemeralEmailEnvelope
from cowork_agent.features.email_action_plan.schemas import (
    EmailExtraction,
    ExtractionBatch,
)
from cowork_agent.features.email_action_plan.shaping import (
    batch_messages,
    group_by_thread,
    merge_correlated_emails,
)

from .gemini import (
    EXTRACTION_SCHEMA,
    SYSTEM_INSTRUCTION,
    _build_prompt,
    _parse_batch,
)

GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_USER_AGENT = "module-mail/0.1.0"


class GroqAPIError(RuntimeError):
    """Groq returned an error or an unusable completion."""

    error_code = "GROQ_API_ERROR"

    def __init__(self, detail: str, *, safe_message: str | None = None) -> None:
        super().__init__(detail)
        self.safe_message = safe_message or (
            "Groq không thể phân tích email. Vui lòng kiểm tra cấu hình model và thử lại."
        )


class GroqActionExtractor:
    def __init__(self, settings: GroqSettings) -> None:
        self._settings = settings

    async def extract(
        self,
        user_timezone: str,
        current_time: datetime,
        messages: Sequence[EphemeralEmailEnvelope],
    ) -> ExtractionBatch:
        email_results: list[EmailExtraction] = []
        threads = group_by_thread(messages)
        for batch in batch_messages(threads, self._settings.max_emails_per_batch):
            prompt = _build_prompt(user_timezone, current_time, batch)
            payload = await self._complete(prompt)
            try:
                email_results.extend(_parse_batch(payload).emails)
            except (KeyError, TypeError, ValueError) as exc:
                raise GroqAPIError(
                    "Groq response did not match the extraction schema",
                    safe_message=(
                        "Groq trả về dữ liệu không đúng cấu trúc task yêu cầu. "
                        "Vui lòng thử lại hoặc kiểm tra schema extraction."
                    ),
                ) from exc
        return ExtractionBatch(merge_correlated_emails(email_results))

    async def _complete(self, prompt: str) -> Mapping[str, Any]:
        request_body = {
            "model": self._settings.model,
            "messages": [
                {"role": "system", "content": SYSTEM_INSTRUCTION},
                {
                    "role": "user",
                    "content": (
                        f"{prompt}\nReturn only a valid JSON object matching this schema exactly:\n"
                        f"{json.dumps(EXTRACTION_SCHEMA, ensure_ascii=False)}"
                    ),
                },
            ],
            "temperature": 0.7,
            "reasoning_effort": "none",
            "reasoning_format": "hidden",
            "response_format": {"type": "json_object"},
        }
        response = await asyncio.to_thread(
            _post_json,
            GROQ_CHAT_COMPLETIONS_URL,
            self._settings.api_key,
            request_body,
            self._settings.timeout_seconds,
        )
        try:
            content = response["choices"][0]["message"]["content"]
        except (IndexError, KeyError, TypeError) as exc:
            raise GroqAPIError("Groq response did not contain a chat completion") from exc
        try:
            payload = json.loads(str(content))
        except json.JSONDecodeError as exc:
            raise GroqAPIError("Groq response was not valid JSON") from exc
        if not isinstance(payload, Mapping):
            raise GroqAPIError("Groq response JSON must be an object")
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
            "User-Agent": GROQ_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 -- fixed HTTPS URL
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise GroqAPIError(
            f"Groq API returned HTTP {exc.code}",
            safe_message=(
                f"Groq từ chối yêu cầu (HTTP {exc.code}). "
                "Vui lòng kiểm tra model, JSON mode và tham số reasoning rồi thử lại."
            ),
        ) from exc
    except (TimeoutError, URLError) as exc:
        raise GroqAPIError(
            "Groq API request failed",
            safe_message=(
                "Không thể kết nối tới Groq hoặc yêu cầu đã hết thời gian chờ. "
                "Vui lòng kiểm tra mạng và thử lại."
            ),
        ) from exc
    if not isinstance(payload, Mapping):
        raise GroqAPIError("Groq API response must be a JSON object")
    return cast(Mapping[str, Any], payload)
