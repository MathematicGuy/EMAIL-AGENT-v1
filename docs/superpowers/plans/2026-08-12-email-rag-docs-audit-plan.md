# Email RAG Documentation Audit & Synchronization Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Audit and update each documentation file in `docs/evaluations/email-rag/` (`EMAIL-RAG-STATUS.md` and `RAG-EVALUATION-STATUS.md`) against codebase ground truth using the `doc-sync-audit` skill.

**Architecture:** Apply minimal targeted edits to documentation based strictly on ground-truth source code facts (source files in `src/cowork_agent/integrations/rag/`, `src/cowork_agent/ingestion_cli.py`, `src/cowork_agent/app.py`, `scripts/`, and `tests/`). Reconcile false test assertions (such as non-existent `test_citation_accuracy.py`) and update snapshot timestamps.

**Tech Stack:** Markdown documentation, Python codebase (`pytest`, `ruff`, `mypy`).

## Global Constraints

- **Minimal Edits:** Modify only lines with verified gaps or factual inaccuracies. Never rewrite stylistically correct prose.
- **Strict Evidence:** Every edit must be justified by an exact source file path and line/function (`doc-sync-audit` Step 4).
- **Preserve Formatting:** Maintain all existing Markdown structure, mermaid diagrams, tables, and heading levels.
- **Verification Gate:** Run `python -m pytest -q` after edits to ensure code and tests remain untouched and passing.

---

### Task 1: Audit & Update `EMAIL-RAG-STATUS.md`

**Files:**
- Modify: `docs/evaluations/email-rag/EMAIL-RAG-STATUS.md:1-123`
- Ground Truth Sources:
  - `src/cowork_agent/integrations/rag/bootstrap.py`
  - `src/cowork_agent/integrations/rag/knowledge_base.py`
  - `src/cowork_agent/integrations/rag/qdrant.py`
  - `src/cowork_agent/integrations/rag/hybrid.py`
  - `src/cowork_agent/ingestion_cli.py`
  - `src/cowork_agent/app.py`

**Interfaces:**
- Consumes: Ground truth facts from `src/cowork_agent/integrations/rag/` and `src/cowork_agent/ingestion_cli.py`.
- Produces: Synchronized `EMAIL-RAG-STATUS.md` snapshot reflecting current checkout state.

- [ ] **Step 1: Audit header snapshot date and ground truth source paths**

Check line 2 of `EMAIL-RAG-STATUS.md` (`snapshot as of 2026-08-10`) and update to `2026-08-12`. Check Source of truth list (lines 99-107) to ensure `src/cowork_agent/ingestion_cli.py` and `src/cowork_agent/integrations/rag/knowledge_base.py` are explicitly referenced.

- [ ] **Step 2: Apply targeted edits to `EMAIL-RAG-STATUS.md`**

Use `replace_file_content` to apply minimal targeted edits to update snapshot date and source of truth file references.

- [ ] **Step 3: Verify document formatting and run pytest**

Run `python -m pytest tests/unit/integrations/rag/ -q` to verify test suite health.

- [ ] **Step 4: Commit changes for Task 1**

```bash
git add docs/evaluations/email-rag/EMAIL-RAG-STATUS.md
git commit -m "docs(rag): update snapshot date and source paths in EMAIL-RAG-STATUS.md"
```

---

### Task 2: Audit & Update `RAG-EVALUATION-STATUS.md`

**Files:**
- Modify: `docs/evaluations/email-rag/RAG-EVALUATION-STATUS.md:1-325`
- Ground Truth Sources:
  - `tests/integration/email_action_plan/test_workflow.py:220` (`test_validation_strips_bogus_citations_from_direct_plan_task`)
  - `scripts/evaluate_retrieval.py`
  - `scripts/evaluate_routing.py`
  - `tests/unit/integrations/rag/test_rag.py`
  - `tests/integration/email_action_plan/test_rag_retrieval_golden.py`

**Interfaces:**
- Consumes: Codebase verification showing `test_citation_accuracy.py` does not exist.
- Produces: Corrected `RAG-EVALUATION-STATUS.md` with reconciled citation accuracy test statements.

- [ ] **Step 1: Reconcile non-existent `test_citation_accuracy.py` references**

Line 85 currently states:
`✅ Citation Accuracy Verification (test_citation_accuracy.py): Inspects plan-step word overlap with each cited retrieved chunk and reports missing chunk IDs.`
Correction: Change to mark citation accuracy eval as missing (matching lines 55, 214, and 273), while noting that bogus citation ID stripping is tested in `test_workflow.py`.

Line 242 currently states:
`test_citation_accuracy.py validates citation IDs and lexical-overlap signals. It does not prove a generated claim is semantically supported by the cited chunk.`
Correction: Replace `test_citation_accuracy.py` reference with `test_workflow.py` (`test_validation_strips_bogus_citations_from_direct_plan_task`).

- [ ] **Step 2: Update snapshot date in header**

Update line 3 from `snapshot as of 2026-08-10` to `snapshot as of 2026-08-12`.

- [ ] **Step 3: Apply targeted edits to `RAG-EVALUATION-STATUS.md`**

Use `replace_file_content` to apply minimal targeted edits to `RAG-EVALUATION-STATUS.md`.

- [ ] **Step 4: Verify document integrity and run pytest suite**

Run `python -m pytest tests/unit/integrations/rag/ tests/integration/email_action_plan/ -q` to confirm everything passes cleanly.

- [ ] **Step 5: Commit changes for Task 2**

```bash
git add docs/evaluations/email-rag/RAG-EVALUATION-STATUS.md
git commit -m "docs(eval): reconcile citation accuracy test references in RAG-EVALUATION-STATUS.md"
```

---

### Task 3: Final Audit Verification & Summary Report

**Files:**
- Read: `docs/evaluations/email-rag/EMAIL-RAG-STATUS.md`
- Read: `docs/evaluations/email-rag/RAG-EVALUATION-STATUS.md`

**Interfaces:**
- Consumes: Completed edits across both documentation files.
- Produces: `doc-sync-audit` Step 5 Report format (CHANGED / UNCHANGED / AMBIGUOUS).

- [ ] **Step 1: Run full test suite and linters to verify repository health**

Run: `python -m pytest -q`
Run: `python -m ruff check .`
Run: `python -m mypy src`

- [ ] **Step 2: Synthesize and format the doc-sync-audit report**

Format final report listing every CHANGED, UNCHANGED, and AMBIGUOUS item with exact file + line justifications.
