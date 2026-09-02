# PLAN — Golden ID repair + page-aware corpus load

> **Implements:** golden-set slug repair (R10) then
>   [SPEC-page-aware-corpus-load.md](../specs/SPEC-page-aware-corpus-load.md)
> **Created:** 2026-08-16

## Overview

1. Align live golden labels with committed `data/extracted/*.md` stems
   (hyphens from `_output_name`). Do not rewrite the corpus or historical
   `evaluations/baselines/*.json`.
2. Then implement `page-aware-corpus-load`.

## Architecture decisions

- Corpus `path.stem` is the source of truth for `document_id`.
- Legacy snapshot hash for q-001..q-032 must be recomputed after the ID
  rewrite. The snapshot still guards later accidental edits.
- Page split lives in `markdown_chunking.py`. `load_corpus` only wires it.
- `SemanticChunk` page fields are optional so old dicts still parse.

## Subagent waves

| Wave | Task | Files | Parallel? |
|---|---|---|---|
| 1 | G1 golden ID repair | fixtures + golden tests only | alone (R10) |
| 2 | P1 `split_markdown_pages` | `markdown_chunking.py` + its tests | after G1 (suite quieter) |
| 2 | P2 `KnowledgeChunk` + `load_corpus` | `knowledge_base.py`, `test_rag.py` | after P1 |
| 3 | P3 `SemanticChunk` + retriever copy | domain + hybrid/memory/turbovec | after P2 |

P1 can start once G1 is merged if it does not touch fixtures.

## Tasks

### G1: Golden document IDs

Replace underscore stems with hyphenated `path.stem` values in live
fixtures and tests only:

| Old | New |
|---|---|
| `cap_lai_cccd` | `cap-lai-cccd` |
| `dang_ky_ket_hon` | `dang-ky-ket-hon` |
| `dang_ky_xe` | `dang-ky-xe` |
| `huong_dan_nop_ho_so_dai_hoc_vinuni` | `huong-dan-nop-ho-so-dai-hoc-vinuni` |
| `thu_tuc_dang_ky_bhxh_luatvietnam` | `thu-tuc-dang-ky-bhxh-luatvietnam` |
| `thue_dien_tu` | `thue-dien-tu` |

Keep `dang_ky_tam_tru` as a deliberately **unknown** id in rule-4 tests
if that file is no longer the expected stem (`dang-ky-tam-tru` exists).

Recompute `LEGACY_CASE_SNAPSHOT_SHA256` in `loader.py` and
`test_retrieval_golden.py` with the same dump the loader uses
(`json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",", ":"))`
over the first 32 raw cases).

**Do not edit** `evaluations/baselines/**` (historical runs).

**Verify:**

```powershell
uv run pytest tests/unit/fixtures/test_retrieval_golden.py tests/unit/integrations/rag/test_rag.py tests/integration/email_action_plan/test_rag_retrieval_golden.py -q
```

### P1: split_markdown_pages

**Verify:** focused markdown_chunking / rag unit tests.

### P2: load_corpus coordinates

Wire split → `chunk_markdown_pages` → `KnowledgeChunk.page_*`.

### P3: SemanticChunk optional pages

`from_dict` defaults missing keys to None. Copy in hybrid, memory,
turbovec_memory.

### Checkpoint

- Focused routes green
- `uv run ruff` + `uv run mypy` on touched src
- `uv run pytest -q`
- No `data/extracted/*.md` writes
