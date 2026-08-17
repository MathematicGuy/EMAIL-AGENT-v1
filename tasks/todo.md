### TODO List written by Human

---

### Document Loading (`document-loading`) — 2026-08-16

Plan: [PLAN-document-loading.md](plans/PLAN-document-loading.md)
Spec: [SPEC-document-loading.md](specs/SPEC-document-loading.md)

- [x] T1 Sanitizer + closed frontmatter helpers
- [x] T2 OCR `<!-- Page N -->` markers
- [x] T3 Manifest optional `title`
- [x] T4 Service wires sanitizer + frontmatter
- [x] T5 `load_corpus` frontmatter hygiene
- [x] T6 README route + focused verify (full suite: pre-existing golden slug drift)

### Golden IDs + page-aware load — 2026-08-16

Plan: [PLAN-golden-ids-and-page-aware-load.md](plans/PLAN-golden-ids-and-page-aware-load.md)

- [x] G1 Repair golden document IDs to hyphenated stems
- [x] P1 `split_markdown_pages`
- [x] P2 `KnowledgeChunk` + `load_corpus` coordinates
- [x] P3 `SemanticChunk` + retriever copy

### format-txt-md — 2026-08-16

Plan: [PLAN-format-txt-md.md](plans/PLAN-format-txt-md.md)

- [x] T1 TextExtractor
- [x] T2 Service + CLI wire

### retrieval-metadata-filters — 2026-08-16

Plan: [PLAN-retrieval-metadata-filters.md](plans/PLAN-retrieval-metadata-filters.md)
Spec: [SPEC-retrieval-metadata-filters.md](specs/SPEC-retrieval-metadata-filters.md)

- [x] T1 Binary `document_date` harvest
- [x] T2 `RetrievalFilters` + `SemanticChunk.document_date`
- [x] T3 Manifest persist + `load_corpus` join
- [x] T4 Allowlist + Hybrid/BM25/Turbovec
- [x] T5 Capability map + verify

---

Agent-tracked backlogs (GitHub Issues is the source of truth; these files are indexes):

- [Qdrant retirement](ISSUES-qdrant-retirement.md) — backend deleted (ADR-009). Remaining: project-document reranker (#9), pgvector/ParadeDB (#10).
- Handoffs for the next agents: [ADR-008 migration](../docs/handoffs/HANDOFF-adr-008-turbovec-migration.md) (main), [Linear setup](../docs/handoffs/HANDOFF-linear-issue-tracking-setup.md) (sandbox)
