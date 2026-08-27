# Key Rotation Architecture & Cohere/Jina Unified Reranker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace legacy single-key Jina adapter with a robust key rotation engine (`APIKeyRotator`) and unified strategy-driven `RerankerAdapter` supporting Cohere Rerank (`rerank-v4.0-fast`) and Jina Rerank endpoints.

**Architecture:** A thread/async-safe `APIKeyRotator` discovers keys matching provider prefixes (`COHERE_API_KEY`, `JINA_API_KEY`, `GEMINI_API_KEY`) regardless of numeric delimiter styles. `RerankerAdapter` uses `RERANKER_MODEL` to route payloads to Cohere API v2 (`https://api.cohere.com/v2/rerank`) or Jina API (`https://api.jina.ai/v1/rerank`) with HTTP 429 rate-limit failover across rotated keys.

**Tech Stack:** Python 3.11+, stdlib `asyncio` & `urllib.request`, `dataclasses`, `pytest`, `ruff`, `mypy`.

## Global Constraints

- **Python Version:** 3.11+
- **Security:** Redact API key secrets in logs via `mask_api_key(key)`.
- **RAG Graceful Degradation:** Upstream network/credential failure on reranking must silently return original candidate order without throwing unhandled runtime errors (§12.3).
- **Reranker Default Model:** `rerank-v4.0-fast`.

---

### Task 1: Core Key Rotation Component (`APIKeyRotator`)

**Files:**
- Create: `src/cowork_agent/integrations/key_rotation.py`
- Test: `tests/unit/integrations/test_key_rotation.py`

**Interfaces:**
- Produces: `parse_api_keys_from_env(environ: Mapping[str, str], prefix: str) -> tuple[str, ...]`, `mask_api_key(key: str) -> str`, `APIKeyRotator(keys: Sequence[str], provider_name: str = "API")` with `.candidates(max_attempts: int)` and `.from_env(prefix, environ, provider_name)`.

- [ ] **Step 1: Write failing unit tests for key discovery and rotation**

```python
# tests/unit/integrations/test_key_rotation.py
import pytest
from cowork_agent.integrations.key_rotation import (
    APIKeyRotator,
    mask_api_key,
    parse_api_keys_from_env,
)


def test_parse_api_keys_from_env_flexible_patterns():
    env = {
        "COHERE_API_KEY": "key1",
        "COHERE_API_KEY2": "key2",
        "COHERE_API_KEY3": "key3",
        "GEMINI_API_KEY_1": "gkey1",
        "GEMINI_API_KEY_2": "gkey2",
    }
    cohere_keys = parse_api_keys_from_env(env, "COHERE_API_KEY")
    assert cohere_keys == ("key1", "key2", "key3")

    gemini_keys = parse_api_keys_from_env(env, "GEMINI_API_KEY")
    assert gemini_keys == ("gkey1", "gkey2")


def test_mask_api_key():
    assert mask_api_key("cohere_1234567890abcdef") == "cohe...cdef"
    assert mask_api_key("short") == "sh***"
    assert mask_api_key("") == "***"


@pytest.mark.asyncio
async def test_key_rotator_round_robin():
    rotator = APIKeyRotator(["k1", "k2", "k3"], provider_name="Test")
    c1 = await rotator.candidates(max_attempts=2)
    assert c1 == ("k1", "k2")

    c2 = await rotator.candidates(max_attempts=2)
    assert c2 == ("k2", "k3")

    c3 = await rotator.candidates(max_attempts=2)
    assert c3 == ("k3", "k1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/integrations/test_key_rotation.py -q`  
Expected: FAIL with `ModuleNotFoundError: No module named 'cowork_agent.integrations.key_rotation'`

- [ ] **Step 3: Implement `APIKeyRotator` and `parse_api_keys_from_env`**

```python
# src/cowork_agent/integrations/key_rotation.py
from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping, Sequence


def mask_api_key(key: str) -> str:
    """Safely mask API key values for logging compliance."""
    if not key:
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
            if cleaned and not cleaned.startswith("replace-with-"):
                found.append((idx, cleaned))

    if not found:
        raise ValueError(f"At least one API key matching '{prefix}' must be configured")

    found.sort(key=lambda item: item[0])
    keys = tuple(value for _, value in found)

    if len(set(keys)) != len(keys):
        raise ValueError(f"API keys matching '{prefix}' must be unique")

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
            provider_name or prefix.removesuffix("_API_KEY").removesuffix("_KEY").capitalize()
        )
        keys = parse_api_keys_from_env(environ, prefix)
        return cls(keys, provider_name=resolved_provider)

    async def candidates(self, max_attempts: int) -> tuple[str, ...]:
        async with self._lock:
            start = self._index
            self._index = (self._index + 1) % len(self._keys)
        attempts = min(max_attempts, len(self._keys))
        return tuple(self._keys[(start + offset) % len(self._keys)] for offset in range(attempts))

    @property
    def keys(self) -> tuple[str, ...]:
        return self._keys

    @property
    def provider_name(self) -> str:
        return self._provider_name
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/integrations/test_key_rotation.py -q`  
Expected: PASS

- [ ] **Step 5: Run linter and typecheck**

Run: `python -m ruff check src/cowork_agent/integrations/key_rotation.py`  
Run: `python -m mypy src/cowork_agent/integrations/key_rotation.py`  
Expected: Clean output with 0 errors.

- [ ] **Step 6: Commit Task 1**

```bash
git add src/cowork_agent/integrations/key_rotation.py tests/unit/integrations/test_key_rotation.py
git commit -m "feat(key-rotation): add APIKeyRotator and parse_api_keys_from_env utility"
```

---

### Task 2: Unified Reranker Adapter (`RerankerAdapter`) & Provider Transports

**Files:**
- Create: `src/cowork_agent/integrations/rag/reranker.py`
- Test: `tests/unit/integrations/test_reranker.py`

**Interfaces:**
- Consumes: `APIKeyRotator`, `SemanticChunk`
- Produces: `RerankerPort`, `StdlibRerankerTransport`, `RerankerAdapter`

- [ ] **Step 1: Write failing tests for Cohere and Jina reranking with rotation & fallback**

```python
# tests/unit/integrations/test_reranker.py
import pytest
from cowork_agent.domain.target_contracts import SemanticChunk
from cowork_agent.integrations.key_rotation import APIKeyRotator
from cowork_agent.integrations.rag.reranker import (
    RerankerAdapter,
    RerankerSettings,
)


class FakeTransport:
    def __init__(self, responses: list[dict | Exception]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def post_json(self, *, url, headers, payload, timeout_seconds):
        self.calls.append({"url": url, "headers": headers, "payload": payload})
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


@pytest.fixture
def sample_candidates():
    return (
        SemanticChunk(
            chunk_id="c1", text="Doc 1", relevance_score=0.5, doc_id="d1", title="T1", filepath="f1"
        ),
        SemanticChunk(
            chunk_id="c2", text="Doc 2", relevance_score=0.8, doc_id="d2", title="T2", filepath="f2"
        ),
    )


@pytest.mark.asyncio
async def test_cohere_rerank_success(sample_candidates):
    rotator = APIKeyRotator(["key_cohere_1"])
    settings = RerankerSettings(model="rerank-v4.0-fast", rotator=rotator)
    transport = FakeTransport(
        [
            {
                "results": [
                    {"index": 1, "relevance_score": 0.99},
                    {"index": 0, "relevance_score": 0.12},
                ]
            }
        ]
    )
    adapter = RerankerAdapter(settings=settings, transport=transport)
    results = await adapter.rerank(query="test", candidates=sample_candidates)

    assert len(results) == 2
    assert results[0].chunk_id == "c2"
    assert results[0].rerank_score == 0.99
    assert results[1].chunk_id == "c1"
    assert results[1].rerank_score == 0.12


@pytest.mark.asyncio
async def test_cohere_rerank_key_rotation_on_429(sample_candidates):
    class HTTP429Error(RuntimeError):
        status = 429

    rotator = APIKeyRotator(["bad_key", "good_key"])
    settings = RerankerSettings(model="rerank-v4.0-fast", rotator=rotator, max_attempts=2)
    transport = FakeTransport(
        [
            HTTP429Error("Rate limit exceeded"),
            {
                "results": [
                    {"index": 0, "relevance_score": 0.95},
                    {"index": 1, "relevance_score": 0.40},
                ]
            },
        ]
    )
    adapter = RerankerAdapter(settings=settings, transport=transport)
    results = await adapter.rerank(query="test", candidates=sample_candidates)

    assert len(transport.calls) == 2
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer bad_key"
    assert transport.calls[1]["headers"]["Authorization"] == "Bearer good_key"
    assert results[0].chunk_id == "c1"
    assert results[0].rerank_score == 0.95
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/integrations/test_reranker.py -q`  
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `RerankerAdapter` and payload builders**

```python
# src/cowork_agent/integrations/rag/reranker.py
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
    model: str
    rotator: APIKeyRotator
    timeout_seconds: float = 10.0
    rotate_on_rate_limit: bool = True
    max_attempts: int = 3


class RerankerTransport(Protocol):
    async def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> Mapping[str, object]: ...


class StdlibRerankerTransport:
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
    """Strategy-driven reranker for Cohere and Jina endpoints with key rotation."""

    def __init__(
        self,
        settings: RerankerSettings,
        transport: RerankerTransport | None = None,
    ) -> None:
        self._settings = settings
        self._rotator = settings.rotator
        self._transport = transport or StdlibRerankerTransport()

    async def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[SemanticChunk],
        top_n: int | None = None,
    ) -> tuple[SemanticChunk, ...]:
        original = tuple(candidates)
        if not original or not self._rotator.keys:
            return original

        result_count = _requested_result_count(top_n=top_n, candidate_count=len(original))
        if result_count is None:
            return original

        is_cohere = (
            self._settings.model.startswith("rerank-") or "cohere" in self._settings.model.lower()
        )
        url = COHERE_RERANK_ENDPOINT if is_cohere else JINA_RERANK_ENDPOINT

        payload: dict[str, object] = {
            "model": self._settings.model,
            "query": query,
            "documents": [c.text for c in original],
        }
        if is_cohere:
            if top_n is not None:
                payload["top_n"] = top_n
        else:
            payload["return_documents"] = False
            if top_n is not None:
                payload["top_n"] = top_n

        keys = await self._rotator.candidates(self._settings.max_attempts)
        response: Mapping[str, object] | None = None

        for idx, key in enumerate(keys, 1):
            try:
                response = await self._transport.post_json(
                    url=url,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    payload=payload,
                    timeout_seconds=self._settings.timeout_seconds,
                )
                break
            except Exception as exc:
                is_rate_limit = getattr(exc, "status", None) == 429 or "429" in str(exc)
                if is_rate_limit:
                    logger.warning(
                        "⚠️ [Reranker] Rate limit (429) on %s Key %d/%d (%s), rotating key...",
                        self._rotator.provider_name,
                        idx,
                        len(keys),
                        mask_api_key(key),
                    )
                    if not self._settings.rotate_on_rate_limit:
                        return original
                    continue
                logger.warning(
                    "⚠️ [Reranker] Endpoint error (%s: %s); degrading to un-reranked candidates",
                    type(exc).__name__,
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

        return tuple(replace(original[index], rerank_score=score) for index, score in results)


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
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            decoded: object = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise exc
    if not isinstance(decoded, Mapping):
        raise TypeError("Reranker response must be a JSON object")
    return decoded
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/integrations/test_reranker.py -q`  
Expected: PASS

- [ ] **Step 5: Run linter and typecheck**

Run: `python -m ruff check src/cowork_agent/integrations/rag/reranker.py`  
Run: `python -m mypy src/cowork_agent/integrations/rag/reranker.py`  
Expected: Clean output with 0 errors.

- [ ] **Step 6: Commit Task 2**

```bash
git add src/cowork_agent/integrations/rag/reranker.py tests/unit/integrations/test_reranker.py
git commit -m "feat(reranker): implement strategy-driven RerankerAdapter with key rotation"
```

---

### Task 3: Configuration Dataclass & RAG Bootstrap Wiring

**Files:**
- Modify: `src/cowork_agent/config.py`
- Modify: `src/cowork_agent/integrations/rag/bootstrap.py`
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Consumes: `APIKeyRotator`, `RerankerSettings`, `RerankerAdapter`
- Produces: `RerankerSettings.from_env()`, `bootstrap.build_semantic_memory` updated.

- [ ] **Step 1: Write failing test for `RerankerSettings.from_env()`**

Add to `tests/unit/test_config.py`:

```python
def test_reranker_settings_from_env_cohere():
    env = {
        "RERANKER_MODEL": "rerank-v4.0-fast",
        "COHERE_API_KEY": "cohere_key_1",
        "COHERE_API_KEY2": "cohere_key_2",
    }
    settings = RerankerSettings.from_env(env)
    assert settings.model == "rerank-v4.0-fast"
    assert settings.rotator.keys == ("cohere_key_1", "cohere_key_2")
    assert settings.rotator.provider_name == "Cohere"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_config.py::test_reranker_settings_from_env_cohere -q`  
Expected: FAIL with `NameError: name 'RerankerSettings' is not defined`

- [ ] **Step 3: Update `config.py` and `bootstrap.py`**

In `src/cowork_agent/config.py`:
Add import:
`from cowork_agent.integrations.key_rotation import APIKeyRotator`

Add class:
```python
@dataclass(frozen=True, slots=True)
class RerankerSettings:
    model: str
    rotator: APIKeyRotator
    timeout_seconds: float = 10.0
    rotate_on_rate_limit: bool = True
    max_attempts: int = 3

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        load_env_file: bool = True,
    ) -> RerankerSettings:
        if environ is None:
            if load_env_file:
                load_dotenv(override=False)
            environ = os.environ

        model = environ.get("RERANKER_MODEL", "rerank-v4.0-fast").strip()
        is_cohere = model.startswith("rerank-") or "cohere" in model.lower()
        prefix = "COHERE_API_KEY" if is_cohere else "JINA_API_KEY"
        provider_name = "Cohere" if is_cohere else "Jina"

        rotator = APIKeyRotator.from_env(prefix, environ=environ, provider_name=provider_name)
        rotate_on_rate_limit = _boolean(environ, "RERANKER_ROTATE_ON_RATE_LIMIT", True)
        timeout = float(_positive_int(environ, "RERANKER_TIMEOUT_SECONDS", 10))

        return cls(
            model=model,
            rotator=rotator,
            timeout_seconds=timeout,
            rotate_on_rate_limit=rotate_on_rate_limit,
            max_attempts=len(rotator.keys),
        )
```

In `src/cowork_agent/integrations/rag/bootstrap.py`:
Replace `from cowork_agent.integrations.rag.jina_reranker import JinaRerankerAdapter` with:
`from cowork_agent.integrations.rag.reranker import RerankerAdapter`
`from cowork_agent.config import RerankerSettings`

Update line 56:
```python
        memory = HybridSemanticMemory(
            documents,
            JinaEmbeddingAdapter(settings),
            reranker=RerankerAdapter(settings=RerankerSettings.from_env()),
            query_transformer=RuleBasedQueryTransformer(enable_hyde=True),
            enable_mmr=True,
            min_rerank_score=0.30,
            relative_cutoff_ratio=0.85,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_config.py::test_reranker_settings_from_env_cohere -q`  
Expected: PASS

- [ ] **Step 5: Run linter and typecheck**

Run: `python -m ruff check src/cowork_agent/config.py src/cowork_agent/integrations/rag/bootstrap.py`  
Run: `python -m mypy src/cowork_agent/config.py src/cowork_agent/integrations/rag/bootstrap.py`  
Expected: Clean output.

- [ ] **Step 6: Commit Task 3**

```bash
git add src/cowork_agent/config.py src/cowork_agent/integrations/rag/bootstrap.py tests/unit/test_config.py
git commit -m "feat(config): integrate RerankerSettings and wire RerankerAdapter into bootstrap"
```

---

### Task 4: Gemini Key Rotator Migration & Integration Verification

**Files:**
- Modify: `src/cowork_agent/integrations/llm/providers/gemini.py`
- Test: `tests/unit/integrations/test_gemini.py`

**Interfaces:**
- Refactor: `GeminiKeyRotator` delegates key discovery and round-robin logic to `APIKeyRotator`.

- [ ] **Step 1: Refactor `GeminiKeyRotator` in `gemini.py`**

In `src/cowork_agent/integrations/llm/providers/gemini.py`:
Import `APIKeyRotator`:
`from cowork_agent.integrations.key_rotation import APIKeyRotator`

Refactor `GeminiKeyRotator`:
```python
class GeminiKeyRotator:
    """Select a different first key per request without exposing key values."""

    def __init__(self, keys: Sequence[str]) -> None:
        self._rotator = APIKeyRotator(keys, provider_name="Gemini")

    async def candidates(self, max_attempts: int) -> tuple[str, ...]:
        return await self._rotator.candidates(max_attempts)
```

- [ ] **Step 2: Run Gemini unit tests to verify backward compatibility**

Run: `python -m pytest tests/unit/integrations/test_gemini.py -q`  
Expected: PASS

- [ ] **Step 3: Run full integration verification suite**

Run: `python -m pytest -q`  
Run: `python -m ruff check .`  
Run: `python -m mypy src`  
Expected: ALL test suites pass, 0 linter errors, 0 typecheck errors.

- [ ] **Step 4: Commit Task 4**

```bash
git add src/cowork_agent/integrations/llm/providers/gemini.py
git commit -m "refactor(gemini): migrate GeminiKeyRotator to use APIKeyRotator core"
```
