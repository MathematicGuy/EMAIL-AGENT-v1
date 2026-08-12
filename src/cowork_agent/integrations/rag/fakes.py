"""Deterministic embedding fakes for the in-repo RAG store."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Literal

_DIMENSIONS = 64
_TOKEN_PATTERN = re.compile(r"[0-9\w]+", re.UNICODE)


class HashingEmbedder:
    """Deterministic, stdlib-only embedder for offline tests.

    Each casefolded token hashes (sha256) into one of ``_DIMENSIONS`` buckets
    with a signed contribution; the vector is L2-normalized. Overlapping
    vocabulary therefore yields high cosine similarity. No builtin ``hash()``,
    so results are stable across processes.
    """

    async def embed(
        self,
        texts: Sequence[str],
        *,
        task: Literal["retrieval.query", "retrieval.passage"] = "retrieval.query",
    ) -> tuple[tuple[float, ...], ...]:
        del task
        return tuple(self._embed_one(text) for text in texts)

    def _embed_one(self, text: str) -> tuple[float, ...]:
        vector = [0.0] * _DIMENSIONS
        for token in _TOKEN_PATTERN.findall(text.casefold()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = digest[0] % _DIMENSIONS
            sign = 1.0 if digest[1] % 2 == 0 else -1.0
            vector[bucket] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return tuple(vector)
        return tuple(value / norm for value in vector)


class SlowEmbedder:
    """Embedder that always raises TimeoutError, to exercise timeout paths."""

    async def embed(
        self,
        texts: Sequence[str],
        *,
        task: Literal["retrieval.query", "retrieval.passage"] = "retrieval.query",
    ) -> tuple[tuple[float, ...], ...]:
        del texts, task
        raise TimeoutError("embedding backend timed out")
