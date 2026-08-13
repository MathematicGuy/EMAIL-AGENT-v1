from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping, Sequence


def mask_api_key(key: str) -> str:
    """Safely mask API key values for logging compliance."""
    if not key or len(key) <= 4:
        return "***"
    return f"{key[:4]}...{key[-4:]}" if len(key) >= 10 else f"{key[:2]}***"


def parse_api_keys_from_env(
    environ: Mapping[str, str],
    prefix: str,
) -> tuple[str, ...]:
    """Parse API keys matching a prefix (e.g. COHERE_API_KEY, COHERE_API_KEY2, GEMINI_API_KEY_1)."""
    raw_prefix = prefix.strip()
    pattern = re.compile(rf"^{re.escape(raw_prefix)}(?:_?(\d+))?$", re.IGNORECASE)

    found: list[tuple[int, str]] = []
    for name, value in environ.items():
        match = pattern.match(name)
        if match:
            idx_str = match.group(1)
            idx = int(idx_str) if idx_str is not None else 1
            cleaned = value.strip()
            if cleaned and not cleaned.lower().startswith("replace-with-"):
                found.append((idx, cleaned))

    if not found:
        raise ValueError(f"At least one API key matching '{prefix}' must be configured")

    found.sort(key=lambda item: item[0])

    unique_keys: list[str] = []
    for _, value in found:
        if value not in unique_keys:
            unique_keys.append(value)

    keys = tuple(unique_keys)
    if not keys:
        raise ValueError(f"At least one API key matching '{prefix}' must be configured")

    return keys


class APIKeyRotator:
    """Thread/async-safe round-robin API key rotator."""

    def __init__(self, keys: Sequence[str], provider_name: str = "API") -> None:
        if not keys:
            raise ValueError(f"At least one {provider_name} API key is required")
        self._keys = tuple(keys)
        self._provider_name = provider_name
        self._index = 0
        self._lock = asyncio.Lock()

    @classmethod
    def from_env(
        cls,
        prefix: str,
        environ: Mapping[str, str] | None = None,
        provider_name: str | None = None,
    ) -> APIKeyRotator:
        import os

        if environ is None:
            environ = os.environ
        resolved_provider = (
            provider_name
            or prefix.removesuffix("_API_KEY").removesuffix("_KEY").capitalize()
        )
        keys = parse_api_keys_from_env(environ, prefix)
        return cls(keys, provider_name=resolved_provider)

    async def candidates(self, max_attempts: int) -> tuple[str, ...]:
        async with self._lock:
            start = self._index
            self._index = (self._index + 1) % len(self._keys)
        attempts = min(max_attempts, len(self._keys))
        return tuple(
            self._keys[(start + offset) % len(self._keys)]
            for offset in range(attempts)
        )

    @property
    def keys(self) -> tuple[str, ...]:
        return self._keys

    @property
    def provider_name(self) -> str:
        return self._provider_name
