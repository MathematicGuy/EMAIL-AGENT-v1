"""Embedding adapters for the in-repo RAG store.

The production adapter uses the Gemini embeddings API through the
google-genai SDK; deterministic fakes live in ``fakes.py``. Text content is
never logged.
"""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Sequence
from collections.abc import Mapping
from typing import Literal, Protocol
from urllib.request import Request, urlopen

from google import genai
from google.genai import errors, types

from cowork_agent.config import GeminiSettings, JinaEmbeddingSettings
from cowork_agent.integrations.llm.providers.gemini import (
    GeminiKeyRotator,
    GeminiRateLimitError,
)

DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"
_MAX_BATCH_CONTENTS = 100
JINA_EMBEDDING_ENDPOINT = "https://api.jina.ai/v1/embeddings"
_USER_AGENT = "cowork-agent/1.0"

EmbeddingTask = Literal["retrieval.query", "retrieval.passage"]


class EmbeddingPort(Protocol):
    """Embeds texts into fixed-size vector tuples."""

    async def embed(
        self,
        texts: Sequence[str],
        *,
        task: EmbeddingTask = "retrieval.query",
    ) -> tuple[tuple[float, ...], ...]: ...


class JinaEmbeddingTransport(Protocol):
    """Injectable Jina HTTPS boundary for deterministic tests."""

    async def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> Mapping[str, object]: ...


class StdlibJinaEmbeddingTransport:
    """Async wrapper around the standard library HTTPS client."""

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


class JinaEmbeddingAdapter:
    """Production Jina v5 embeddings with validated, ordered vectors."""

    def __init__(
        self,
        settings: JinaEmbeddingSettings,
        *,
        transport: JinaEmbeddingTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport or StdlibJinaEmbeddingTransport()

    async def embed(
        self,
        texts: Sequence[str],
        *,
        task: EmbeddingTask = "retrieval.query",
    ) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        response = await self._transport.post_json(
            url=JINA_EMBEDDING_ENDPOINT,
            headers={
                "Authorization": f"Bearer {self._settings.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            payload={
                "model": self._settings.model,
                "input": list(texts),
                "task": task,
                "dimensions": self._settings.dimensions,
                "embedding_type": "float",
            },
            timeout_seconds=float(self._settings.timeout_seconds),
        )
        return _validated_jina_vectors(
            response, expected_count=len(texts), dimensions=self._settings.dimensions
        )


class GeminiEmbeddingAdapter:
    """Production embeddings via the Gemini API (one batch call)."""

    def __init__(
        self,
        settings: GeminiSettings,
        *,
        model: str = DEFAULT_EMBEDDING_MODEL,
        client: genai.Client | None = None,
    ) -> None:
        if not settings.api_keys:
            raise ValueError("GeminiEmbeddingAdapter requires at least one API key")
        self._settings = settings
        self._rotator = GeminiKeyRotator(settings.api_keys)
        self._model = model
        self._client = client

    async def embed(
        self,
        texts: Sequence[str],
        *,
        task: EmbeddingTask = "retrieval.query",
    ) -> tuple[tuple[float, ...], ...]:
        del task
        if not texts:
            return ()
        embeddings: list[tuple[float, ...]] = []
        for start in range(0, len(texts), _MAX_BATCH_CONTENTS):
            batch = list(texts[start : start + _MAX_BATCH_CONTENTS])
            response = await self._embed_content(batch)
            for item in response.embeddings or ():
                if item.values is None:
                    raise ValueError("Embedding response contained an empty vector")
                embeddings.append(tuple(float(value) for value in item.values))
        if len(embeddings) != len(texts):
            raise ValueError("Embedding response count does not match request count")
        return tuple(embeddings)

    async def _embed_content(self, contents: list[str]) -> types.EmbedContentResponse:
        """Same key rotation the generator uses: one exhausted key must not
        take the whole RAG index down while sibling keys still have quota."""
        if self._client is not None:
            # list[str] is not assignable to the SDK's invariant union
            # list[str | Image | File | Part]; the values are plain strings.
            return await self._client.aio.models.embed_content(
                model=self._model, contents=contents  # type: ignore[arg-type]
            )
        last_error: GeminiRateLimitError | None = None
        for key in await self._rotator.candidates(self._settings.max_attempts):
            client = genai.Client(api_key=key)
            try:
                return await client.aio.models.embed_content(
                    model=self._model, contents=contents  # type: ignore[arg-type]
                )
            except errors.APIError as exc:
                if exc.code != 429:
                    raise
                last_error = GeminiRateLimitError("Gemini embedding quota exhausted")
                # Set by hand because the loop's final raise is outside this
                # except block, where `from exc` is no longer available.
                last_error.__cause__ = exc
                if not self._settings.rotate_on_rate_limit:
                    raise last_error from exc
        raise last_error or RuntimeError("No Gemini API key was attempted")


def _validated_jina_vectors(
    response: Mapping[str, object], *, expected_count: int, dimensions: int
) -> tuple[tuple[float, ...], ...]:
    raw_data = response.get("data")
    if isinstance(raw_data, str | bytes) or not isinstance(raw_data, Sequence):
        raise ValueError("Jina embedding response data must be a sequence")
    if len(raw_data) != expected_count:
        raise ValueError("Jina embedding response count does not match request count")

    vectors_by_index: dict[int, tuple[float, ...]] = {}
    for item in raw_data:
        if not isinstance(item, Mapping):
            raise ValueError("Jina embedding response data items must be objects")
        index = item.get("index")
        raw_embedding = item.get("embedding")
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or index < 0
            or index >= expected_count
            or index in vectors_by_index
            or isinstance(raw_embedding, str | bytes)
            or not isinstance(raw_embedding, Sequence)
            or len(raw_embedding) != dimensions
        ):
            raise ValueError("Jina embedding response has an invalid vector dimension or index")
        vector = tuple(float(value) for value in raw_embedding)
        if any(
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(float(value))
            for value in raw_embedding
        ):
            raise ValueError("Jina embedding response contains a non-finite vector value")
        vectors_by_index[index] = vector

    if set(vectors_by_index) != set(range(expected_count)):
        raise ValueError("Jina embedding response indexes do not match request")
    return tuple(vectors_by_index[index] for index in range(expected_count))


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
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - fixed Jina endpoint
        decoded: object = json.loads(response.read().decode("utf-8"))
    if not isinstance(decoded, Mapping):
        raise TypeError("Jina embedding response must be a JSON object")
    return decoded
