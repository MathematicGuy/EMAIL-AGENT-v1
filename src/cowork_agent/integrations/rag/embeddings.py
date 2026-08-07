"""Embedding adapters for the in-repo RAG store.

The production adapter uses the Gemini embeddings API through the
google-genai SDK; deterministic fakes live in ``fakes.py``. Text content is
never logged.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from google import genai

from cowork_agent.config import GeminiSettings

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
        self._api_key = settings.api_keys[0]
        self._model = model
        self._client = client

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        client = self._client or genai.Client(api_key=self._api_key)
        # list[str] is not assignable to the SDK's invariant union
        # list[str | Image | File | Part]; the values are plain strings.
        response = await client.aio.models.embed_content(
            model=self._model, contents=list(texts)  # type: ignore[arg-type]
        )
        embeddings = []
        for item in response.embeddings or ():
            if item.values is None:
                raise ValueError("Embedding response contained an empty vector")
            values = tuple(float(value) for value in item.values)
            embeddings.append(values)
        if len(embeddings) != len(texts):
            raise ValueError("Embedding response count does not match request count")
        return tuple(embeddings)
