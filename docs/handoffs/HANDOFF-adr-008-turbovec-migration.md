# Handoff — Plan & implement ADR-008 (Turbovec + Postgres FTS project document plane)

**Date:** 2026-08-14 · **Branch at handoff:** `dev` · **Status:** ADR Accepted, nothing built yet.

Your job is **planning and implementation**. The architecture decision is already made and
recorded; do not relitigate it. Everything below is verified against the codebase — file:line
references were checked, not inferred.

---

## 0. Mission in one paragraph

Move the project-document retrieval plane off Qdrant Cloud. Chunk text, page coordinates, and
the tenant ACL move into Supabase Postgres; the lexical leg becomes a `tsvector` column ranked
by `ts_rank_cd`; the dense leg becomes a per-project Turbovec 4-bit `.tvim` index searched with
a `uint64` allowlist; the two are fused with the existing RRF implementation. Qdrant stays in
the tree, quarantined, until a recall gate proves the new path didn't regress — then it's
deleted.

## 1. Coordination — another agent may be running

A second agent may be working from
[HANDOFF-linear-issue-tracking-setup.md](HANDOFF-linear-issue-tracking-setup.md). It is
sandboxed to Linear and `docs/handoffs/`. It will not touch `src/`, `tests/`, `scripts/`, or
`tasks/`. You own all of those. No coordination needed beyond knowing it exists.

**Also:** a concurrent `wgm` process has previously stashed this worktree mid-edit. If your
changes vanish, check `git stash list` before re-doing work.

---

## 2. Level 1 — Rules that always apply

From `AGENTS.md` and project convention:

- **Always `uv run`.** An Anaconda Python is on PATH and will shadow the venv. Verified:
  `uv run python -c "import turbovec"` resolves to `.venv`, bare `python` resolves to
  `E:\CODE\Anaconda`.
- Gates: `uv run pytest -q`, `uv run ruff check .`, `uv run mypy src`. All must pass.
- Never commit `.env`. No real key **or** real hostname in `src/`, tests, or `.env.example`.
- `tests/README.md` is the routing index — it owns the route table and invariant ownership.
  Read it before writing or running tests; update it when you add or delete a test file.
- Test suite performance is a maintained property: no subprocess for CLI tests, probe an
  external service once per session. Someone spent real effort taking this suite 111s → 19s.
- Docs language split: `tasks/` PRDs and SPECs are Vietnamese; `docs/` stays English. ADRs in
  `tasks/adr/` are English (follow ADR-007's shape).
- If a run produces a wall of phantom failures, check for a stale `SSL_CERT_FILE` in the
  environment before debugging the code. It has caused 23 fake failures before, and it breaks
  `uv` earlier than `conftest.py` can help.

## 3. Level 2 — The decision

**Authoritative:** `tasks/adr/ADR-008-turbovec-project-document-plane.md` — Accepted, 9
decisions. Read it in full before planning. It supersedes ADR-007 clause 4 (Qdrant payload ACL)
and clause 5's "extracted text is retained only in the Project Qdrant collection".

**Still binding:** `tasks/adr/ADR-007-project-scoped-classifier-gated-user-documents.md` —
everything except clauses 4 and 5. Especially: the classifier is the sole authority that may
originate retrieval; company evidence is never a fallback; citations are server-validated
coordinates; the chunker and Gemini `gemini-embedding-2` 3072-d config are unchanged.

**Deferred work:** GitHub Issues `#4`–`#11`, indexed at `tasks/ISSUES-qdrant-retirement.md`.
Critical path is **#4 → #5 → #6 → #11**. `#4` (quarantine markers) is cheap and should land
first so nobody extends the dead Qdrant path while you work.

**Out of scope, do not drift into:** retiring the company knowledge plane in any form (`#8`),
adding a reranker (`#9`), pgvector or ParadeDB (`#10`), deleting Qdrant before the gate (`#6`).

---

## 4. Level 3 — Source map

### Will be rewritten
| File | What's there now |
| :--- | :--- |
| `src/cowork_agent/integrations/rag/project_documents.py` | `ProjectDocumentVectorStore` (Qdrant, `:86`), `CanonicalProjectDocumentRetriever` (`:321`), the six-condition `_retrieval_filter` (`:427`), `ProjectDocumentChunk` (`:53`), `ProjectDocumentEvidence` (`:72`) |
| `src/cowork_agent/orchestration/project_document_worker.py` | `ProjectVectorStore` protocol (`:73`), `ProjectDocumentIngestionWorker` (`:105`), cleanup worker (`:286`) — **the original handoff missed this file entirely** |
| `src/cowork_agent/persistence/repositories/projects.py` | document repo; deletion audit writes at `:770,795,805` |

### Will be edited
| File | Why |
| :--- | :--- |
| `src/cowork_agent/app.py` | `:437-489` Qdrant client lifespan, `:591-593` close, `:619,652-670` health checks, `:465` store construction |
| `src/cowork_agent/orchestration/worker.py` | `:195-227` builds the store for the worker process, `:252` close |
| `src/cowork_agent/config.py` | `:249` project collection default, `:277-318` `QdrantSettings` |

### New
- `src/cowork_agent/persistence/migrations/012_project_document_chunks.sql` **and** its
  `.down.sql`. Every migration in this repo has a down file — match that. `012` is free; latest
  is `011_chat_history.sql`.

### Do not touch (quarantine, `#4`)
`src/cowork_agent/integrations/rag/qdrant.py`, `tests/unit/integrations/test_qdrant.py`,
`tests/integration/test_qdrant_integration.py`. They stay until the gate passes.

---

## 5. Verified facts — do not re-derive these

**Turbovec** (`turbovec 0.8.0`, in `.venv`; docs: `github.com/ryancodrai/turbovec/docs/api.md`):

```python
idx = IdMapIndex(dim=1536, bit_width=4)
idx.add_with_ids(vectors, np.array([1001, 1002], dtype=np.uint64))
scores, ids = idx.search(queries, k=10, allowlist=np.array([1003], dtype=np.uint64))
idx.remove(1002)          # O(1) by external id
assert 1003 in idx
idx.write("index.tvim"); loaded = IdMapIndex.load("index.tvim")
```

- `allowlist` filtering happens **inside the kernel** — pre-filter, not post-filter. It
  guarantees up to `k` results *from the allowed set*. This is what makes the ACL exact.
- `.tvim` persists the slot→id mapping, so IDs survive `write`/`load`.
- Vectors must be L2-normalized and padded to a multiple of 8. See `_pad_vector_dim` in
  `integrations/rag/turbovec_memory.py:34` — reuse it, don't rewrite it.

**Supabase hybrid recipe** (`apps/docs/content/guides/ai/hybrid-search.mdx`): a generated
`fts tsvector` column plus `row_number() over (order by ts_rank_cd(fts,
websearch_to_tsquery(query_text)) desc)`. Note it ranks with `row_number()` — **RRF consumes
ranks, never raw scores.** That's why `ts_rank_cd` not being true BM25 is acceptable: its
calibration never propagates, only its ordering does.

## 6. Gotchas that will bite you

1. **`HybridSemanticMemory` is not reusable here.** `integrations/rag/hybrid.py:58,84` fixes its
   corpus at construction and builds `BM25SearchAdapter` over in-memory text. It consumes
   `KnowledgeDocument` and returns `SemanticChunk`, which has **no `page_start`, `page_end`, or
   `filename`** — using it silently destroys citations. The project path needs its own
   composition. ADR-008 decision 5 exists because of this.
2. **The ACL is six conditions, not one.** `project_documents.py:427` — workspace, user,
   project, `document_id ∈ selection`, `document_status='ready'`, `expires_at > now`. Per-project
   `.tvim` files only cover *project*. The other five must be in the SQL `WHERE` that produces
   the allowlist. Getting this wrong is a cross-tenant leak.
3. **Ordering invariant.** ADR-007 requires the ACL be established *before* embedding I/O.
   `project_documents.py:193` has the comment explaining why. Preserve it: SQL first, embed
   second.
4. **`mark_document_ready` has no Turbovec analogue.** Today it flips a Qdrant payload field
   (`:160`). Ready-gating moves entirely into the SQL allowlist query.
5. **Two processes, one index.** `mail-todo-api` and `mail-todo-worker` are separate entrypoints
   (`pyproject.toml:47-48`). The worker writes `.tvim`, the API reads it. Sync via Supabase
   Storage per ADR-008 decision 7, and write tmp-then-rename or the API will load a torn file.
6. **Measured, not theoretical:** 4-bit Turbovec scored **88.64% Recall@5 vs 99.6% float32** on
   the company corpus, and **unweighted RRF hybrid measured *worse* than dense-only** until a
   reranker was added. Do not write "hybrid improves quality" into a plan as an assumption. It
   is the thing issue `#5` exists to measure.
7. **`scripts/evaluate_retrieval.py` imports `rag.qdrant` directly** (`:798`) and lists `QDRANT`
   in `RETRIEVERS` (`:76`). It is the instrument that validates your work. Don't break it.
8. **`document_deletion_audits`** needs both the `qdrant_outcome` → `vector_store_outcome` rename
   (`migrations/007_projects_documents.sql:94`) and a new `chunks_outcome` (ADR-008 decision 9).
   Chunk rows are hard-`DELETE`d in the same transaction as metadata — never soft-flagged.

## 7. What the earlier handoff got wrong

The doc at `%TEMP%\handoff-turbovec-qdrant-migration.md` seeded this work. It's mostly sound but
was corrected on these points — don't regress to it:

- It proposed **in-process BM25**. Rejected: needs every active project's text resident in API
  RAM, nullifying the 4-bit saving. ADR-008 uses Postgres FTS.
- It proposed **deleting Qdrant in Phase 4**. Rejected on sequencing — that deletes the control
  group for the gate that validates the change.
- Its migration schema had **no `uint64` vector ID**. Turbovec IDs are `uint64`; chunk IDs are
  `uuid5` strings. Without `vector_id bigint` there is nothing to join on.
- It bundled **retiring the company knowledge plane** into the same change. That's three separate
  amputations (endpoints / chat evidence source / email action plan grounding) and it's now
  issue `#8` with its own ADR pending.
- Its blast radius listed **13 files**; the real Qdrant surface is roughly 30 non-vendor files.
  It missed `orchestration/project_document_worker.py`, `rag/__init__.py`, all four `scripts/`,
  the frontend evidence union (`frontend/src/dashboard/types.ts:66`), and 9 test files.

---

## 8. Definition of done

- [ ] Migration `012` + `.down.sql` applied and reversible
- [ ] Ingestion writes chunks to Postgres and updates the project's `.tvim`
- [ ] Deletion removes chunk rows, calls `IdMapIndex.remove()`, and records both outcomes
- [ ] Retrieval returns `ProjectDocumentEvidence` with page range and filename intact
- [ ] All six ACL conditions enforced, with a test proving cross-project isolation
- [ ] `uv run pytest -q` · `uv run ruff check .` · `uv run mypy src` all green
- [ ] `tests/README.md` updated
- [ ] Issue `#5` recall numbers captured — this is what unblocks deleting Qdrant

## 9. When you're unsure

Surface it, don't guess. The user is the product owner, is a fresher intern who wants the
reasoning explained rather than the conclusion asserted, and has repeatedly brought
measurements that overturned an assumption. Push back if the ADR looks wrong once you're in the
code — but say so explicitly rather than quietly implementing something else.

Two things ADR-008 deliberately left unstated, which your plan must decide and state:
1. Whether `vector_id` is globally unique (`bigserial`) or per-project — affects whether one
   `.tvim` could ever be merged with another.
2. Whether the API rebuilds a missing `.tvim` from Postgres chunks on cache miss, or fails
   closed and returns `degraded=True`. `CanonicalProjectDocumentRetriever` already has the
   degradation vocabulary (`project_documents.py:346-352`).
