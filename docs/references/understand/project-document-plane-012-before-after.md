# Project-document plane — migration 012 before & after

**Date:** 2026-08-14  
**Status:** Operator note for the ADR-008 cutover  
**Related:** [ADR-008](../../../tasks/adr/ADR-008-turbovec-project-document-plane.md),
`src/cowork_agent/persistence/migrations/012_project_document_chunks.sql`,
`src/cowork_agent/integrations/rag/project_documents.py`,
`src/cowork_agent/integrations/rag/project_index.py`

This is the project-document plane only (user-uploaded PDFs in a Project).
Company Email RAG (`data/extracted/*.md`, `RAG_STORE_PROVIDER`) is a different
store and is unchanged by 012.

---

## 1. What 012 is — and is not

012 is a **Postgres schema** change. Applying it:

- creates the empty table `project_document_chunks`
- renames `document_deletion_audits.qdrant_outcome` → `vector_store_outcome`
- adds `chunks_outcome` (default `pending` on existing audit rows)

It does **not**:

- copy vectors out of Qdrant Cloud
- write any `.tvim` to disk or to Supabase Storage
- re-ingest existing PDFs
- delete the leftover Qdrant project collection

A `.tvim` appears later, when `mail-todo-worker` **ingests a document** on the
new path. Until then, documents that were `ready` under Qdrant stay `ready` in
metadata but have **zero searchable chunks**.

API and worker both call `apply_migrations()` on boot. The next process that
starts with `DATABASE_URL` set applies every pending `*.sql` in one transaction.
`*.down.sql` files are never auto-applied.

---

## 2. Before 012 (ADR-007 Qdrant project collection)

```text
Upload PDF ──► Supabase Storage          (original bytes, private)
               Postgres project_documents (metadata, status, expiry)
               Qdrant Cloud collection    (chunk TEXT + vector + ACL payload)
                                          one shared collection, filtered
                                          by payload at query time
```

| Concern | Where it lived |
|---|---|
| Original PDF | Supabase Storage |
| Ready / expiry / owner | Postgres `project_documents` |
| Chunk text + page range | Qdrant **payload** |
| Dense vector | Qdrant |
| ACL | Qdrant payload filter (six conditions), built before embed |
| Lexical search | None on this plane |
| Citations | Read back from the Qdrant payload |

`document_deletion_audits` recorded three outcomes: Postgres, **Qdrant**, Storage.

The `.tvim` file did not exist on this plane. Search was a network call to
Qdrant Cloud. The API and worker both talked to that cluster.

---

## 3. After 012 (ADR-008 hybrid)

```text
Upload PDF ──► Supabase Storage                 (original bytes, unchanged)
               Postgres project_documents       (metadata, status, expiry)
               Postgres project_document_chunks (text, pages, vector_id, fts)
               local var/project-indexes/{id}.tvim   (cache, 4-bit vectors)
               Storage project-indexes/{id}.tvim     (durable snapshot)

Query ──► Postgres ACL + FTS  (allowlist of vector_ids)
      ──► Gemini embed query
      ──► search local .tvim with that allowlist
      ──► RRF fuse ranks
      ──► hydrate text/pages from Postgres
```

| Concern | Where it lives now |
|---|---|
| Original PDF | Supabase Storage (unchanged) |
| Ready / expiry / owner | Postgres `project_documents` (unchanged) |
| Chunk text + page range | **Postgres** `project_document_chunks` |
| Lexical ranks | **Postgres** `ts_rank_cd` on generated `fts` |
| Dense vector | Per-project Turbovec `.tvim` |
| Durable `.tvim` | Supabase Storage key `project-indexes/{project_id}.tvim` |
| Local `.tvim` | `USER_DOCUMENTS_INDEX_ROOT` (default `var/project-indexes`) — cache only |
| ACL | **Postgres `WHERE`**, same six conditions, before embed |
| Citations | Hydrated from Postgres after fusion |
| Deletion audit | Postgres + **vector store** + Storage + **chunks** |

### Why search still uses a local `.tvim`

Turbovec is an in-process quantized index (`uint64` id + 4-bit code). It has
**no payload**: no text, no tenant fields, no ready/expiry. `search()` only
accepts an allowlist of ids.

- The local file is how the API **searches** (SIMD, no Qdrant Cloud).
- Postgres is how the API **authorizes** and **reads text**.
- Storage is how the API **gets the file** when this host does not have it
  (`mail-todo-worker` writes; `mail-todo-api` is a different process).

Searching the `.tvim` alone would return nearest vectors in that project,
including expired, still-indexing, and documents the user did not attach.

---

## 4. The six ACL conditions

Evaluated in **one SQL query** (`list_eligible`), **before** the query is
embedded. Only surviving `vector_id`s are passed to Turbovec.

| # | Condition | Stops |
|---|---|---|
| 1 | `documents.workspace_id = …` | Another workspace |
| 2 | `documents.user_id = …` | Another user's upload |
| 3 | `documents.project_id = …` | Another project (the `.tvim` is also one file per project) |
| 4 | `documents.id = ANY(selected)` | Searching the whole project when chat attached a subset |
| 5 | `status = 'ready'` and `deleted_at IS NULL` | Hits while extracting/indexing, or after delete started |
| 6 | `expires_at > now` | Past-retention documents |

The per-project `.tvim` physically contains only that project's vectors (#3).
Conditions 1, 2, 4, 5, 6 are invisible to Turbovec. An empty allowlist
short-circuits — the index is never searched unfiltered.

After fusion, text is hydrated from Postgres again so a document that flipped
to `deleting` / expired between the two queries is dropped.

---

## 5. What happens to data that already existed

| Already in the database | After 012 |
|---|---|
| `project_documents` rows (`ready`, `failed`, …) | Unchanged |
| Source PDFs in Storage | Unchanged |
| `document_deletion_audits.qdrant_outcome` values | Same strings, new column name `vector_store_outcome` |
| New column `chunks_outcome` on old audit rows | `'pending'` |
| Qdrant Cloud project-collection points | **Orphans.** New code never reads or deletes them |
| Chunk text that lived only in Qdrant payload | **Not copied.** Re-ingest to populate `project_document_chunks` |

Until re-ingest: chat over those documents returns empty evidence, not a
Qdrant fallback.

---

## 6. Rollback

`012_project_document_chunks.down.sql` drops `project_document_chunks` and
renames the audit column back. It is **manual**. Rolling back throws away
any chunk text written after 012; restore is re-extract from Storage PDFs.

---

## 7. Operator checklist after apply

1. Confirm `schema_migrations` contains `012_project_document_chunks.sql`.
2. Confirm `\d project_document_chunks` and
   `document_deletion_audits.vector_store_outcome`.
3. Re-ingest every project document that must be searchable.
4. Optionally delete the leftover Qdrant **project** collection in the cloud
   console (company-plane `QDRANT_COLLECTION=company_knowledge` stays).
5. Leave `USER_DOCUMENTS_INDEX_ROOT=var/project-indexes` as the local cache.
