# SPEC — Document Loading (company CLI)

> **Status:** Accepted for implementation — 2026-08-16
> **Created:** 2026-08-16
> **Module id:** `document-loading`
> **Map:** [CAPABILITY-MAP-ingestion-pipeline.md](./CAPABILITY-MAP-ingestion-pipeline.md)
> **Source:** Review of `docs/references/ingestion-pipeline-brainstorming.md` §5–§8
>   against live `KnowledgeIngestionService`
> **Architecture authority:** [c3-ingestion-cli.md](../../docs/architectures/c3-ingestion-cli.md),
>   ADR-003 (emails never ingested), ADR-009 (Qdrant retired; Turbovec is the
>   company store)
> **Does not authorize:** `page-aware-corpus-load`, `format-txt-md`,
>   `retrieval-metadata-filters`, project-plane wiring, corpus rewrite, or a
>   new YAML dependency

## 1. Objective

Make the administrator CLI `mail-todo-ingest-knowledge` emit **Unicode-NFC**
Markdown with a **closed document-metadata header** and a matching manifest
`title`, without changing retrieval contracts, Turbovec, project uploads, or
the committed corpus in the implementation PR.

**User:** the workspace administrator who runs the company ingestion CLI.

**Success:** a newly ingested PDF/DOCX is clean Vietnamese text, self-describing
on disk, and safe for today's `load_corpus()` to chunk. Existing
`data/extracted/*.md` files that this PR does not touch keep today's chunk
texts.

## 2. Corrections to the brainstorm

Verified against code on 2026-08-16. The spec works around all eight.

| # | Claim in the brainstorm | Verified reality |
|---|---|---|
| 1 | Document Loading includes `load_corpus` upgrades and Turbovec/Qdrant pre-filtering (board §5.2 stages 4–5) | Simple-RAG §II.2.1 Document Loading is extract + document metadata. Chunking and vector filters are later Indexing steps. Those are other module ids on the capability map. |
| 2 | Qdrant is a live alternative store | ADR-009 retired Qdrant. Company RAG is Turbovec hybrid. `RAG_STORE_PROVIDER=qdrant` is null memory. |
| 3 | Ingestion Stage 4 commits the vector index | `KnowledgeIngestionService` writes Markdown + `ingestion-manifest.json` only. Bootstrap loads the corpus later. |
| 4 | `year` / `category` / `author` can be harvested from extractors | Live legal DOCX (`01-2021-nd-cp-283247.md`) starts with a table, not an H1. PDF Info / Word core properties do not reliably carry those fields. Inventing them needs filename heuristics or an ingest LLM — both rejected. |
| 5 | Rebuild a 4-tier COS PdfInspector | `pdf_inspector.detect_pdf` already classifies native vs scanned. Out of scope. |
| 6 | `sanitize_text()` should collapse all `[ \t]+` to a single space | That destroys Markdown list indent and pipe-table alignment. NFC + control-char strip + blank-line collapse only. |
| 7 | Filename slugs should use NFC | `_output_name` uses **NFKD** to ASCII-slug the path. That is intentional. NFC applies to **extracted body text**, not slugs. |
| 8 | OCR output already has page coordinates | Native PDF (`_render_pdf`) inserts `<!-- Page N -->`. `MistralOcrExtractor.extract` joins pages without markers. That gap is in scope. |

## 3. Tech stack

- Python 3.11+, existing project dependencies only.
- `unicodedata` + `re` for sanitization.
- Hand-rolled closed-field frontmatter emit/parse. **No PyYAML.**
- Existing extractors: `DocxExtractor`, `PdfInspector`, `MistralOcrExtractor`.
- Existing persist: `write_markdown_atomically`, `ManifestStore`.
- Always invoke tests with `uv run` (plain `python -m` hits Anaconda SSL errors
  on this machine).

## 4. Commands

Implementation-time verification (not required to accept this spec):

```powershell
uv run pytest tests/unit/integrations/knowledge_ingestion -q
uv run pytest tests/unit/integrations/rag/test_rag.py -q
uv run ruff check src/cowork_agent/integrations/knowledge_ingestion src/cowork_agent/integrations/rag/knowledge_base.py
uv run mypy src/cowork_agent/integrations/knowledge_ingestion src/cowork_agent/integrations/rag/knowledge_base.py
```

After any shared contract change (ports, schemas, `KnowledgeChunk`), run
`uv run pytest -q` once.

## 5. Project structure

```text
src/cowork_agent/integrations/knowledge_ingestion/
    text_sanitizer.py          NEW — sanitize_text, frontmatter emit/parse
    service.py                 apply sanitizer + wrap frontmatter; keep skip
    models.py                  ManifestEntry.title (optional, default "")
    manifest.py                persist/load title; ignore unknown extra keys
    ocr.py                     emit <!-- Page N --> per OCR page
    ingestion_cli.py           unchanged flags
src/cowork_agent/integrations/rag/
    knowledge_base.py          strip leading frontmatter before chunk_markdown
tests/unit/integrations/knowledge_ingestion/
    test_text_sanitizer.py     NEW — owns sanitizer + frontmatter invariants
    test_service.py            emit, skip, collision, no silent rewrite
    test_ocr.py                page markers on OCR pages
tests/unit/integrations/rag/
    test_rag.py                load_corpus hygiene; no-frontmatter unchanged
tests/README.md                add source→route row for knowledge_ingestion
```

Do **not** edit `data/extracted/*.md` or `data/extracted/ingestion-manifest.json`
in the implementation PR.

## 6. Code style

Apply sanitization **once** in `KnowledgeIngestionService` after extraction and
before persist. Do not fork a copy into every extractor.

```python
def sanitize_text(text: str) -> str:
    """NFC-normalize body text without destroying Markdown structure."""
    text = unicodedata.normalize("NFC", text)
    text = "".join(
        ch for ch in text if not unicodedata.category(ch).startswith("C") or ch in "\n\t"
    )
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"
```

Rules the implementation must keep:

- Preserve `<!-- Page N -->` comments, leading indentation, and `|` tables.
- Do **not** globally collapse `[ \t]+`.
- Do **not** NFC-normalize the output **filename** (`_output_name` stays NFKD).
- `document_id` is the output stem, identical to today's `load_corpus` id.

Closed frontmatter (exact keys, this order):

```yaml
---
document_id: policy-file
title: Policy
source_file: Policy File.pdf
extractor: pdf_native
page_count: 2
processed_at: 2026-08-16T00:00:00+00:00
---
```

- `title` = first ATX H1 (`# …`) in the **sanitized body**, else the output stem.
- `extractor` ∈ {`docx`, `pdf_native`, `mistral_ocr`}.
- `source_file` = the relative source path already used as the manifest key.
- `processed_at` = the same UTC ISO-8601 timestamp written to the manifest.
- No `year`, `category`, or `author` keys. A parser that sees unknown keys
  **ignores them**; it does not fail the ingest.

Pipeline order:

1. Extract Markdown (existing format router / OCR).
2. For OCR, wrap each page as `<!-- Page {n} -->\n{page}` before join — same
   contract as `_render_pdf`.
3. `sanitize_text(body)`.
4. Resolve `title` from the sanitized body.
5. Wrap frontmatter around the body.
6. Atomic write + manifest `record` (including `title`).

## 7. Testing strategy

`tests/README.md` §3: one invariant, one owner. Do not re-assert skip/collision
in `test_text_sanitizer.py`. Do not add an integration test that boots the API
to prove NFC.

| Invariant | Owner | Do not re-assert in |
|---|---|---|
| NFC converts Vietnamese NFD; control chars drop; indent/tables/page comments survive | `test_text_sanitizer.py` | service/OCR tests |
| Frontmatter emit is the closed key set; parse round-trips those keys | `test_text_sanitizer.py` | — |
| Service writes frontmatter + sanitized body; hash skip still skips | `test_service.py` | — |
| OCR pages carry `<!-- Page N -->` | `test_ocr.py` | service tests (unless proving the service wires OCR) |
| `load_corpus` strips a leading closed frontmatter block; files without frontmatter keep today's chunk texts | `test_rag.py` (`test_load_corpus_*`) | ingestion tests |
| Implementation PR does not rewrite committed `data/extracted/*.md` | review (`git status`) | — |

When implementing, add this source→route row to `tests/README.md` §1 (it is
missing today):

| Edited under | Run |
|---|---|
| `integrations/knowledge_ingestion/` | `tests/unit/integrations/knowledge_ingestion`, then `test_rag.py` if `load_corpus` changed |

## 8. Boundaries

- **Always:** deterministic ingest (no LLM); atomic `.tmp` → `.md`; SHA-256
  skip on the **source** bytes; symlink rejection; slug collision failure;
  emails and attachments stay out of this pipeline (ADR-003).
- **Ask first:** rewriting committed `data/extracted/*.md`; adding a YAML
  library; changing `RetrievalFilters` or RAG bootstrap fallbacks; wiring
  `sanitize_text` into `ProjectDocumentExtractor`; adding `.txt` / `.md` to
  `_SUPPORTED_SUFFIXES`.
- **Never:** persist raw email or attachment bytes; commit secrets; treat
  Qdrant as live; invent `year` / `category`; put vector upsert inside the
  ingestion CLI; add `page_start` / `page_end` to `KnowledgeChunk` in this
  module.

## 9. Success criteria

- Ingesting a DOCX/PDF fixture whose body contains Vietnamese NFD `ế` writes
  NFC `ế` in the Markdown body and a valid frontmatter block.
- Native PDF **and** Mistral OCR Markdown both contain `<!-- Page N -->` for
  each extracted page.
- A second ingest of an unchanged source with a matching succeeded manifest
  entry is `skipped`. The writer does not silently regenerate outputs.
- `load_corpus` on a file **without** frontmatter produces the same chunk
  texts as today.
- `load_corpus` on a file **with** frontmatter does not put `document_id:`,
  `extractor:`, or other YAML keys into any `KnowledgeChunk.text`.
- `SemanticRetrievalRequest`, `RetrievalFilters`, Turbovec, and
  project-document APIs are unchanged.
- The implementation PR does not modify `data/extracted/*.md`.

## 10. Open questions

None that block this spec. Operator `--force` re-ingest of the committed
corpus is a later ops decision; it belongs with golden-set re-measure (R10 +
R3), not with this module.

## 11. Not doing (and why)

- **`page-aware-corpus-load`** — `MarkdownChunk` already has coordinates;
  `KnowledgeChunk` does not. Binding pages is a retrieval/citation change.
- **`format-txt-md`** — live `data/raw/` is only PDF/DOCX.
- **`retrieval-metadata-filters`** — no honest `year` / `category` source yet.
- **Project-plane `.txt` / `.md` uploads** — different plane (ADR-007).
- **PdfInspector rewrite** — classification already works.
- **PyYAML** — closed field set does not earn a dependency.
- **Index commit in the CLI** — contradicts the current bootstrap boundary.
