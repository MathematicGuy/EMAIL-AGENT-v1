# Issues — Qdrant retirement

Index for [ADR-008](adr/ADR-008-turbovec-project-document-plane.md) and
[ADR-009](adr/ADR-009-qdrant-backend-retired.md). GitHub Issues is the source
of truth.

**Status as of 2026-08-14:** Qdrant is gone from the codebase. Company RAG is
Turbovec hybrid. Project documents are Postgres FTS + per-project `.tvim`.

| # | Issue | Status |
| :-- | :--- | :--- |
| Q-3 | [#4 Quarantine markers](https://github.com/MathematicGuy/EMAIL-AGENT-v1/issues/4) | **Closed** — superseded by deletion |
| Q-2 | [#5 Recall gate vs Qdrant baseline](https://github.com/MathematicGuy/EMAIL-AGENT-v1/issues/5) | **Closed** — control group removed by operator decision (ADR-009) |
| Q-1 | [#6 Delete Qdrant from the codebase](https://github.com/MathematicGuy/EMAIL-AGENT-v1/issues/6) | **Closed** — company and project planes deleted |
| Q-4 | [#7 Stale deprecation text](https://github.com/MathematicGuy/EMAIL-AGENT-v1/issues/7) | **Closed** — warnings now name Turbovec |
| Q-5 | [#8 Retire the company knowledge plane](https://github.com/MathematicGuy/EMAIL-AGENT-v1/issues/8) | **Closed** — ADR-009 keeps A/B/C; only the Qdrant backend goes |
| Q-6 | [#9 Reranker on the project-document path](https://github.com/MathematicGuy/EMAIL-AGENT-v1/issues/9) | Open — product follow-up, not a Qdrant leftover |
| Q-7 | [#10 Deferred alternatives — pgvector, ParadeDB](https://github.com/MathematicGuy/EMAIL-AGENT-v1/issues/10) | Open — product follow-up, not a Qdrant leftover |
| Q-8 | [#11 Docs after Qdrant is deleted](https://github.com/MathematicGuy/EMAIL-AGENT-v1/issues/11) | **Closed** — runtime docs and PRD-v4 updated |

Remaining work on this list is **not** Qdrant cleanup.
