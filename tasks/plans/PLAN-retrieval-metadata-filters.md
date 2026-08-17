# PLAN — `retrieval-metadata-filters`

Spec: [SPEC-retrieval-metadata-filters.md](../specs/SPEC-retrieval-metadata-filters.md)

Review override 2026-08-16: **no category**; harvest **year/month/day from
binaries if present**; filter on **`document_ids`** as well.

## Wave 1 (parallel)

### T1 Binary date harvest
- Files: `date_harvest.py`, `test_date_harvest.py` only
- Public: `harvest_document_date(path: Path) -> date | None`
- Acceptance: DOCX created/modified; PDF `D:YYYYMMDD`; else None; no mtime
- Verify: `uv run pytest tests/unit/integrations/knowledge_ingestion/test_date_harvest.py -q`

### T2 Filter contract
- Files: `target_contracts.py`, `test_target_contracts.py` only
- Add `document_ids`, `years`, `months`; `SemanticChunk.document_date`
- Acceptance: defaults empty/`None`; `from_dict` without new keys still works
- Verify: `uv run pytest tests/unit/domain/test_target_contracts.py -q`

## Wave 2 (after T1)

### T3 Manifest + service + load_corpus join
- Files: `models.py`, `manifest.py`, `service.py`, `knowledge_base.py`,
  `test_manifest.py`, `test_service.py`, `test_rag.py`
- `ManifestEntry.document_date: str = ""`
- Service calls `harvest_document_date` and records ISO date
- `load_corpus` reads `ingestion-manifest.json` in `corpus_dir` and joins
  `output` stem → `document_date`
- Verify: `uv run pytest tests/unit/integrations/knowledge_ingestion tests/unit/integrations/rag/test_rag.py -q`

## Wave 3 (after T2+T3)

### T4 Allowlist + retrievers
- Files: `knowledge_base.py` (`allowed_chunk_indices`), `bm25.py`,
  `hybrid.py`, `memory.py`, `turbovec_memory.py`, focused tests
- Empty allowlist → `NO_RESULTS` without embed
- Hybrid passes allowlist to dense + BM25
- Copy `document_date` onto `SemanticChunk`
- Verify: `uv run pytest tests/unit/integrations/rag -q`

### T5 Orchestrator docs
- Capability map spec pointer; `tasks/todo.md`; README invariant owner if needed

## Out of scope

category, sidecar file, frontmatter keys, corpus rewrite, Qdrant, classifier
filter population, filesystem mtime, filename/body heuristics.
