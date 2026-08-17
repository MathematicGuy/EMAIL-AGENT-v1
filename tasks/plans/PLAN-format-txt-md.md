# PLAN — format-txt-md

> **Implements:** [SPEC-format-txt-md.md](../specs/SPEC-format-txt-md.md)
> **Created:** 2026-08-16

## Overview

Add local UTF-8 `.txt` / `.md` discovery and extraction to the company CLI.
Route those suffixes before the OCR branch so advance mode cannot bill Mistral
for plain text.

## Decisions

- New `text_extractor.py` keeps `service.py` from growing another inline reader.
- Incoming Markdown frontmatter is stripped; the persist path writes the closed
  company block once.
- `page_count` is 1, or max `<!-- Page N -->` if markers already exist.

## Tasks

### T1 — TextExtractor

New module + unit tests only.

**Files:** `text_extractor.py`, `test_text_extractor.py`

**Verify:** `uv run pytest tests/unit/integrations/knowledge_ingestion/test_text_extractor.py -q`

### T2 — Service + CLI wire

`_SUPPORTED_SUFFIXES` includes `.txt`/`.md`. `_extract` handles those suffixes
before `EXTRACTION_MODE=advance`. CLI help updated.

**Depends on:** T1

**Files:** `service.py`, `ingestion_cli.py`, `test_service.py`

**Verify:** `uv run pytest tests/unit/integrations/knowledge_ingestion/test_service.py -q`

## Subagents

- T1 first (new files).
- T2 after T1 (imports the extractor).
- Orchestrator: ruff, mypy, `uv run pytest -q`. No corpus rewrite.
