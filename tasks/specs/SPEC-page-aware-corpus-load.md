# SPEC — Page-aware corpus load

> **Status:** Accepted for implementation — 2026-08-16
> **Module id:** `page-aware-corpus-load`
> **Map:** [CAPABILITY-MAP-ingestion-pipeline.md](./CAPABILITY-MAP-ingestion-pipeline.md)
> **Depends on:** `document-loading` ([SPEC-document-loading.md](./SPEC-document-loading.md))
> **Does not authorize:** `format-txt-md`, `retrieval-metadata-filters`,
>   year/category, project-plane changes, or rewriting `data/extracted/*.md`

## 1. Objective

`load_corpus()` must turn existing `<!-- Page N -->` markers into
`page_start` / `page_end` on each company-knowledge chunk, and those
coordinates must survive the mapping to `SemanticChunk` so retrieval
can cite pages later.

Documents without markers keep `page_start = page_end = None`.

## 2. Tech stack

- Existing `markdown_chunking.MarkdownPage` / `chunk_markdown_pages`
- No new dependencies
- Always `uv run`

## 3. Commands

```powershell
uv run pytest tests/unit/integrations/rag/test_rag.py tests/unit/integrations/rag/test_markdown_chunking.py -q
uv run pytest tests/unit/domain/test_target_contracts.py -q
uv run ruff check src/cowork_agent/integrations/rag src/cowork_agent/domain/target_contracts.py
uv run mypy src/cowork_agent/integrations/rag src/cowork_agent/domain/target_contracts.py
```

Shared-contract change: also `uv run pytest -q` once at the end.

## 4. Project structure

```text
src/cowork_agent/integrations/rag/markdown_chunking.py
    NEW split_markdown_pages(markdown) -> tuple[MarkdownPage, ...]
src/cowork_agent/integrations/rag/knowledge_base.py
    KnowledgeChunk.page_start / page_end: int | None
    load_corpus: split pages, chunk_markdown_pages, copy coordinates
src/cowork_agent/domain/target_contracts.py
    SemanticChunk.page_start / page_end: int | None = None
    from_dict: missing keys → None (backward compatible)
src/cowork_agent/integrations/rag/{hybrid,memory,turbovec_memory}.py
    copy page fields onto SemanticChunk
tests/unit/integrations/rag/test_rag.py
    page-span tests; no-marker docs stay None
tests/unit/integrations/rag/test_markdown_chunking.py  (create if absent)
    split_markdown_pages behaviour
```

## 5. Code style

Page comment (same as `_render_pdf` / OCR):

```text
<!-- Page 12 -->
```

`split_markdown_pages`:

- Match `^<!--\s*Page\s+(\d+)\s*-->\s*$` per line (1-based).
- Text after a marker belongs to that page. The marker line is **not**
  part of the page body (must not appear in `KnowledgeChunk.text`).
- No markers → one `MarkdownPage(markdown=body, page_number=None)`.
- Text before the first marker, if any, is `page_number=None`.

`KnowledgeChunk` and `SemanticChunk` grow two optional ints. Do not
require pages. Do not invent page 1 for DOCX-only files.

## 6. Testing strategy

| Invariant | Owner |
|---|---|
| Split pages; markers absent from page bodies | `test_markdown_chunking.py` or `test_rag.py` |
| Two-page fixture → chunks carry 1..2 inclusive spans | `test_rag.py` |
| No-marker fixture → both fields None | `test_rag.py` |
| `<!-- Page N -->` never in `KnowledgeChunk.text` | `test_rag.py` |
| `SemanticChunk.from_dict` accepts payloads without page keys | `test_target_contracts.py` |
| Committed corpus still loads | `test_load_corpus_reads_the_committed_documents` |

## 7. Boundaries

- **Always:** keep `document_id = path.stem`; strip frontmatter first
  (already shipped).
- **Ask first:** changing citation prompt text; rewriting committed MD.
- **Never:** year/category filters; Qdrant; project-document plane;
  adding `.txt`/`.md` to the CLI whitelist.

## 8. Success criteria

- A tmp markdown with `<!-- Page 1 -->` / `<!-- Page 2 -->` yields
  chunks whose coordinates cover those pages.
- Marker lines are not indexed as chunk text.
- Files without markers load with `None` coordinates.
- Retrieval adapters copy page fields onto `SemanticChunk`.
- `data/extracted/*.md` is not rewritten.

## 9. Not doing

- Query-time metadata pre-filtering
- Promoting frontmatter `title` over H1 (still H1-then-stem)
- LLM citations in the email/chat prompts (consumer of this data, later)
