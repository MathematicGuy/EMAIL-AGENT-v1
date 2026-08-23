"""OpenAI-compatible HTTP JSON transport shared by Vyce, Mistral, and OpenRouter."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, cast
from urllib.request import Request, urlopen

DEFAULT_USER_AGENT = "module-mail/0.1.0"


def openai_request_body(
    model: str,
    system_instruction: str,
    user_prompt: str,
    schema: Mapping[str, object],
    max_output_tokens: int,
    *,
    temperature: float = 0.0,
    schema_in_system: bool = False,
    task_proposal_guard: bool = False,
    fallback_models: Sequence[str] = (),
) -> dict[str, object]:
    schema_json = json.dumps(schema, ensure_ascii=False)
    system_content = system_instruction
    if schema_in_system:
        system_content = (
            f"{system_instruction}\n\n"
            "CRITICAL JSON FORMAT REQUIREMENT:\n"
            "You must ALWAYS return a valid JSON object matching this schema exactly:\n"
            f"{schema_json}\n"
            "Never output raw plain text. Even for refusals, missing information, "
            "or generic replies, you MUST encapsulate the response inside the JSON object."
        )
    task_requested = task_proposal_guard and (
        '"task_proposal_requested": true' in user_prompt
        or '"task_proposal_requested":true' in user_prompt
    )
    if task_requested:
        user_content = (
            f"{user_prompt}\n"
            "CRITICAL: Since task_proposal_requested is true, "
            "you MUST populate task_proposal with a full object (NOT null).\n"
            f"Return only a valid JSON object matching this schema exactly:\n"
            f"{schema_json}"
        )
    else:
        user_content = (
            f"{user_prompt}\nReturn only a valid JSON object matching this schema exactly:\n"
            f"{schema_json}"
        )
    body: dict[str, object] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ],
        "temperature": temperature,
        "max_tokens": max_output_tokens,
        "response_format": {"type": "json_object"},
    }
    if fallback_models:
        # Native OpenRouter fallbacks: model=primary, models=fallback array.
        # https://openrouter.ai/docs/guides/routing/model-fallbacks
        body["models"] = list(fallback_models)
    return body


def openai_completion_json(
    response: Mapping[str, Any],
    *,
    error_cls: type[Exception],
    missing_completion: str,
    invalid_json: str,
    not_object: str,
    coerce_plain_text: bool = False,
    empty_response: str | None = None,
) -> Mapping[str, Any]:
    try:
        content = response["choices"][0]["message"]["content"]
    except (IndexError, KeyError, TypeError) as exc:
        raise error_cls(missing_completion) from exc
    content_str = str(content).strip() if coerce_plain_text else str(content)
    if coerce_plain_text and content_str.startswith("```"):
        lines = content_str.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        content_str = "\n".join(lines).strip()
    try:
        payload = json.loads(content_str)
    except json.JSONDecodeError as exc:
        if coerce_plain_text:
            payload = None
        else:
            raise error_cls(invalid_json) from exc
    if isinstance(payload, Mapping):
        if coerce_plain_text:
            normalized: dict[str, Any] = dict(payload)
            if "assistant_text" in normalized:
                normalized.setdefault("conversation_title", "Phản hồi")
                normalized.setdefault("citation_ids", [])
                normalized.setdefault("task_proposal", None)
            return normalized
        return cast(Mapping[str, Any], payload)
    if coerce_plain_text and content_str:
        first_line = content_str.splitlines()[0] if content_str.splitlines() else "Phản hồi"
        title = first_line[:40] if len(first_line) <= 40 else f"{first_line[:37]}..."
        return {
            "assistant_text": content_str,
            "conversation_title": title,
            "citation_ids": [],
            "task_proposal": None,
        }
    if coerce_plain_text:
        raise error_cls(empty_response or invalid_json)
    raise error_cls(not_object)


def post_json(
    url: str,
    api_key: str,
    body: Mapping[str, object],
    timeout_seconds: int,
    *,
    extra_headers: Mapping[str, str] | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
) -> Mapping[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": user_agent,
    }
    if extra_headers:
        headers.update(dict(extra_headers))
    request = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 -- caller supplies HTTPS URL
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("API response must be a JSON object")
    return cast(Mapping[str, Any], payload)
