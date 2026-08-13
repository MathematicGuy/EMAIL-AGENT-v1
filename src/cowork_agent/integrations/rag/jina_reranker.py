"""Jina cross-encoder reranking boundary with a safe retrieval fallback."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Protocol
from urllib.request import Request, urlopen

from cowork_agent.domain.target_contracts import SemanticChunk

JINA_RERANK_ENDPOINT = "https://api.jina.ai/v1/rerank"
JINA_RERANK_MODEL = "jina-reranker-v2-base-multilingual"
_USER_AGENT = "cowork-agent/1.0"


class RerankerPort(Protocol):
    """Secondary scorer for already ACL-filtered retrieval candidates."""

    async def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[SemanticChunk],
        top_n: int | None = None,
    ) -> tuple[SemanticChunk, ...]: ...


class JinaRerankerTransport(Protocol):
    """Injectable HTTP boundary, kept small so unit tests need no network."""

    async def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> Mapping[str, object]: ...


class StdlibJinaRerankerTransport:
    """Async wrapper around the standard library's blocking HTTPS client."""

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


class JinaRerankerAdapter:
    """Call Jina when available; otherwise preserve the existing candidate order."""

    def __init__(
        self,
        *,
        api_key: str | None,
        transport: JinaRerankerTransport | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._transport = transport or StdlibJinaRerankerTransport()
        self._timeout_seconds = timeout_seconds

    async def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[SemanticChunk],
        top_n: int | None = None,
    ) -> tuple[SemanticChunk, ...]:
        """Return validated Jina ordering, or the untouched candidate tuple on failure.

        This boundary deliberately logs neither request content nor credentials.
        """
        original = tuple(candidates)
        if not original or not self._api_key:
            return original
        result_count = _requested_result_count(top_n=top_n, candidate_count=len(original))
        if result_count is None:
            return original

        payload: dict[str, object] = {
            "model": JINA_RERANK_MODEL,
            "query": query,
            "documents": [candidate.text for candidate in original],
            # The adapter needs only index and score; do not request echoed text.
            "return_documents": False,
        }
        if top_n is not None:
            payload["top_n"] = top_n
        try:
            response = await self._transport.post_json(
                url=JINA_RERANK_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                payload=payload,
                timeout_seconds=self._timeout_seconds,
            )
        except Exception:
            return original

        results = _validated_results(
            response=response,
            candidate_count=len(original),
            expected_count=result_count,
        )
        if results is None:
            return original
        return tuple(replace(original[index], rerank_score=score) for index, score in results)


class FakeJinaReranker:
    """Deterministic reranker for offline integration tests.

    With no scores, it is a pass-through. Supplying scores orders candidates by
    descending score and then chunk ID, giving tests a stable re-scoring fake.
    """

    def __init__(self, *, scores: Mapping[str, float] | None = None) -> None:
        self._scores = dict(scores) if scores is not None else None

    async def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[SemanticChunk],
        top_n: int | None = None,
    ) -> tuple[SemanticChunk, ...]:
        del query
        original = tuple(candidates)
        result_count = _requested_result_count(top_n=top_n, candidate_count=len(original))
        if self._scores is None or result_count is None:
            return original
        scored = tuple(
            replace(
                candidate,
                rerank_score=self._scores.get(candidate.chunk_id, candidate.relevance_score),
            )
            for candidate in original
        )
        return tuple(sorted(scored, key=_rerank_sort_key))[:result_count]


def _requested_result_count(*, top_n: int | None, candidate_count: int) -> int | None:
    if top_n is None:
        return candidate_count
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n <= 0:
        return None
    return min(top_n, candidate_count)


def _rerank_sort_key(candidate: SemanticChunk) -> tuple[float, str]:
    score = candidate.rerank_score
    assert score is not None
    return (-score, candidate.chunk_id)


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
    # Cloudflare fronts api.jina.ai and rejects urllib's default User-Agent with
    # HTTP 403 "error code: 1010". Without this the adapter's fallback silently
    # degrades every call to a no-op reranker.
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"User-Agent": _USER_AGENT, **dict(headers)},
        method="POST",
    )
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - fixed Jina endpoint
        decoded: object = json.loads(response.read().decode("utf-8"))
    if not isinstance(decoded, Mapping):
        raise TypeError("Jina reranker response must be a JSON object")
    return decoded
