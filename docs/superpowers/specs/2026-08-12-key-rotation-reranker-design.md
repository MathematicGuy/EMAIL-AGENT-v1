# Key Rotation Architecture & Cohere/Jina Unified Reranker Adapter Specification

**Date:** 2026-08-12  
**Status:** Approved / Design Phase  
**Target Package:** `cowork_agent`

---

## 1. Executive Summary

This specification defines a unified key rotation system (`APIKeyRotator`) and an updated Reranker subsystem (`RerankerAdapter`) for `cowork_agent`. The changes replace the legacy single-key Jina adapter with a strategy-driven reranker that supports Cohere Rerank API v2/v1 models (such as `rerank-v4.0-fast`) as well as Jina Rerank models, backed by robust round-robin multi-key rotation and rate-limit (HTTP 429) failover.

---

## 2. Environment Configuration & Discovery Rules

### 2.1 New & Updated Environment Variables
The application `.env` contains the following active variables for Reranking and Provider Keys:

```env
# RERANKER MODEL
RERANKER_MODEL=rerank-v4.0-fast

# JINA API KEYS
JINA_API_KEY=jina_b5e52...
JINA_API_KEY2=jina_174ac...
JINA_API_KEY3=jina_44539...
JINA_API_KEY4=jina_f4fc5...
JINA_API_KEY5=jina_1dfcf...

# COHERE API KEYS
COHERE_API_KEY=cohere_yEtY7...
COHERE_API_KEY2=cohere_Mohl5...
COHERE_API_KEY3=cohere_WP1wG...
COHERE_API_KEY4=cohere_EDUKm...
COHERE_API_KEY5=cohere_pd6VB...
COHERE_API_KEY6=cohere_RkfWM...

# GEMINI API KEYS
GEMINI_API_KEY_1=AIzaSyCgBg...
GEMINI_API_KEY_2=AIzaSyAtfd...
...
```

### 2.2 Key Discovery Logic (`parse_api_keys_from_env`)
The key rotation parser scans the environment dictionary for keys matching a given `prefix` (e.g. `COHERE_API_KEY`, `JINA_API_KEY`, `GEMINI_API_KEY`):

1. **Pattern Matching**: Matches keys formatted with or without numeric delimiters:
   - Base key: `PREFIX_API_KEY` or `PREFIX_API_KEY_1` / `PREFIX_API_KEY1` (Index 1)
   - Suffix keys: `PREFIX_API_KEY2`, `PREFIX_API_KEY_2`, `PREFIX_API_KEY3`, etc.
2. **Numeric Ordering**: Keys are sorted numerically by their integer index (`1, 2, 3, ...`) to ensure deterministic rotation order.
3. **Filtering & Sanitization**: Whitespace is trimmed; empty strings or placeholders starting with `replace-with-` are ignored.
4. **Validation**:
   - At least 1 non-placeholder key must exist.
   - All parsed keys must be unique.

---

## 3. Core Key Rotation Component (`APIKeyRotator`)

**Module:** `cowork_agent.integrations.key_rotation`

### 3.1 Class Responsibilities
- **Thread/Async Lock Safety**: Uses `asyncio.Lock` when modifying the internal starting index (`self._index`).
- **Round-Robin Key Distribution**: Distributes load evenly across requests by starting each attempt sequence at `(start_index + request_count) % total_keys`.
- **Attempt Generation**: `candidates(max_attempts)` returns a tuple of up to `min(max_attempts, total_keys)` key strings.
- **Key Masking**: `mask_api_key(key)` redacts secret key bytes for secure logging (e.g. `cohe...3YtzwC`).

### 3.2 Public Contract
```python
class APIKeyRotator:
    def __init__(self, keys: Sequence[str], provider_name: str = "API") -> None: ...

    @classmethod
    def from_env(
        cls,
        prefix: str,
        environ: Mapping[str, str] | None = None,
        provider_name: str | None = None,
    ) -> APIKeyRotator: ...

    async def candidates(self, max_attempts: int) -> tuple[str, ...]: ...

    @property
    def keys(self) -> tuple[str, ...]: ...
```

---

## 4. Reranker Architecture (`RerankerAdapter`)

**Module:** `cowork_agent.integrations.rag.reranker` (supersedes `jina_reranker.py`)

### 4.1 Dispatching Strategy
The adapter inspects `RERANKER_MODEL` (configured via `RerankerSettings`):
1. **Cohere Provider** (`rerank-v*`, `cohere-*`):
   - **Endpoint:** `https://api.cohere.com/v2/rerank`
   - **Headers:** `Authorization: Bearer <KEY>`, `Content-Type: application/json`
   - **Payload:**
     ```json
     {
       "model": "rerank-v4.0-fast",
       "query": "<query_text>",
       "documents": ["<doc1>", "<doc2>"],
       "top_n": 5
     }
     ```
   - **Response Format:** Validates `results: [{"index": int, "relevance_score": float}]`.
2. **Jina Provider** (`jina-*`):
   - **Endpoint:** `https://api.jina.ai/v1/rerank`
   - **Payload:** Existing Jina API contract with `return_documents: false`.

### 4.2 Rate Limit Failover & Degraded Fallback
- When making HTTPS calls, if an HTTP 429 (Rate Limit / Quota Exceeded) status or rate limit exception occurs, `RerankerAdapter` logs a warning with the masked key and immediately attempts the next key from `rotator.candidates(...)`.
- If all key attempts fail or network errors occur, `RerankerAdapter` returns the un-reranked candidate list intact, ensuring RAG retrieval degrades gracefully without raising runtime errors (§12.3).

---

## 5. Integration & Migration Points

1. **`cowork_agent.config.RerankerSettings`**:
   - `from_env(environ)` reads `RERANKER_MODEL` (default: `rerank-v4.0-fast`).
   - Automatically determines key prefix (`COHERE_API_KEY` for Cohere models, `JINA_API_KEY` for Jina models).
   - Instantiates `APIKeyRotator.from_env(prefix, environ)`.
2. **`cowork_agent.integrations.rag.bootstrap`**:
   - Updates `build_semantic_memory` to instantiate `RerankerAdapter(RerankerSettings.from_env())`.
3. **`cowork_agent.integrations.llm.providers.gemini`**:
   - Refactors `GeminiKeyRotator` to use `APIKeyRotator.from_env(prefix="GEMINI_API_KEY")`.

---

## 6. Verification Suite

1. **`tests/unit/integrations/test_key_rotation.py`**:
   - Key discovery with `COHERE_API_KEY`, `COHERE_API_KEY2`..`COHERE_API_KEY6`.
   - Key discovery with `GEMINI_API_KEY_1`..`GEMINI_API_KEY_6`.
   - Round-robin index advancement under concurrent calls.
   - Validation for empty / invalid keys.
2. **`tests/unit/integrations/test_reranker.py`**:
   - Cohere reranker JSON serialization and score application.
   - Jina reranker backward compatibility.
   - HTTP 429 failover to secondary rotated API key.
   - Safe degraded fallback when all keys fail.
3. **Static Checks**:
   - `python -m ruff check .`
   - `python -m mypy src`
   - `python -m pytest tests/unit/integrations -q`
