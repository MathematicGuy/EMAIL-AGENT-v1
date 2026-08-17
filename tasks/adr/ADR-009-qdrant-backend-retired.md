# ADR-009 — Qdrant backend retired; company knowledge plane stays

- Status: Accepted
- Date: 2026-08-14
- Relates to: ADR-008, PRD-v4 (superseded as a switchable-provider PRD)
- Follow-ups: project-document reranker remains [issue #9](https://github.com/MathematicGuy/EMAIL-AGENT-v1/issues/9);
  pgvector / ParadeDB remain [issue #10](https://github.com/MathematicGuy/EMAIL-AGENT-v1/issues/10)

## Context

ADR-008 moved the **project-document** plane off Qdrant. The company knowledge
plane (`data/extracted/*.md`, Email RAG, Chat Type 4) still used
`QdrantSemanticMemory` as an optional `RAG_STORE_PROVIDER=qdrant` path and as
the eval float32 control group.

GitHub #8 listed three separable amputations of the *company knowledge plane
itself* (HTTP knowledge endpoints, chat company evidence, email action-plan
grounding). Those are not the same as deleting the Qdrant **library**. The
operator decision is: remove Qdrant from the codebase; keep company RAG on
Turbovec.

## Decision

1. **Delete the Qdrant backend.** `integrations/rag/qdrant.py`,
   `QdrantSettings`, `qdrant-client`, the eval `qdrant` retriever, and the
   `QDRANT_*` env block are gone. `RAG_STORE_PROVIDER=qdrant` degrades to
   `NullSemanticMemory` with a retired-provider warning.
2. **Keep company RAG (A, B, C of #8).**
   - A — `/v1/mail-todo/knowledge/{ready,documents,chat}` stay.
   - B — chat may still cite company knowledge via `SemanticChatMemoryAdapter`.
   - C — Email Action Plan `RETRIEVE_RAG` stays; `DigestWorker` still receives
     `semantic_memory=` from `build_semantic_memory()`.
3. **Production store is Turbovec hybrid** (`TurbovecSemanticMemory` + BM25 +
   RRF). In-repo dense memory remains an eval/offline fallback only.
4. **Do not retire the committed corpus.** `data/extracted/*.md` and
   `mail-todo-ingest-knowledge` remain the company knowledge source.

## Consequences

- There is no in-tree float32 Qdrant control group. Quality is measured on
  `dense` / `turbovec` / `hybrid` / `hybrid_turbovec`.
- Existing Qdrant Cloud collections (company or leftover project) are not
  read. Delete them in the cloud console if they still exist.
- PRD-v4's switchable Qdrant/Turbovec premise is superseded.
