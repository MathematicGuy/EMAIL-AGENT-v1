# Capability Map: Company Knowledge Ingestion

> **Status:** Approved 2026-08-16
> **Source:** Review of `docs/references/ingestion-pipeline-brainstorming.md`
>   (Document Loading specialization) plus live code in
>   `src/cowork_agent/integrations/knowledge_ingestion/`
> **Spec index:** this file. Do not guess which spec is active — select by
>   module id.

Company knowledge ingestion is several independently testable capabilities.
They share extractors and the `data/extracted/*.md` corpus. They do **not**
share one spec.

## Modules

| Module id | Responsibility | Depends on | Spec |
|---|---|---|---|
| `document-loading` | Company CLI Document Loading: discover, extract, NFC-sanitize, emit closed document metadata, persist atomic Markdown + manifest. Stops at `data/extracted/*.md`. | — | [SPEC-document-loading.md](./SPEC-document-loading.md) |
| `page-aware-corpus-load` | `load_corpus()` parses `<!-- Page N -->` into `KnowledgeChunk.page_start` / `page_end`. Coordinates survive onto `SemanticChunk`. | `document-loading` | [SPEC-page-aware-corpus-load.md](./SPEC-page-aware-corpus-load.md) |
| `format-txt-md` | Expand the company CLI whitelist to `.txt` and `.md`. | `document-loading` | [SPEC-format-txt-md.md](./SPEC-format-txt-md.md) |
| `retrieval-metadata-filters` | Query-time filters on `document_ids` and binary `document_date` (`years` / `months`). No `category`. | `page-aware-corpus-load` | [SPEC-retrieval-metadata-filters.md](./SPEC-retrieval-metadata-filters.md) |

## Build order

```text
document-loading
    → page-aware-corpus-load
        → format-txt-md
        → retrieval-metadata-filters   (only after a real filter source exists)
```

`format-txt-md` and `retrieval-metadata-filters` are independent of each other.

## Before / after

Company plane only. Left is the pipeline before these four modules; right is live code after they landed. `category` is still absent.

```mermaid
flowchart LR
  subgraph BEFORE["Before"]
    direction TB
    B_RAW["data/raw<br/>.pdf .docx only"]
    B_EXT["Extract body text<br/>no NFC, no frontmatter"]
    B_MD["data/extracted/*.md<br/>flat Markdown"]
    B_MAN["manifest: hash, status,<br/>extractor, page_count"]
    B_LOAD["load_corpus<br/>H1 title + section chunks"]
    B_CHUNK["KnowledgeChunk<br/>no pages, no date"]
    B_RET["Hybrid dense + BM25<br/>on the full corpus"]
    B_FILT["RetrievalFilters<br/>document_status only<br/>and unused"]
    B_RAW --> B_EXT --> B_MD
    B_EXT --> B_MAN
    B_MD --> B_LOAD --> B_CHUNK --> B_RET
    B_FILT -.->|"ignored"| B_RET
  end

  subgraph AFTER["After — four modules"]
    direction TB
    A_RAW["data/raw<br/>.pdf .docx .txt .md"]
    A_EXT["Extract + suffix-before-OCR"]
    A_DATE["harvest_document_date<br/>PDF Info / DOCX props if present"]
    A_CLEAN["NFC sanitize<br/>closed frontmatter<br/>OCR/native Page N markers"]
    A_MD["data/extracted/*.md<br/>self-describing Markdown"]
    A_MAN["manifest + document_date<br/>ISO or empty"]
    A_LOAD["load_corpus<br/>strip YAML, split pages,<br/>join date by stem"]
    A_CHUNK["KnowledgeChunk<br/>page_start/end + document_date"]
    A_ALLOW["allowed_chunk_indices<br/>document_ids AND years AND months"]
    A_RET["Hybrid: Turbovec allowlist<br/>+ BM25 allowlist"]
    A_RAW --> A_EXT --> A_CLEAN --> A_MD
    A_EXT --> A_DATE --> A_MAN
    A_CLEAN --> A_MAN
    A_MD --> A_LOAD
    A_MAN --> A_LOAD
    A_LOAD --> A_CHUNK --> A_ALLOW --> A_RET
  end
```

| Step | Before | After |
|---|---|---|
| Discover | `.pdf` / `.docx` | also `.txt` / `.md`; text never goes to OCR |
| Persist | raw extracted body | NFC + closed frontmatter; OCR pages get `<!-- Page N -->` |
| Manifest | operational fields only | plus optional `document_date` from binaries |
| `load_corpus` | H1 + sections; page comments ignored | strips frontmatter; `page_start`/`page_end`; join date by stem |
| Retrieve | whole corpus; `document_status` unused | pre-filter `document_ids` / `years` / `months`; empty allowlist → `NO_RESULTS` without embed |

Missing binary date stays `None` and fails a year/month filter. Callers that still pass only `document_status=ready` keep the before ranking.

## Plane boundary

These module ids apply to the **company CLI plane**
(`mail-todo-ingest-knowledge` → `data/extracted/*.md`).

The project-document plane (ADR-007 / ADR-008) is a different consumer. It may
later reuse `sanitize_text()` from `document-loading`. It is not a module on
this map.

Emails and email attachments are never ingested (ADR-003).

## Stable ids

Module ids are kebab-case and are not renamed mid-initiative. New specs,
plans, and PRs name the module id they implement.
