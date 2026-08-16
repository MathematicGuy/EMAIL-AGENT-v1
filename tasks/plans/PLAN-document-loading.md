# PLAN — Document Loading (company CLI)

> **Implements:** [SPEC-document-loading.md](../specs/SPEC-document-loading.md)
> **Map:** [CAPABILITY-MAP-ingestion-pipeline.md](../specs/CAPABILITY-MAP-ingestion-pipeline.md)
> **Module id:** `document-loading`
> **Created:** 2026-08-16
> **Task list:** this file (implementation session). Not filed to Linear unless asked.

## Overview

Ship the company CLI Document Loading slice: NFC-safe body text, closed YAML
frontmatter, optional manifest `title`, OCR `<!-- Page N -->` markers, and a
`load_corpus` strip so YAML is never chunked as text. No retrieval-contract
change, no committed-corpus rewrite, no project-plane wiring.

## Architecture decisions

- Sanitizer + frontmatter live in one new module
  (`text_sanitizer.py`). Service applies them once after extract.
- No PyYAML. Closed-field emit/parse only.
- Source SHA-256 skip stays as-is. Regeneration is `--force`.
- `load_corpus` only strips a leading frontmatter block. It does **not** add
  `page_start` / `page_end` to `KnowledgeChunk`.
- Filename slugs stay NFKD. NFC is body text only.

## Subagent leverage (lifecycle-safe)

Orchestrator owns: this plan, spec/scope gate, wave integration, `tests/README.md`,
`ruff` / `mypy` / full suite, and any review.

| Wave | Agent | Task | Why a subagent is safe |
|---|---|---|---|
| 1 | A | T1 sanitizer + frontmatter helpers | New files only; TDD; no shared writes |
| 1 | B | T2 OCR page markers | Touches only `ocr.py` + `test_ocr.py` |
| 1 | C | T3 manifest `title` | Touches only models/manifest tests |
| 2 | D | T4 service wiring | Depends on T1+T3; isolated to service tests |
| 2 | E | T5 `load_corpus` hygiene | Depends on T1 parse; isolated to RAG tests |

**Do not give subagents:** planning, spec edits, capability-map changes, corpus
rewrite, `.txt`/`.md` expansion, project-plane wiring, `RetrievalFilters`,
Qdrant, PdfInspector rewrite, or the final suite.

## Dependency graph

```text
T1 text_sanitizer          T2 OCR markers          T3 manifest title
        \                        |                        /
         \                       |                       /
          +-------- T4 service wiring ------------------+
                             |
                    T5 load_corpus strip
                             |
              T6 README route + orchestrator verify
```

Wave 1 is parallel. Wave 2 starts only after T1 (and T3 for T4) is merged in
the shared workspace.

## Task list

### Task 1: Sanitizer and closed frontmatter helpers

**Description:** Add `sanitize_text`, `build_frontmatter`, and `split_frontmatter`
as pure functions. This is the contract Wave 2 imports.

**Acceptance criteria:**
- [x] Vietnamese NFD `ế` becomes NFC
- [x] Control chars drop; `\n` / `\t` kept
- [x] List indent, pipe tables, and `<!-- Page N -->` survive
- [x] 3+ blank lines collapse to one blank line
- [x] Frontmatter emit/parse round-trips the closed key set; unknown keys ignored

**Verification:** `uv run pytest tests/unit/integrations/knowledge_ingestion/test_text_sanitizer.py -q`

**Dependencies:** None

**Files:** `src/cowork_agent/integrations/knowledge_ingestion/text_sanitizer.py` (new),
`tests/unit/integrations/knowledge_ingestion/test_text_sanitizer.py` (new)

**Estimated scope:** S

**Subagent:** Wave 1-A

### Task 2: OCR page markers

**Description:** Each Mistral OCR page is wrapped like `_render_pdf`:
`<!-- Page {n} -->\n{markdown}`.

**Acceptance criteria:**
- [x] Two-page fake OCR output contains `<!-- Page 1 -->` and `<!-- Page 2 -->`
- [x] Existing image-rewrite behavior still works
- [x] Empty pages are still omitted

**Verification:** `uv run pytest tests/unit/integrations/knowledge_ingestion/test_ocr.py -q`

**Dependencies:** None

**Files:** `src/cowork_agent/integrations/knowledge_ingestion/ocr.py`,
`tests/unit/integrations/knowledge_ingestion/test_ocr.py`

**Estimated scope:** S

**Subagent:** Wave 1-B

### Task 3: Manifest `title`

**Description:** Optional `ManifestEntry.title` (default `""`). Old manifests
without `title` still load.

**Acceptance criteria:**
- [x] New records persist `title`
- [x] Missing `title` in JSON loads as `""`
- [x] Skip behavior unchanged (source hash + succeeded)

**Verification:** `uv run pytest tests/unit/integrations/knowledge_ingestion/test_manifest.py -q`

**Dependencies:** None

**Files:** `src/cowork_agent/integrations/knowledge_ingestion/models.py`,
`src/cowork_agent/integrations/knowledge_ingestion/manifest.py`,
`tests/unit/integrations/knowledge_ingestion/test_manifest.py`

**Estimated scope:** S

**Subagent:** Wave 1-C

### Checkpoint: Wave 1

- [x] T1–T3 tests green; no `data/extracted/` edits

### Task 4: Service wires sanitizer + frontmatter + title

**Description:** After extract, sanitize body, resolve title (first H1 else stem),
wrap frontmatter, write atomically, record `title` on the manifest.

**Acceptance criteria:**
- [x] New DOCX/PDF outputs start with the closed `---` block
- [x] Unchanged source still `skipped`
- [x] `test_native_pdf_uses_stable_page_markers` still proves markers (allow
      frontmatter prefix + trailing newline from sanitizer)
- [x] NFD Vietnamese in a DOCX body is written NFC

**Verification:** `uv run pytest tests/unit/integrations/knowledge_ingestion/test_service.py -q`

**Dependencies:** T1, T3

**Files:** `src/cowork_agent/integrations/knowledge_ingestion/service.py`,
`tests/unit/integrations/knowledge_ingestion/test_service.py`

**Estimated scope:** S

**Subagent:** Wave 2-D

### Task 5: `load_corpus` frontmatter hygiene

**Description:** Strip a leading closed frontmatter block before `chunk_markdown`.
Do not add page coordinates. Title remains first H1 in the body, else stem.

**Acceptance criteria:**
- [x] No-frontmatter fixtures keep today's chunk texts
- [x] Frontmatter keys never appear in `KnowledgeChunk.text`
- [x] Committed corpus test still passes without rewriting those files

**Verification:** `uv run pytest tests/unit/integrations/rag/test_rag.py -q`

**Dependencies:** T1

**Files:** `src/cowork_agent/integrations/rag/knowledge_base.py`,
`tests/unit/integrations/rag/test_rag.py`

**Estimated scope:** S

**Subagent:** Wave 2-E

### Task 6: Test route + Definition of Done

**Description:** Add the missing `integrations/knowledge_ingestion/` row to
`tests/README.md`. Orchestrator runs focused routes, then `ruff`, `mypy`, and
`uv run pytest -q`.

**Acceptance criteria:**
- [x] README source→route row exists
- [x] Focused routes green (`54 passed`); ruff/mypy clean
- [x] `git status` shows no `data/extracted/*.md` changes
- [ ] Full suite green — blocked by **pre-existing** golden IDs (`cap_lai_cccd`) vs hyphenated corpus stems (`cap-lai-cccd`). Not introduced by this module.

**Verification:**

```powershell
uv run pytest tests/unit/integrations/knowledge_ingestion tests/unit/integrations/rag/test_rag.py -q
uv run ruff check src/cowork_agent/integrations/knowledge_ingestion src/cowork_agent/integrations/rag/knowledge_base.py
uv run mypy src/cowork_agent/integrations/knowledge_ingestion src/cowork_agent/integrations/rag/knowledge_base.py
uv run pytest -q
```

**Dependencies:** T4, T5

**Files:** `tests/README.md`

**Estimated scope:** XS

**Owner:** orchestrator (not a subagent)

### Checkpoint: Complete

- [x] SPEC §9 success criteria met for `document-loading`
- [x] No out-of-scope modules implemented

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Exact-string page-marker test breaks when frontmatter/sanitizer land | Med | T4 updates that assertion; keep marker proof |
| Parallel Wave 1 writes collide | High | File-disjoint tasks only |
| Subagent adds year/category or page_start | High | Prompt forbids it; orchestrator diffs against spec |
| Committed corpus accidentally rewritten | High | Review `git status` in T6 |

## Open questions

None. `--force` re-ingest of `data/extracted/` is an ops decision after this
module, with golden-set re-measure (R10 + R3).
