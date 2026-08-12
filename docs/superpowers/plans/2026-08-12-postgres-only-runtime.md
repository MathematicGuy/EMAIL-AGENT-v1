# Postgres-only Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the Redis runtime and run durable work using Supabase Postgres, private Storage, and Qdrant Cloud only.

**Architecture:** FastAPI uses the established in-memory bounded chat buffer, while Postgres remains authoritative for sessions and jobs. `mail-todo-worker` polls atomically claimable Postgres digest/document jobs; it never needs Redis or carries source/text data in a queue.

**Tech Stack:** FastAPI, psycopg, Supabase Postgres/Storage, Qdrant async client, pytest, ruff, mypy.

## Global Constraints

- No `redis` import, optional dependency, `REDIS_URL`, or Redis state after this plan.
- Do not persist raw chat turns, document bytes, extracted content, prompts, Gmail bodies, or copied chunks in Postgres.
- Keep the immutable project source path and Qdrant project ACL filters.
- Work directly on `feat/supabase-integrate`, as the user explicitly requested.

---

### Task 1: Restore no-Redis FastAPI composition

**Files:** `src/cowork_agent/app.py`, `src/cowork_agent/features/ai_chat/session_buffer.py`, `src/cowork_agent/config.py`, `pyproject.toml`, `.env.example`; existing API/config tests.

- [x] Write failing tests proving a configured `DATABASE_URL` does not require `REDIS_URL` and selects `InMemoryChatSessionBuffer`.
- [x] Run the focused test and observe the current Redis-required failure.
- [x] Remove Redis client/queue startup and shutdown code; leave `run_queue` and `project_document_queue` unset; remove Redis dependency/config example.
- [x] Run focused tests, ruff, and mypy; commit `refactor(runtime): remove Redis API composition`.

### Task 2: Postgres job discovery and document completion

**Files:** `src/cowork_agent/persistence/repositories/projects.py`, `src/cowork_agent/api/projects.py`, `src/cowork_agent/orchestration/project_document_queue.py` (delete); persistence/API tests.

- [x] Write failing tests proving upload completion creates a `queued` job without a queue dependency, and the repository can atomically list one claimable document job.
- [x] Run focused tests and observe the missing Postgres poll interface.
- [x] Delete Redis document queue use; add metadata-only `next_claimable_job()` query to the repository. Keep `claim_job()` as the CAS authority.
- [x] Run focused tests, ruff, and mypy; commit `feat(projects): dispatch document jobs from Postgres`.

### Task 3: Postgres polling worker for documents and digests

**Files:** `src/cowork_agent/orchestration/worker.py`, new `src/cowork_agent/orchestration/postgres_poller.py`, `src/cowork_agent/orchestration/redis_queue.py` (delete), tests.

- [x] Write failing unit tests for one polling iteration: it discovers a queued digest/document ID, invokes the executor, and does nothing when empty.
- [x] Run tests and observe the absent poller.
- [x] Implement an interval-bounded poller with safe transient error retry. Add a Postgres query for the next queued digest run and keep existing CAS execution claim/recovery semantics.
- [x] Compose both polling workers in `mail-todo-worker`; remove Redis imports, URL checks, and Redis cleanup.
- [x] Run focused tests, ruff, mypy, and commit `feat(worker): poll Supabase jobs without Redis`.

### Task 4: Regression proof and operational docs

**Files:** `.env.example`, `README.md` or deployment docs, `docs/superpowers/plans/2026-08-12-supabase-project-documents.md`.

- [x] Write/extend regression tests for Postgres-only configuration and project document completion.
- [x] Run all affected API, persistence, worker, storage, and Qdrant tests; database-backed cases may skip only without `PG_TEST_URL`.
- [x] Run ruff/mypy on every changed source file and `uv lock --check`.
- [x] Document the exact production variables: no `REDIS_URL`; commit `docs: document Postgres-only deployment`.
