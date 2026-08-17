# SPEC — Company CLI `.txt` / `.md` ingestion

> **Status:** Accepted for implementation — 2026-08-16
> **Module id:** `format-txt-md`
> **Map:** [CAPABILITY-MAP-ingestion-pipeline.md](./CAPABILITY-MAP-ingestion-pipeline.md)
> **Depends on:** `document-loading`
> **Does not authorize:** project-plane uploads, `retrieval-metadata-filters`,
>   year/category, rewriting `data/extracted/*.md`, or sending text files to OCR

## 1. Objective

`mail-todo-ingest-knowledge` must discover administrator `.txt` and `.md`
files under `--source`, convert them through the same sanitize + closed
frontmatter persist path as DOCX/PDF, and never send them to Mistral OCR.

## 2. Tech stack

- Existing `sanitize_text`, `build_frontmatter`, `split_frontmatter`,
  `resolve_title`, `ManifestStore`
- UTF-8 decode only. No new dependencies.
- Always `uv run`

## 3. Commands

```powershell
uv run pytest tests/unit/integrations/knowledge_ingestion -q
uv run ruff check src/cowork_agent/integrations/knowledge_ingestion src/cowork_agent/ingestion_cli.py
uv run mypy src/cowork_agent/integrations/knowledge_ingestion src/cowork_agent/ingestion_cli.py
```

## 4. Project structure

```text
src/cowork_agent/integrations/knowledge_ingestion/text_extractor.py
    NEW — read UTF-8 .txt/.md; strip incoming frontmatter on .md
src/cowork_agent/integrations/knowledge_ingestion/service.py
    add .txt/.md to _SUPPORTED_SUFFIXES; route them BEFORE the OCR branch
src/cowork_agent/ingestion_cli.py
    help text mentions PDF/DOCX/TXT/MD
tests/unit/integrations/knowledge_ingestion/test_text_extractor.py
tests/unit/integrations/knowledge_ingestion/test_service.py
```

## 5. Behaviour

- Discover `.txt` and `.md` with the same symlink / slug / SHA-256 skip rules.
- Read UTF-8. `UnicodeDecodeError` → `reason_code="decode_failed"`, no output.
- Empty after strip → `empty_extraction`.
- `.md` that already starts with `---`: take `split_frontmatter` **body** only
  so persist does not nest YAML.
- Preserve `<!-- Page N -->` if present in the body.
- `extractor` is `text` or `markdown`. `page_count` is `1` unless the body
  already contains page markers; then `page_count` is the max `N`.
- **Suffix wins over `EXTRACTION_MODE=advance`.** `.txt`/`.md` never call
  `MistralOcrExtractor`.
- Persist path unchanged: sanitize → title → frontmatter → atomic write.

## 6. Testing strategy

| Invariant | Owner |
|---|---|
| UTF-8 read; NFC via persist path; extractor field | `test_text_extractor.py` + `test_service.py` |
| Incoming `.md` frontmatter is not nested | `test_service.py` |
| Advance mode does not OCR `.txt` | `test_service.py` |
| Invalid bytes → `decode_failed`, no `.md` written | `test_service.py` |
| Skip-on-hash still works | `test_service.py` |

## 7. Boundaries

- **Always:** emails stay out (ADR-003); company CLI only.
- **Ask first:** project-plane `.txt`/`.md` uploads; adding HTML.
- **Never:** OCR text files; invent year/category; rewrite committed corpus.

## 8. Success criteria

- A `.txt` and a `.md` in `--source` produce extracted Markdown with closed
  frontmatter.
- Advance mode on a folder of mixed PDF + TXT does not call OCR for the TXT.
- Existing PDF/DOCX tests still pass.

## 9. Not doing

- User-document upload MIME expansion
- Query-time metadata filters
- Committing new files under `data/raw/` or `data/extracted/`
