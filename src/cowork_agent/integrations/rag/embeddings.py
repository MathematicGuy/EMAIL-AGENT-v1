"""Embedding adapters for the in-repo RAG store.

The production adapter uses the Gemini embeddings API through the
google-genai SDK; deterministic fakes live in ``fakes.py``. Text content is
never logged.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from google import genai
from google.genai import errors, types

from cowork_agent.config import GeminiSettings
from cowork_agent.integrations.llm.providers.gemini import (
    GeminiKeyRotator,
    GeminiRateLimitError,
)

DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"


class EmbeddingPort(Protocol):
    """Embeds texts into fixed-size vector tuples."""

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...


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

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        response = await self._embed_content(list(texts))
        embeddings = []
        for item in response.embeddings or ():
            if item.values is None:
                raise ValueError("Embedding response contained an empty vector")
            values = tuple(float(value) for value in item.values)
            embeddings.append(values)
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
