# Implementation Plan — Email-RAG Documentation Sync Audit

Audit and update all documentation in `docs/evaluations/email-rag/` (`EMAIL-RAG-STATUS.md` and `RAG-EVALUATION-STATUS.md`) against ground-truth implementation source files using a team of 3 subagents and following the `/doc-sync-audit` skill.

**Target Directory:** `docs/evaluations/email-rag/`
- `docs/evaluations/email-rag/EMAIL-RAG-STATUS.md`
- `docs/evaluations/email-rag/RAG-EVALUATION-STATUS.md`

**Ground-Truth Source Files:**
- `src/cowork_agent/integrations/rag/` (`bootstrap.py`, `qdrant.py`, `hybrid.py`, `knowledge_base.py`, `bm25.py`, `rrf.py`, `jina_reranker.py`, `memory.py`, `null_memory.py`, `embeddings.py`, `chat_memory.py`)
- `src/cowork_agent/ingestion_cli.py`
- `src/cowork_agent/app.py`
- `src/cowork_agent/gui/app.py`
- `scripts/evaluate_retrieval.py`
- `scripts/evaluate_routing.py`
- `tests/unit/integrations/rag/`
- `tests/integration/email_action_plan/`
- `tests/fixtures/rag/`

---

## Tasks

### Task 1: Subagent 1 — Extract Ground-Truth Facts from Source Code & Scripts
- **Files:** `src/cowork_agent/integrations/rag/*`, `scripts/evaluate_retrieval.py`, `scripts/evaluate_routing.py`, `tests/`
- **Validation command:** `python -m pytest tests/unit/integrations/rag -q`
- **Criteria:**
  - Launch Subagent 1 (Ground-Truth Code & Evaluation Analyst).
  - Extract exact factual details: retriever classes, fallback logic, CLI arguments, tenant ACL filtering, Qdrant settings, ingestion behavior, evaluation metrics, test cases, and open vs closed gaps.
  - Produce a structured Ground-Truth Fact Sheet with `file:line` references.
- **Status:** pending

### Task 2: Subagent 2 — Audit & Update `EMAIL-RAG-STATUS.md`
- **Files:** `docs/evaluations/email-rag/EMAIL-RAG-STATUS.md`
- **Validation command:** `python -m pytest tests/unit/integrations/rag -q`
- **Criteria:**
  - Launch Subagent 2 (`EMAIL-RAG-STATUS.md` Auditor).
  - Compare `EMAIL-RAG-STATUS.md` sections (Executive summary, Implemented architecture table, Runtime behavior, Security, Known gaps, Operational checks, Local knowledge ingestion) against Subagent 1's Fact Sheet.
  - Make minimal targeted edits with source citations (`file:line`). Mark unclear items as `AMBIGUOUS`.
- **Status:** pending

### Task 3: Subagent 3 — Audit & Update `RAG-EVALUATION-STATUS.md`
- **Files:** `docs/evaluations/email-rag/RAG-EVALUATION-STATUS.md`
- **Validation command:** `python -m pytest tests/unit/integrations/rag -q`
- **Criteria:**
  - Launch Subagent 3 (`RAG-EVALUATION-STATUS.md` Auditor).
  - Compare `RAG-EVALUATION-STATUS.md` sections (Coverage map, Plain English summary, Layer 1-3 evaluations, baseline results, summary table, open vs closed gaps) against Subagent 1's Fact Sheet.
  - Make minimal targeted edits with source citations (`file:line`). Mark unclear items as `AMBIGUOUS`.
- **Status:** pending

### Task 4: Demo-Validation & Synthesis Report
- **Files:** `docs/evaluations/email-rag/EMAIL-RAG-STATUS.md`, `docs/evaluations/email-rag/RAG-EVALUATION-STATUS.md`
- **Validation command:** `python -m pytest -q && python -m ruff check .`
- **Criteria:**
  - Verify that all updated markdown documents pass formatting and syntax checks.
  - Run full test suite and linter to confirm zero regressions.
  - Synthesize findings into a final CHANGED / UNCHANGED / AMBIGUOUS audit report.
- **Status:** pending
