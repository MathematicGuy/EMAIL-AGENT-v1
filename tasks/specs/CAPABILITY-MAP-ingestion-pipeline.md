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
| `retrieval-metadata-filters` | Query-time filters beyond `document_status=ready`. Requires a real metadata source first (`year` / `category` are **not** produced by `document-loading`). | `page-aware-corpus-load` | *not written* |

## Build order

```text
document-loading
    → page-aware-corpus-load
        → format-txt-md
        → retrieval-metadata-filters   (only after a real filter source exists)
```

`format-txt-md` and `retrieval-metadata-filters` are independent of each other.

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
