"""Embedding adapters for the in-repo RAG store.

The production adapter uses the Gemini embeddings API through the
google-genai SDK; deterministic fakes live in ``fakes.py``. Text content is
never logged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Protocol, cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from google import genai
from google.genai import errors, types

from cowork_agent.config import GeminiEmbeddingSettings, GeminiSettings, JinaEmbeddingSettings
from cowork_agent.integrations.key_rotation import mask_api_key
from cowork_agent.integrations.llm.providers.gemini import (
    GeminiKeyRotator,
    GeminiRateLimitError,
)

DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"
_MAX_BATCH_CONTENTS = 100
JINA_EMBEDDING_ENDPOINT = "https://api.jina.ai/v1/embeddings"
_USER_AGENT = "cowork-agent/1.0"

logger = logging.getLogger(__name__)

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
        batch_size = 20
        all_vectors: list[tuple[float, ...]] = []
        for start in range(0, len(texts), batch_size):
            batch_texts = list(texts[start : start + batch_size])
            keys = await self._settings.rotator.candidates(self._settings.max_attempts)
            response: Mapping[str, object] | None = None
            last_error: Exception | None = None
            for key in keys:
                try:
                    response = await self._transport.post_json(
                        url=JINA_EMBEDDING_ENDPOINT,
                        headers={
                            "Authorization": f"Bearer {key}",
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                        },
                        payload={
                            "model": self._settings.model,
                            "input": batch_texts,
                            "task": task,
                            "dimensions": self._settings.dimensions,
                            "embedding_type": "float",
                        },
                        timeout_seconds=float(self._settings.timeout_seconds),
                    )
                    break
                except Exception as exc:
                    last_error = exc
                    if (
                        _is_rate_limit_error(exc)
                        and self._settings.rotate_on_rate_limit
                    ):
                        logger.warning(
                            "Jina embedding rate limit for key %s; rotating key",
                            mask_api_key(key),
                        )
                        continue
                    raise
            if response is None:
                raise last_error or ValueError("Embedding request failed without attempting a key")
            batch_vectors = _validated_jina_vectors(
                response, expected_count=len(batch_texts), dimensions=self._settings.dimensions
            )
            all_vectors.extend(batch_vectors)
            await asyncio.sleep(0.2)
        return tuple(all_vectors)


def _is_rate_limit_error(exc: Exception) -> bool:
    if isinstance(exc, HTTPError) and exc.code == 429:
        return True
    code = getattr(exc, "code", getattr(exc, "status_code", getattr(exc, "status", None)))
    if code == 429:
        return True
    message = str(exc).lower()
    return "429" in message or "rate limit" in message or "too many requests" in message


class GeminiEmbeddingAdapter:
    """Production embeddings via the Gemini API with model-safe batching."""

    def __init__(
        self,
        settings: GeminiSettings | GeminiEmbeddingSettings,
        *,
        model: str = DEFAULT_EMBEDDING_MODEL,
        client: genai.Client | None = None,
    ) -> None:
        if not settings.api_keys:
            raise ValueError("GeminiEmbeddingAdapter requires at least one API key")
        self._settings = settings
        self._rotator = GeminiKeyRotator(settings.api_keys)
        self._model = model
        if isinstance(settings, GeminiEmbeddingSettings):
            self._model = settings.model
            self._dimensions: int | None = settings.dimensions
            self._batch_size = settings.batch_size
            self._timeout_seconds: float | None = float(settings.timeout_seconds)
            self._max_attempts = min(settings.max_attempts, 2)
        else:
            self._dimensions = None
            self._batch_size = _MAX_BATCH_CONTENTS
            self._timeout_seconds = None
            self._max_attempts = settings.max_attempts
        self._client = client

    async def embed(
        self,
        texts: Sequence[str],
        *,
        task: EmbeddingTask = "retrieval.query",
    ) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        embeddings: list[tuple[float, ...]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            # Gemini Embedding 2 combines a list of contents into one
            # multimodal input and deliberately returns one vector. Send each
            # text separately so every project-document chunk receives its own
            # embedding. Earlier text models retain their supported batching.
            requests = (
                ([text] for text in batch)
                if "gemini-embedding-2" in self._model
                else (batch,)
            )
            for contents in requests:
                response = await self._embed_content(contents, task=task)
                for item in response.embeddings or ():
                    if item.values is None:
                        raise ValueError("Embedding response contained an empty vector")
                    vector = tuple(float(value) for value in item.values)
                    if self._dimensions is not None and len(vector) != self._dimensions:
                        raise ValueError(
                            "Gemini embedding response has an invalid vector dimension"
                        )
                    if any(not math.isfinite(value) for value in vector):
                        raise ValueError("Gemini embedding response contains a non-finite value")
                    embeddings.append(vector)
        if len(embeddings) != len(texts):
            raise ValueError("Embedding response count does not match request count")
        return tuple(embeddings)

    async def _embed_content(
        self, contents: list[str], *, task: EmbeddingTask
    ) -> types.EmbedContentResponse:
        """Same key rotation the generator uses: one exhausted key must not
        take the whole RAG index down while sibling keys still have quota."""
        config = (
            types.EmbedContentConfig(
                task_type=(
                    "RETRIEVAL_DOCUMENT"
                    if task == "retrieval.passage"
                    else "RETRIEVAL_QUERY"
                ),
                output_dimensionality=self._dimensions,
            )
            if self._dimensions is not None
            else None
        )

        async def request(client: genai.Client) -> types.EmbedContentResponse:
            call = client.aio.models.embed_content(
                model=self._model,
                contents=cast(Any, contents),
                **({"config": config} if config is not None else {}),
            )
            if self._timeout_seconds is None:
                return await call
            return await asyncio.wait_for(call, timeout=self._timeout_seconds)

        if self._client is not None:
            return await request(self._client)
        last_error: GeminiRateLimitError | None = None
        for key in await self._rotator.candidates(self._max_attempts):
            client = genai.Client(api_key=key)
            try:
                return await request(client)
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
