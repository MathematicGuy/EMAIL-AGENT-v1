# Implementation Plan — Architecture Doc Sync Audit (TARGET-ARCHITECTURE.md)

Audit and update `docs/architectures/TARGET-ARCHITECTURE.md` against actual implementation in `src/cowork_agent/` following `doc-sync-audit` guidelines and `/wgm` protocol. Each subagent focuses strictly on 1 Architecture Section at a time, while the supervisor synthesizes findings and performs targeted updates.

**Target File:** `docs/architectures/TARGET-ARCHITECTURE.md`
**Ground-Truth Source Base:** `src/cowork_agent/` (domain, api, features/ai_chat, features/email_action_plan, integrations, persistence)

---

## Tasks

### Task 1: Ground-Truth Code Mapping
- **Files:** `src/cowork_agent/`
- **Validation command:** `python -m pytest -q`
- **Criteria:**
  - Index and map ground-truth source files for all 20 sections of `TARGET-ARCHITECTURE.md`.
  - Identify exact source files for API routes, Chat Controller, TaskEpisodes, Memory Gateway, Four-Type Memory, RAG Module, Email Module, and domain models.
- **Status:** pending

### Task 2: Audit & Update Sections 1 – 5
- **Files:** `docs/architectures/TARGET-ARCHITECTURE.md` (Sections 1 to 5)
- **Validation command:** `python -m pytest -q`
- **Criteria:**
  - Launch dedicated subagents for each section (§1 to §5), each focusing on exactly 1 section.
  - Check alignment against actual source code.
  - Apply minimal targeted edits for clear factual gaps with source citations (`file:line`). Mark ambiguous/future scope as `AMBIGUOUS`.
- **Status:** pending

### Task 3: Audit & Update Sections 6 – 10
- **Files:** `docs/architectures/TARGET-ARCHITECTURE.md` (Sections 6 to 10)
- **Validation command:** `python -m pytest -q`
- **Criteria:**
  - Launch dedicated subagents for each section (§6 to §10), each focusing on exactly 1 section.
  - Compare RAG architecture, agent-memory interaction, state ownership, database traces, and internal APIs with actual implementation.
  - Apply minimal targeted edits with source citations.
- **Status:** pending

### Task 4: Audit & Update Sections 11 – 15
- **Files:** `docs/architectures/TARGET-ARCHITECTURE.md` (Sections 11 to 15)
- **Validation command:** `python -m pytest -q`
- **Criteria:**
  - Launch dedicated subagents for each section (§11 to §15), each focusing on exactly 1 section.
  - Compare failure paths, retries/timeouts, human approval policy, observability/evaluation, and output contracts with source implementation.
  - Status: pending

### Task 5: Audit & Update Sections 16 – 20
- **Files:** `docs/architectures/TARGET-ARCHITECTURE.md` (Sections 16 to 20, including §20.1–20.5)
- **Validation command:** `python -m pytest -q`
- **Criteria:**
  - Launch dedicated subagents for each section (§16 to §20), each focusing on exactly 1 section.
  - Validate Architecture Principles, Implementation Order, Out-of-Scope items, Baseline Summary, and Accepted ADR-004 Chat-Native Target (§20.1 to §20.5) against `src/cowork_agent/features/ai_chat/` & `domain/`.
  - Apply minimal targeted edits with source citations.
- **Status:** pending

### Task 6: Synthesis, Codebase Verification & Audit Report
- **Files:** `docs/architectures/TARGET-ARCHITECTURE.md`
- **Validation command:** `python -m pytest -q && python -m ruff check . && python -m mypy src`
- **Criteria:**
  - Ensure all 20 sections have been audited and updated where factual drift occurred.
  - Verify overall documentation consistency and run codebase checks (`pytest`, `ruff`, `mypy`).
  - Generate full CHANGED / UNCHANGED / AMBIGUOUS report.
- **Status:** pending
