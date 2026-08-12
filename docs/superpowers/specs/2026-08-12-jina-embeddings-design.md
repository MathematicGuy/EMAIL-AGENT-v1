# Jina Embeddings Design

## Goal

Replace Gemini embeddings in the RAG retrieval path with Jina embeddings so
Gemini embedding quota is no longer consumed by corpus indexing or queries.
Gemini and Groq continue to provide LLM behaviour unchanged.

## Selected approach

Use Jina's HTTPS embeddings endpoint through the existing standard-library
transport pattern. This adds no SDK dependency and keeps the HTTP boundary
injectable for deterministic tests. The adapter sends `jina-embeddings-v3`,
`embedding_type: float`, and task-specific values: `retrieval.passage` for
indexing and `retrieval.query` for retrieval.

The Jina API accepts `POST https://api.jina.ai/v1/embeddings`, bearer
authentication, string-array input, a model name, optional task, and returns
embedding items in `data`. `jina-embeddings-v3` has 1024 output dimensions.
Sources: https://jina.ai/en-US/embeddings/ and
https://jina.ai/news/jina-embeddings-v3-a-frontier-multilingual-embedding-model/

## Configuration and migration

Introduce `JinaEmbeddingSettings`, loaded from `.env`:

- `JINA_API_KEY` is required for embeddings and must never be represented in
  logs or object reprs.
- `JINA_EMBEDDING_MODEL` defaults to `jina-embeddings-v3`.
- `JINA_EMBEDDING_DIMENSIONS` defaults to `768` and is sent to Jina. The
  Jina API supports output truncation, which preserves the repository's
  existing Qdrant default dimension.
- `JINA_EMBEDDING_TIMEOUT_SECONDS` defaults to `30`.

`QDRANT_VECTOR_SIZE` remains 768 by default and must equal
`JINA_EMBEDDING_DIMENSIONS`. An existing
collection built with Gemini vectors is not compatible: set
`QDRANT_REINDEX=true` once during migration, which rebuilds the collection
using only Jina vectors. The in-repo hybrid fallback likewise rebuilds in
memory at each process startup, so it needs no separate migration.

## Components and data flow

`JinaEmbeddingAdapter` implements an evolved `EmbeddingPort` whose `embed()`
method accepts an explicit `task` value. Indexing calls it with
`retrieval.passage`, while query and MMR calls use `retrieval.query`.
Deterministic fakes accept the same keyword while preserving their current
offline vectors.

The RAG bootstrap creates exactly one adapter and supplies it to Qdrant and
the in-repo hybrid memory. The adapter validates response item count, indexes,
finite numeric vector elements, and configured dimension. Any upstream error
preserves existing bootstrap degradation to `NullSemanticMemory`; no text or
credential is logged.

## Testing

Offline unit tests will exercise configuration validation, request headers and
payloads, document/query task selection, ordering, malformed responses,
timeouts, and missing API keys. Existing RAG/Qdrant tests will be updated to
inject the new adapter seam, retaining their deterministic hashing embedder.
The focused RAG tests, Ruff, and strict mypy checks will be run after the
change.
