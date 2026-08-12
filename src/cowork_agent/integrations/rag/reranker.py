"""Unified strategy-driven RerankerAdapter supporting Cohere and Jina APIs with key rotation."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Protocol
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cowork_agent.domain.target_contracts import SemanticChunk
from cowork_agent.integrations.key_rotation import APIKeyRotator, mask_api_key

COHERE_RERANK_ENDPOINT = "https://api.cohere.com/v2/rerank"
JINA_RERANK_ENDPOINT = "https://api.jina.ai/v1/rerank"
_USER_AGENT = "cowork-agent/1.0"

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RerankerSettings:
    """Configuration settings for RerankerAdapter with key rotation."""

    model: str
    rotator: APIKeyRotator
    timeout_seconds: float = 10.0
    rotate_on_rate_limit: bool = True
    max_attempts: int = 3


class RerankerTransport(Protocol):
    """Injectable HTTP boundary for reranking API requests."""

    async def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> Mapping[str, object]: ...


class StdlibRerankerTransport:
    """Async wrapper around standard library HTTPS client."""

    async def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        return await asyncio.wait_for(
            asyncio.to_thread(
                _post_json,
                url=url,
                headers=headers,
                payload=payload,
                timeout_seconds=timeout_seconds,
            ),
            timeout=timeout_seconds,
        )


class RerankerAdapter:
    """Unified reranker adapter supporting Cohere and Jina endpoints with key rotation failover."""

    def __init__(
        self,
        settings: RerankerSettings,
        transport: RerankerTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport or StdlibRerankerTransport()

    async def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[SemanticChunk],
        top_n: int | None = None,
    ) -> tuple[SemanticChunk, ...]:
        """Return reordered candidates with updated rerank_score, or untouched tuple on failure."""
        original = tuple(candidates)
        if not original:
            return original

        result_count = _requested_result_count(top_n=top_n, candidate_count=len(original))
        if result_count is None:
            return original

        endpoint = _determine_endpoint(self._settings.model)
        is_cohere = endpoint == COHERE_RERANK_ENDPOINT

        payload: dict[str, object] = {
            "model": self._settings.model,
            "query": query,
            "documents": [c.text for c in original],
        }
        if not is_cohere:
            payload["return_documents"] = False
        if top_n is not None:
            payload["top_n"] = top_n

        keys = await self._settings.rotator.candidates(self._settings.max_attempts)
        if not keys:
            return original

        response: Mapping[str, object] | None = None
        for key in keys:
            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            try:
                response = await self._transport.post_json(
                    url=endpoint,
                    headers=headers,
                    payload=payload,
                    timeout_seconds=self._settings.timeout_seconds,
                )
                break
            except Exception as exc:
                if _is_rate_limit_error(exc) and self._settings.rotate_on_rate_limit:
                    logger.warning(
                        "Rate limit (HTTP 429) hit for key %s, rotating key",
                        mask_api_key(key),
                    )
                    continue
                logger.warning(
                    "Reranker API call failed for key %s: %s",
                    mask_api_key(key),
                    exc,
                )
                return original

        if response is None:
            return original

        results = _validated_results(
            response=response,
            candidate_count=len(original),
            expected_count=result_count,
        )
        if results is None:
            return original

        return tuple(
            replace(original[index], rerank_score=score)
            for index, score in results
        )


def _determine_endpoint(model: str) -> str:
    model_lower = model.lower()
    if model_lower.startswith("rerank-") or "cohere" in model_lower:
        return COHERE_RERANK_ENDPOINT
    return JINA_RERANK_ENDPOINT


def _is_rate_limit_error(exc: Exception) -> bool:
    if isinstance(exc, HTTPError) and exc.code == 429:
        return True
    code = getattr(exc, "code", getattr(exc, "status_code", getattr(exc, "status", None)))
    if code == 429:
        return True
    err_str = str(exc).lower()
    return "429" in err_str or "rate limit" in err_str or "too many requests" in err_str


def _requested_result_count(*, top_n: int | None, candidate_count: int) -> int | None:
    if top_n is None:
        return candidate_count
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n <= 0:
        return None
    return min(top_n, candidate_count)


def _validated_results(
    *,
    response: Mapping[str, object],
    candidate_count: int,
    expected_count: int,
) -> tuple[tuple[int, float], ...] | None:
    raw_results = response.get("results")
    if isinstance(raw_results, str) or not isinstance(raw_results, Sequence):
        return None
    if len(raw_results) != expected_count:
        return None

    parsed: list[tuple[int, float]] = []
    seen_indexes: set[int] = set()
    for raw_result in raw_results:
        if not isinstance(raw_result, Mapping):
            return None
        index = raw_result.get("index")
        score = raw_result.get("relevance_score")
        if score is None:
            score = raw_result.get("score")
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or index < 0
            or index >= candidate_count
            or index in seen_indexes
            or isinstance(score, bool)
            or not isinstance(score, int | float)
            or not math.isfinite(float(score))
        ):
            return None
        seen_indexes.add(index)
        parsed.append((index, float(score)))
    return tuple(parsed)


def _post_json(
    *,
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, object],
    timeout_seconds: float,
) -> Mapping[str, object]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"User-Agent": _USER_AGENT, **dict(headers)},
        method="POST",
    )
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
        decoded: object = json.loads(response.read().decode("utf-8"))
    if not isinstance(decoded, Mapping):
        raise TypeError("Reranker response must be a JSON object")
    return decoded
