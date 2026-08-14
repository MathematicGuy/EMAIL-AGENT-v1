# ADR-008 — Turbovec + Postgres FTS project document plane

- Status: Accepted
- Date: 2026-08-14
- Supersedes: ADR-007 clause 4 (Qdrant collection + payload ACL filter) and clause 5's
  "extracted text is retained only in the Project Qdrant collection"
- Relates to: PRD-v4 (pluggable RAG providers), ADR-006, ADR-009 (company knowledge plane, not
  yet written)
- Follow-ups: [ISSUES-qdrant-retirement.md](../ISSUES-qdrant-retirement.md)

## Context

Project documents live in a Qdrant Cloud collection whose payload carries chunk text, page range,
and section, plus a six-condition ACL filter enforcing tenant isolation
(`project_documents.py:427`). Qdrant is the only component of the runtime needing an external
cluster; everything else already runs on Supabase Postgres and Storage.

Turbovec (4-bit TurboQuant `IdMapIndex`) is already a dependency and already backs the company
knowledge plane. It is a pure quantized vector index — `uint64` external IDs plus 4-bit codes, no
payload, no filtering except a caller-supplied allowlist. Verified against the library docs:
`search(queries, k, allowlist=uint64[])` filters inside the kernel and guarantees up to `k`
results *from the allowed set* (pre-filtering, not post-filtering); `remove(id)` is O(1);
`.tvim` persists the slot→ID mapping.

The initial sketch put BM25 in-process. That would have required every active project's chunk
text resident in the API process, since `BM25SearchAdapter` indexes text at construction
(`hybrid.py:58,84`). Supabase's documented hybrid-search recipe does the lexical leg in SQL
instead — a `tsvector` generated column ranked by `ts_rank_cd(fts, websearch_to_tsquery(...))`,
fused by RRF. That removes the memory problem and puts the ACL and the lexical leg in the same
query.

## Decision

1. **Chunk text becomes durable in Postgres.** Migration `012_project_document_chunks.sql` stores
   `chunk_id`, `document_id`, `project_id`, `chunk_index`, `text`, `page_start`, `page_end`,
   `section`, plus a `vector_id bigint` and an `fts tsvector GENERATED ALWAYS AS
   (to_tsvector(...)) STORED` column with a GIN index. This reverses ADR-007 clause 5.
2. **`vector_id` is the Turbovec external ID.** Stable across rebuilds, the argument to
   `IdMapIndex.remove()`. `chunk_id` remains the public, citation-facing identifier.
3. **The ACL and the lexical leg are one SQL query.** The `WHERE` clause carries all six ADR-007
   conditions — workspace, user, project, ready status, expiry, optional document IDs — and
   returns both the eligible `vector_id[]` and their `ts_rank_cd` ordering. The allowlist is
   therefore computed before any embedding I/O, preserving ADR-007's ordering invariant.
4. **Dense leg is a per-project Turbovec index**, searched as
   `search(query, k, allowlist=eligible_vector_ids)`. Physical per-project files make
   cross-tenant leakage structurally impossible; the allowlist enforces the remaining five
   conditions exactly.
5. **Fusion is RRF in Python**, reusing `rrf.py`. Dense ranks originate outside the database, so
   fusion cannot happen in SQL. Only ranks are fused, never raw scores — which is why
   `ts_rank_cd` not being BM25 does not matter: its calibration never propagates, only its
   ordering does.
6. **Text and citations are hydrated in one follow-up query** on the fused `vector_id[]`.
7. **`.tvim` snapshots are owned by `mail-todo-worker` and synced through Supabase Storage** so
   `mail-todo-api` can read them across process and host boundaries. Writes are tmp-file plus
   atomic rename. The API invalidates its per-project index cache on `projects.updated_at`.
8. **Qdrant is quarantined, not deleted.** It stays in the tree solely as the float32 control
   group for the recall gate, marked `RETAINED, NOT WIRED` in its docstring, dropped from
   `rag/__init__.py` exports, and released from the `ProjectVectorStore` protocol so the protocol
   can evolve without dragging dead code. Deletion is tracked as Q-1 with a stated expiry.
9. **Chunk rows are hard-deleted, never soft-flagged.** Deleting a document removes its
   `project_document_chunks` rows in the same transaction as its metadata, and
   `document_deletion_audits` gains a `chunks_outcome` column alongside the renamed
   `vector_store_outcome`. Deletion now has two targets — Postgres rows and the project's
   `.tvim` entries via `IdMapIndex.remove()` — and the audit must record both. Backup aging is
   out of our control and unaffected by this clause.

## Alternatives Considered

### In-process BM25 over project chunk text
- Pros: reuses `BM25SearchAdapter` and `HybridSemanticMemory` as written; true BM25 scoring.
- Cons: requires every active project's full chunk text in API RAM, plus a second cache with its
  own invalidation. The 4-bit quantization would have been saving vector RAM while text RAM grew
  unbounded.
- Rejected: Postgres FTS gives the same ranks for zero resident memory, and shares the ACL query.

### pgvector for the dense leg
- Pros: one datastore; filtering, text, and vectors in a single query; no snapshot transport.
- Cons: not evaluated on this corpus; abandons the 4-bit memory advantage and the existing
  Turbovec investment.
- Deferred, not rejected — Q-7. Revisit if snapshot transport proves painful.

### ParadeDB `pg_search` for true in-database BM25
- Unverified on Supabase managed Postgres. Not planned around — Q-7.

### Single Turbovec index with allowlist covering every tenant condition
- Pros: one file, one cache.
- Cons: tenant isolation becomes a pure code invariant with no physical backstop.
- Rejected: per-project files make leakage structurally impossible.

### Delete the whole `.tvim` on document deletion
- Rejected: `remove(id)` is O(1) by external ID, so per-chunk deletion is available.

### Delete Qdrant now
- Rejected for sequencing only, not on merit. `scripts/evaluate_retrieval.py` is the instrument
  that proves this migration did not regress, and Qdrant is its float32 reference. Deleting the
  control group before running the experiment leaves the change unverifiable.

## Consequences

- Private document text now lives in Postgres and therefore in Supabase's automated backups and
  PITR window. This is a **second copy** of data already persisted — the source PDF is in
  Supabase Storage under ADR-007 clause 5 — so the marginal exposure is small. The real cost is
  that document deletion now has two targets, which decision 9 addresses.
- `scripts/backup_restore_chat_memory.py`'s FR-16 justification ("semantic RAG content is never
  affected by table-level backup/restore") stops holding and must be rewritten. Q-8.
- `HybridSemanticMemory` is not reusable here: corpus-fixed at construction, consumes
  `KnowledgeDocument`, returns `SemanticChunk` which has no page range or filename. The project
  path needs its own composition to keep citations intact.
- The company knowledge plane is untouched by this ADR. Its retirement is three separable
  decisions — endpoints, chat evidence source, email action plan grounding — deferred to ADR-009
  and blocked on the recall gate, since its corpus is what the gate measures. Q-5.
- PRD-v4's premise (switchable Qdrant/Turbovec providers) is contradicted by the intent to delete
  Qdrant. It must be marked Superseded or rescoped. Q-8.

## Links

- [ADR-007](ADR-007-project-scoped-classifier-gated-user-documents.md)
- [PRD-v4](../prds/PRD-v4-pluggable-hybrid-rag-providers.md)
- [Qdrant retirement follow-ups](../ISSUES-qdrant-retirement.md)
- [Target Architecture](../../docs/architectures/TARGET-ARCHITECTURE.md)
- [Turbovec snapshot storage](../../docs/references/understand/supabase-turbovec-snapshot-storage.md)
- Supabase hybrid search recipe: `apps/docs/content/guides/ai/hybrid-search.mdx`
- Turbovec API: https://github.com/ryancodrai/turbovec/blob/main/docs/api.md
