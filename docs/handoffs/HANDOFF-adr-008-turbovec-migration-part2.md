# HANDOFF — ADR-008 Turbovec migration, part 2

**Date:** 2026-08-14 · **Branch:** `dev` · **Base commit:** `c3d9ef7` · **Nothing committed yet.**

Continues `HANDOFF-adr-008-turbovec-migration.md`. Read that first for the architecture;
this file covers only what changed since, what is verified, and what is left.

The decision is made and recorded in `tasks/adr/ADR-008-turbovec-project-document-plane.md`.
**Do not relitigate it.** Your job is to finish the remaining wiring and cleanup.

---

## 1. State of the gates (measured, not assumed)

| Gate | Result |
|---|---|
| `uv run ruff check .` | **green** |
| `uv run mypy src` | **green** (129 files) |
| `uv run pytest -q` | **not re-run since the last edits** — see §4.1 |

Last full run before the final edits: `18 failed, 962 passed, 9 skipped, 5 errors`.
Of those, **12 failures + 4 errors are pre-existing on `c3d9ef7`** (corpus-slug drift:
`cap-lai-cccd` vs `cap_lai_cccd` in `tests/unit/fixtures/test_retrieval_golden.py`,
`tests/unit/scripts/test_evaluate_retrieval.py`,
`tests/unit/integrations/rag/test_rag.py::test_load_corpus_reads_the_committed_documents`,
`tests/integration/email_action_plan/test_rag_retrieval_golden.py`). **Do not try to fix
those here** — they are unrelated to ADR-008 and were failing before this work started.

Everything else has been fixed and verified individually (33 passed across the three
affected files, 43 passed across `tests/integration/api/`).

### Baseline methodology (reuse it — it caught a false alarm)

`git worktree add <scratchpad>/baseline HEAD` then run pytest from there. **Copy both
`config` and `.env` into the worktree first**, or the run silently takes the SQLite path
and every Postgres-dependent test passes for the wrong reason. Delete the `.env` copy and
`git worktree remove --force` when done (already done for the previous one).

---

## 2. What was completed this session

### 2.1 `app.py` — fully rewired

- Imports `HybridProjectDocumentStore` + `TurbovecProjectIndexStore` +
  `PostgresProjectDocumentChunkRepository`. `ProjectDocumentVectorStore` import gone.
- The lifespan block (~line 452) builds the index store, the hybrid store, and the
  retriever. It **reuses the existing `app.state.private_storage`** built at ~line 397 —
  do not add a second `httpx.AsyncClient`.
- `app.state.project_document_qdrant_client` → `app.state.project_document_index`.
- Shutdown no longer closes a Qdrant client (the index store owns no network handle).
- Health check `"qdrant"` → `"project_index"`, probing that `index_store.root` is
  writable (a `.tvim` is per-project and pulled on demand, so there is nothing else to
  probe without a project ID). `"project_index"` is in the `required` list.
- `TurbovecProjectIndexStore` gained a public `root` property for that probe.

### 2.2 `orchestration/worker.py` — fully rewired

- `AsyncQdrantClient`, `QdrantSettings`, and the `qdrant_client` close block are gone.
- Builds `HybridProjectDocumentStore(PostgresProjectDocumentChunkRepository(pool),
  TurbovecProjectIndexStore(document_settings.index_root, storage=private_storage, ...),
  GeminiEmbeddingAdapter(embedding), ...)`.
- The `ensure_collection()` startup probe is gone. **`document_settings.startup_timeout_ms`
  is now unused in this file** — decide whether it still has a purpose or should be
  dropped from `UserDocumentsSettings`.

### 2.3 Qdrant project plane **deleted**, per the user's explicit instruction

> "after all tests gate passed, you allow to delete the stale Qdrant code (i want to
> remove it completely from the codebase and even from eval)"

Deleted outright (not quarantined — this supersedes decision 8 of ADR-008 and issues
#4/#6 in `tasks/ISSUES-qdrant-retirement.md`):

- `src/cowork_agent/integrations/rag/project_documents_qdrant.py`
- `tests/unit/integrations/test_project_document_qdrant.py`
- `scripts/cutover_project_documents.py`
- `QdrantSettings.project_collection_name` and the `QDRANT_PROJECT_COLLECTION` read

**Scope boundary you must respect:** `rag/qdrant.py` (`QdrantSemanticMemory`) is the
**company knowledge plane**, still live via `integrations/rag/bootstrap.py:25,109`, and
is *not* retired by ADR-008. `scripts/evaluate_retrieval.py`'s Qdrant arm is that same
company plane — it is the eval's control group, not project-document code. Removing it
would gut a live subsystem and the eval harness. **Confirm with the user before touching
either.** The user's "even from eval" most plausibly meant the project-plane control
group, which never existed in the eval to begin with.

### 2.4 Tests

- **New:** `tests/unit/integrations/test_project_documents_hybrid.py` (11 tests, all
  passing). Covers: the six ACL conditions reach Postgres *before* embedding; an empty
  allowlist short-circuits so no unfiltered `.tvim` search can happen; the dense leg only
  sees IDs that passed SQL; incomplete tenant scope raises; text persists before vectors;
  deletion hits the index before the text; retry reuses the authorized vector *and* still
  passes `query=`; evidence deleted mid-query is dropped; one deadline spans both stores;
  `ProjectIndexUnavailable` degrades with `reason_code="index_unavailable"`.
- `tests/unit/test_config.py` — the collection-name test became
  `test_project_documents_read_the_turbovec_index_root`.
- `tests/unit/orchestration/test_project_document_worker.py` — `mark_document_ready`
  removed from the fake; the test now asserts the vector store has *no* readiness hook.

### 2.5 A real bug fixed that was **not** part of ADR-008

`config.py:load_runtime_environment` had uncommitted working-tree edits changing both
`load_dotenv(...)` calls to `override=True`. That makes `.env` clobber the process
environment, so `monkeypatch.setenv("DATABASE_URL", "")` in
`tests/integration/api/test_principal_boundary.py` was ignored and **8 integration tests
reached the real Supabase database**, failing with
`psycopg.errors.InvalidTextRepresentation: invalid input syntax for type uuid: "owner@example.com"`.

Reverted to `override=False` with a comment explaining why. **Tell the user** — this was
their (or another agent's) uncommitted change, and if it was deliberate, the motivation
needs a different fix than `override=True`.

---

## 3. Files map

**New (untracked):**
```
src/cowork_agent/integrations/rag/project_index.py            TurbovecProjectIndexStore
src/cowork_agent/persistence/migrations/012_project_document_chunks.sql
src/cowork_agent/persistence/migrations/012_project_document_chunks.down.sql
src/cowork_agent/persistence/repositories/project_document_chunks.py
tests/unit/integrations/test_project_documents_hybrid.py
tasks/adr/ADR-008-turbovec-project-document-plane.md
tasks/ISSUES-qdrant-retirement.md
```

**Modified:** `app.py`, `config.py`, `integrations/rag/project_documents.py`,
`integrations/storage/supabase.py` (new `upload_file`),
`orchestration/project_document_worker.py`, `orchestration/worker.py`,
`persistence/repositories/projects.py`, `tests/unit/test_config.py`,
`tests/unit/orchestration/test_project_document_worker.py`.

**Not mine, already dirty at session start:** `config`,
`docs/references/ingestion-pipeline-brainstorming.md`,
`tasks/adr/ADR-007-...md`, `tasks/todo.md`.

---

## 4. Remaining work, in order

### 4.1 Re-run the full gate (do this first)
```
uv run ruff check .
uv run mypy src
uv run pytest -q
```
Expect exactly the 12 pre-existing corpus failures + 4 errors listed in §1. Anything else
is a regression from this work.

### 4.2 Purge the remaining `QDRANT_PROJECT_COLLECTION` references
The setting no longer exists in code. These still name it:
```
.env.example:113
docs/architectures/TARGET-ARCHITECTURE.md:533
docs/superpowers/specs/2026-08-12-postgres-only-runtime-design.md:42
tasks/specs/SPEC-chat-with-user-documents.md:385, 677
tasks/prds/PRD-v4-pluggable-hybrid-rag-providers.md:61   (names ProjectDocumentVectorStore)
docs/superpowers/plans/2026-08-13-pluggable-hybrid-rag-providers.md:15  (same)
```
Add `USER_DOCUMENTS_INDEX_ROOT=var/project-indexes` to `.env.example` in its place.
**Language rule:** `tasks/` PRDs and SPECs are written in Vietnamese; `docs/` stays
English. Match the surrounding file.
**Secret rule:** never put a real key *or* a real hostname in `src/`, tests, or
`.env.example`.

### 4.3 `tests/README.md`
It is the routing index — a route table plus invariant ownership. It still points at the
deleted `test_project_document_qdrant.py`. Repoint to
`test_project_documents_hybrid.py` and record which file now owns the cross-project
isolation invariant.

### 4.4 Apply migration 012 against the real database
`persistence/migrate.py` applies `.sql` files in filename order; `.down.sql` companions
are never auto-applied. Migration 012 creates `project_document_chunks` and **renames
`document_deletion_audits.qdrant_outcome` → `vector_store_outcome`** plus adds
`chunks_outcome`. It has never run against Supabase. Applying it is outward-facing and
hard to reverse — **confirm with the user before running it**, and note that any row
written by the old code path uses the old column name.

### 4.5 Reconcile the tracking artifacts with the deletion
`tasks/ISSUES-qdrant-retirement.md` and GitHub issues **#4–#11** still describe a
quarantine-then-delete plan. §2.3 skipped straight to deletion. Update them.
**Constraints:** the repo `MathematicGuy/EMAIL-AGENT-v1` is **PUBLIC** — anything posted
to its issues is publicly visible. The user's standing decision: *"Keep all Issues local
and on github for now."* The Linear agent must not close, edit, or mirror these issues,
must not enable Linear's GitHub sync, and may only write under `docs/handoffs/`.

Also correct **ADR-008 decision 8**, which names the wrong module: it says to quarantine
`rag/qdrant.py`, but that file is the live company plane. The project-plane store was the
actual target, and it is now deleted.

### 4.6 Open question to put to the user
`UserDocumentsSettings.startup_timeout_ms` lost its only consumer (§2.2). Keep or drop?

---

## 5. Facts that are easy to get wrong

- **Turbovec `IdMapIndex`, verified live against `turbovec 0.8.0`:** `search` raises
  `KeyError: 'allowlist contains id(s) not present in index: [...]'` if *any* allowlist ID
  is absent, raises `ValueError` on an empty allowlist, and `add_with_ids` raises
  `ValueError` on a duplicate ID. `project_index.py` handles all three (`contains()`
  intersection before search; `remove()` before `add`). Do not "simplify" those away.
- **`vector_id` is deliberately stable across re-ingest** — `UNIQUE (document_id, chunk_id)`
  + `ON CONFLICT DO UPDATE`. That stability is what lets `IdMapIndex.remove()` find the
  right entry later.
- **RRF consumes ranks, not scores.** `ts_rank_cd` not being true BM25 therefore never
  propagates. Measured, do not re-derive: unweighted RRF hybrid was *worse* than
  dense-only until a reranker was added; 4-bit Turbovec = 88.64% Recall@5 vs 99.6% float32.
- **`ProjectDocumentEvidence.score` is now a fused RRF score, not a cosine similarity.**
  The only consumer is `features/ai_chat/controller.py:129` (`relevance_score`, display
  only).
- **`ProjectDocumentEvidence` carries private user document text.** ADR-007's rule that it
  must never leak into a company-plane path still binds.
- **Always `uv run`** — an Anaconda Python on PATH shadows the venv. If you see
  `warning: Invalid SSL_CERT_FILE. Path does not exist: E:\CODE\Anaconda/ssl/cacert.pem`,
  clear `SSL_CERT_FILE` for the command (`SSL_CERT_FILE= uv run ...`); a stale value has
  previously produced ~23 phantom test failures.
- **Commit only when the user asks.** Nothing here is committed.
