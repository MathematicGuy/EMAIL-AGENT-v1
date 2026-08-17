# SPEC — Retrieval metadata filters

> **Status:** Accepted for implementation — 2026-08-16
> **Module id:** `retrieval-metadata-filters`
> **Map:** [CAPABILITY-MAP-ingestion-pipeline.md](./CAPABILITY-MAP-ingestion-pipeline.md)
> **Depends on:** `page-aware-corpus-load`
> **Source decision:** binary document date **if present**; `document_id` =
>   `path.stem`. **No `category`.** No filename/body heuristics, no ingest LLM,
>   no operator sidecar in this module.

## 1. Objective

Company RAG must pre-filter the candidate set **before** dense/BM25 scoring
using:

1. `document_ids` — already on every chunk (`path.stem`).
2. `years` / `months` — derived from a binary `document_date` when the
   PDF `/Info` or DOCX core properties actually carry a creation (else
   modification) timestamp. Missing stays `None`.

Callers that pass only `document_status=("ready",)` keep today’s ranking.

**User:** workspace administrator (ingest) and retrieval callers.

**Success:** a query with `years=(2026,)` never returns a chunk whose
binary date is another year or is missing. A corpus with no binary dates
behaves exactly as today.

## 2. Corrections

| # | Claim | Reality |
|---|---|---|
| 1 | Harvest year/category from every PDF/DOCX | This corpus: DOCX props empty; most PDFs have no Title/Subject; six `chi-tiet-thu-tuc-*.pdf` have `CreationDate=2026-08-07` (PDFKit export). Harvest **if able**; do not invent. |
| 2 | Use that CreationDate as legal year | It is an export clock. Still stored as `document_date` because the operator asked for binary dates when present. Do not reinterpret it. |
| 3 | Category from binaries or H1 | **Out of this module.** Later plan. |
| 4 | Operator sidecar `ingestion-sidecar.json` | Superseded. Dates are machine-harvested onto the **manifest**. |
| 5 | Filter only Turbovec | Hybrid also runs BM25 on the full corpus. Both legs must share the allowlist. |

## 3. Tech stack

- `python-docx` core properties (already a dependency).
- Stdlib PDF `/Info` parse (`CreationDate` / `ModDate`, including
  FlateDecode + indirect refs). **No new PDF library.**
- No PyYAML. Always `uv run`.

## 4. Commands

```powershell
uv run pytest tests/unit/integrations/knowledge_ingestion tests/unit/integrations/rag tests/unit/domain/test_target_contracts.py -q
uv run ruff check src/cowork_agent/integrations/knowledge_ingestion src/cowork_agent/integrations/rag src/cowork_agent/domain/target_contracts.py
uv run mypy src/cowork_agent/integrations/knowledge_ingestion src/cowork_agent/integrations/rag src/cowork_agent/domain/target_contracts.py
```

Shared-contract change: `uv run pytest -q` once at the end.

## 5. Project structure

```text
src/cowork_agent/integrations/knowledge_ingestion/date_harvest.py
    NEW harvest_document_date(path) -> date | None
src/cowork_agent/integrations/knowledge_ingestion/models.py
    ManifestEntry.document_date: str = ""   # ISO YYYY-MM-DD or ""
src/cowork_agent/integrations/knowledge_ingestion/manifest.py
    persist/load document_date; missing key → ""
src/cowork_agent/integrations/knowledge_ingestion/service.py
    harvest after extract; write onto ManifestEntry
src/cowork_agent/integrations/rag/knowledge_base.py
    KnowledgeChunk.document_date: date | None
    load_corpus joins manifest by output stem
    allowed_chunk_indices(chunks, filters)
src/cowork_agent/domain/target_contracts.py
    RetrievalFilters.document_ids / years / months
    SemanticChunk.document_date: date | None
src/cowork_agent/integrations/rag/bm25.py
    search(..., allowlist: Collection[str] | None = None)
src/cowork_agent/integrations/rag/{hybrid,memory,turbovec_memory}.py
    apply allowlist; copy document_date onto SemanticChunk
```

## 6. Code style

`harvest_document_date(path)`:

- `.docx`: `core_properties.created`, else `modified`. Date only (drop time).
- `.pdf`: `/Info` `CreationDate`, else `ModDate`. Parse `D:YYYYMMDD…`.
- Anything else, unreadable file, or missing/unparseable field → `None`.
- Never use filesystem mtime. Never parse filename or body text.

Manifest JSON field `document_date` is `""` or `"2026-08-07"`.

`RetrievalFilters`:

```python
document_status: tuple[str, ...] = ("ready",)
document_ids: tuple[str, ...] = ()
years: tuple[int, ...] = ()
months: tuple[int, ...] = ()  # 1..12
```

Empty tuple = unconstrained. AND across provided keys. A chunk with
`document_date is None` **fails** a non-empty `years` or `months` filter.

`from_dict`: missing new keys → `()`. Unknown keys ignored.

`allowed_chunk_indices` empty → retrievers return `NO_RESULTS` **without**
embedding.

Closed Markdown frontmatter is **unchanged** (no `year` / `category` /
`document_date` keys). No rewrite of `data/extracted/*.md`.

## 7. Testing strategy

| Invariant | Owner |
|---|---|
| DOCX with created → that date; empty props → None | `test_date_harvest.py` |
| PDF `D:20260807101925Z` → `2026-08-07`; no Info date → None | `test_date_harvest.py` |
| `.txt` / missing file → None | `test_date_harvest.py` |
| Manifest round-trip / missing key `""` | `test_manifest.py` |
| Service records harvested date | `test_service.py` |
| `load_corpus` joins manifest date by stem; no manifest → None | `test_rag.py` |
| Filters default empty; `from_dict` backward compatible | `test_target_contracts.py` |
| `years`/`months`/`document_ids` shrink allowlist; None date excluded | `test_rag.py` |
| Empty allowlist → NO_RESULTS, no embed | `test_turbovec_memory.py` or `test_rag.py` |
| Hybrid passes allowlist to BM25 and dense | `test_hybrid.py` or `test_rag.py` |

Do not weaken golden top-1 assertions.

## 8. Boundaries

- **Always:** missing date is `None`/`""`; emails stay out (ADR-003).
- **Ask first:** rewriting committed corpus; adding `category`; sidecar overlay;
  teaching the classifier to emit `years`; new PDF dependencies.
- **Never:** harvest category; filename/body year regex; filesystem mtime;
  Qdrant; project-plane ACL; invent dates.

## 9. Success criteria

- Ingest of a PDF whose `/Info` has `CreationDate=D:20260807101925Z` stores
  `document_date=2026-08-07` on the manifest entry.
- `load_corpus` copies that date onto every chunk of that `document_id`.
- `RetrievalFilters(years=(2026,))` keeps only those chunks.
- `RetrievalFilters(document_ids=("cap-lai-cccd",))` keeps only that id.
- Existing callers with only `document_status` are unchanged.
- `data/extracted/*.md` is not modified.

## 10. Not doing

- `category` / `Lĩnh vực` / classifier `ExpectedDocumentType`
- Operator sidecar file
- Frontmatter expansion / `--force` re-ingest of the committed corpus
- Populating email/chat request filters (callers stay as they are)
