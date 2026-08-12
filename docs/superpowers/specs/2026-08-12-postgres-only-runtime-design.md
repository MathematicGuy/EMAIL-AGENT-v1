# Postgres-only Runtime Design

## Goal

Run the Supabase integration using only Supabase Postgres, private Supabase
Storage, and Qdrant Cloud. Redis is not deployed or required.

## Decision

The project returns to its documented MVP working-memory tier:
`InMemoryChatSessionBuffer`. Short-term turns are bounded and are lost on API
restart; durable chat ownership, profiles, episodes, projects, and document
metadata remain in Supabase Postgres.

Both worker loops use Postgres polling with atomic compare-and-set claims:

1. A digest worker polls `digest_runs` in `queued` state, then invokes the
   existing `DigestWorker.execute(run_id)`, whose existing `claim` protects
   against duplicate processing.
2. A project-document worker polls `document_ingestion_jobs` in `queued` or
   retryable `failed` state. It keeps the existing `claim_job` state machine:
   `received -> extracting -> indexing -> ready`, with safe `failed` terminal
   results for controlled source/extraction failures.

Polling uses a small configurable interval (default 1 second). It never puts
source bytes, extracted text, prompt content, Gmail bodies, or credentials in
Postgres or logs.

## Data and Runtime Boundaries

```text
FastAPI
  Supabase Postgres: auth/session ownership, metadata, durable job state
  Supabase Storage: private PDF/DOCX original objects

mail-todo-worker
  polls Supabase Postgres -> downloads private source -> Qdrant Cloud

No Redis service, Redis client, Redis environment value, or Redis dependency.
```

`QDRANT_PROJECT_COLLECTION=project_documents` remains separate from
`QDRANT_COLLECTION=company_knowledge`. Project extraction requires the Gemini
embedding provider as in the current worker composition.

## Failure and Recovery

- A polling iteration catches and logs only safe exception type information,
  then retries on the next interval; it does not terminate the worker.
- A worker crash after a run claim is recovered by the existing stuck-run
  recovery path. A project job crash is made claimable again after an explicit
  lease timeout; ordinary controlled extraction errors transition to `failed`.
- API document completion writes only the Postgres ingestion job. The polling
  worker discovers it; no best-effort in-process background task is used.

## Scope

This replaces the Redis runtime added by the Supabase increments: Redis
Streams for digest/document jobs and `RedisChatSessionBuffer`. It does not
change Gmail OAuth, Supabase Auth policy, private Storage path convention,
Qdrant ACL filters, or company RAG.
