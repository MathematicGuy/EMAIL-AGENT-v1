# Supabase Project Documents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add private Supabase Storage-backed project documents with durable Postgres metadata, async ingestion state, project-scoped Qdrant retrieval, retention, and deletion audit.

**Architecture:** Postgres is authoritative for projects, document state, jobs, expiry, authorization, and deletion audit. FastAPI creates opaque project/document IDs and server-authorized Storage signed URLs; it never exposes a Supabase credential. A worker processes queued documents and writes chunks only to a distinct Qdrant project collection after a filterable metadata record is ready. The company collection is untouched.

**Tech Stack:** FastAPI, psycopg, Redis Streams, Supabase Storage HTTP API, Qdrant async client, existing PDF/DOCX extractors, pytest, ruff, mypy.

## Global Constraints

- Use `SUPABASE_URL` and `SUPABASE_SECRET_KEY` only in server configuration; neither publishable key nor secret reaches browser code.
- Storage bucket is private and canonical keys are `workspace/{workspace_id}/user/{user_id}/project/{project_id}/document/{document_id}/source`.
- Never write uploaded bytes, extracted text, OCR output, prompts, full chat transcripts, Gmail bodies, or copied chunks to Postgres, logs, fixtures, traces, or TaskEpisodes.
- `received -> extracting -> indexing -> ready` is the only successful document state path. Partial or empty extraction is never indexed.
- Project retrieval uses a separate Qdrant collection and its workspace, user, project, ready, and expiry filters are created before embedding.
- Removing or expiring a document first makes it non-retrievable in Postgres, then retries Qdrant and Storage deletion while recording each outcome.

---

### Task 1: Project/default-session and document metadata migrations

**Files:** migrations `007_projects_documents.sql` and `.down.sql`; project/document repository; migration/repository tests.

- [ ] Write RED tests proving: a first owner gets one default project; durable sessions receive a valid `project_id`; duplicate digest in one project resolves to one document; foreign user/workspace queries get no record; SQL has no raw-content columns.
- [ ] Run focused tests; expected missing repository/migration failures.
- [ ] Create `projects`, `project_documents`, `document_ingestion_jobs`, and `document_deletion_audits`; backfill each existing durable chat session to its owner’s default project; add `project_id` to sessions and task episodes.
- [ ] Implement Postgres repository methods `default_project`, `create/list/require_project`, `create_or_get_document`, `require_document`, `mark_upload_completed`, `claim_job`, `transition_document`, `begin_deletion`, and `record_deletion_audit`.
- [ ] Run focused tests and commit `feat(projects): persist project document metadata`.

### Task 2: Private Storage client and FastAPI document API

**Files:** Supabase storage settings/client, project router/API integration, config/env tests, storage adapter tests.

- [ ] Write RED tests for canonical keys, secret-only API headers, an authorized signed upload request, 404 for foreign project/document, and an expiration-safe signed download URL.
- [ ] Run tests; expected missing client/routes failures.
- [ ] Implement `SupabasePrivateStorage` via server-side `httpx`: signed upload, object metadata, signed download, and delete. Translate non-sensitive failures to `StorageUnavailable`; never log response payloads or document names.
- [ ] Add project create/list/delete, document upload-initiate/complete/list/status/download/delete endpoints. The client supplies only content metadata/digest; FastAPI creates all IDs/paths.
- [ ] Run focused API/settings/storage tests and commit `feat(storage): add private project document URLs`.

### Task 3: Async ingestion and project Qdrant collection

**Files:** document queue/worker, page-aware chunker, project Qdrant adapter, worker integration, focused unit/integration tests.

- [ ] Write RED tests for complete native DOCX/PDF extraction, rejected empty/unsupported/encrypted content, page ranges, state transition guards, and a pre-embedding ACL filter containing workspace/user/project/ready/unexpired constraints.
- [ ] Run tests; expected missing queue/worker/Qdrant adapter failures.
- [ ] Implement a metadata-only Redis document job, source download to a secure temporary file, existing guarded extraction, OCR failure behavior, page-aware chunks, embeddings, distinct Qdrant collection upsert/delete, and retry-safe document/job state updates.
- [ ] Run focused ingestion/Qdrant tests; a real Redis/Qdrant run skips only without explicit test URLs. Commit `feat(rag): ingest project documents asynchronously`.

### Task 4: Chat project scope, retrieval, retention, and deletion proof

**Files:** chat scope/contracts/controllers/generation context, project retrieval adapter, retention coordinator, ADR-007, API/RAG tests.

- [ ] Write RED tests: `POST /sessions` defaults to the owned default project; a foreign project is 404; a ready document causes project retrieval every turn; company evidence remains separate; deleted/expired documents never pass the pre-embedding filter; TaskEpisodes preserve coordinate-only citations.
- [ ] Implement scope propagation, `project_document_evidence` context precedence, deterministic project retrieval, document purge/deletion audit retries, and ADR-007.
- [ ] Run project/chat/RAG focused tests plus RAG evaluation only when a configured evaluation baseline exists; record the result rather than inventing a quality score.
- [ ] Run ruff, targeted mypy, migration down/forward checks, commit `feat(rag): retrieve and retain project documents`.

## Plan self-review

- The work is deliberately ordered so project authorization and private upload URLs are deployable before extraction or Qdrant availability.
- It preserves the two semantic planes and the no-raw-text Postgres boundary.
- OCR remains a bounded worker concern; a missing configured provider produces the explicit safe failure state rather than partial indexing.
