---
name: update-current-architectures
description: Audits and updates Level 1 system architecture documentation in docs/architectures/current-architectures/ against live source code to catch architecture drift vs TARGET-ARCHITECTURE.md. Use when the user asks to "update current architecture", "sync architecture docs", "check architecture drift", or runs /update-architecture.
---

# Update Current Architectures

Audit live codebase modules against [docs/architectures/current-architectures/](../../docs/architectures/current-architectures/) and track architecture drift compared to [TARGET-ARCHITECTURE.md](../../docs/architectures/TARGET-ARCHITECTURE.md).

## Usage & Scope Modes

Parse the invocation or request target:
- `/update-architecture [all]` — Audits all 3 streams, updates module docs `01-`, `02-`, `03-`, and refreshes the `README.md` Dashboard.
- `/update-architecture <file_basename>` (e.g. `01-email-action-plan-and-rag.md`) — Audits only the mapped source directory and updates that specific file in isolation.

## Execution Workflow

1. **Consult Source Map:** Read [references/SOURCE_MAP.md](references/SOURCE_MAP.md) to locate the live source directories for the targeted module doc.
2. **Stream Isolation (Token Efficiency):**
   - For single-file updates, inspect **only** the mapped source folders. Do not load the entire codebase into context.
   - For `"all"` updates, dispatch 3 research/writer subagents (one per stream), then execute a final consolidator pass to update the `README.md` Dashboard.
3. **Audit Drift against Target Architecture:** Compare live code capabilities against [TARGET-ARCHITECTURE.md](../../docs/architectures/TARGET-ARCHITECTURE.md) to detect implementation drift, gaps, or new features.
4. **Apply Format Guardrails:** Enforce formatting templates and rules defined in [references/FORMAT_GUARDRAIL.md](references/FORMAT_GUARDRAIL.md).
5. **Targeted Edits:** Use line-targeted edits (`replace_file_content`) to update status matrices, mermaid diagrams, and drift sections rather than full-file overwrites.

## Format Guardrail Checklist (Must Pass)

- [ ] **Metadata Block:** Includes `Architecture level`, `Status`, `Primary Owner`, and `Target Alignment`.
- [ ] **Mermaid Diagram:** Valid Level 1 Mermaid syntax (`flowchart TB` or `flowchart LR`) with quoted labels.
- [ ] **Relative Links:** All file and directory paths use clean standard relative paths (e.g., `[file.py](../../../src/cowork_agent/path/to/file.py)`).
- [ ] **Status Matrix:** Standardized columns (`Module / Component`, `Implemented Scope`, `Status`, `Target Alignment`, `Authoritative Code Location`).
- [ ] **Decoupling Rule:** Standalone Email Agent with decoupled in-chat email scan cards (`MailScanSummary`).
