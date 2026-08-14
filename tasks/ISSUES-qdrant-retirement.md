# Issues — Qdrant retirement follow-ups

Deferred work from [ADR-008](adr/ADR-008-turbovec-project-document-plane.md). GitHub Issues is
the source of truth; this file is the index.

Intent: **Qdrant leaves the codebase entirely.** ADR-008 quarantines it rather than deleting it
only so the recall gate has a float32 control group. Every item below exists to make that
deletion happen, not to make the quarantine permanent.

| # | Issue | Blocked by |
| :-- | :--- | :--- |
| Q-3 | [#4 Quarantine markers on the Qdrant path](https://github.com/MathematicGuy/EMAIL-AGENT-v1/issues/4) | — do now |
| Q-2 | [#5 Recall gate — Turbovec + Postgres FTS vs Qdrant baseline](https://github.com/MathematicGuy/EMAIL-AGENT-v1/issues/5) | — |
| Q-1 | [#6 Delete Qdrant from the codebase](https://github.com/MathematicGuy/EMAIL-AGENT-v1/issues/6) | Q-2 |
| Q-4 | [#7 Stale deprecation text points at QdrantSemanticMemory](https://github.com/MathematicGuy/EMAIL-AGENT-v1/issues/7) | — |
| Q-5 | [#8 Retire the company knowledge plane (needs ADR-009)](https://github.com/MathematicGuy/EMAIL-AGENT-v1/issues/8) | Q-2 |
| Q-6 | [#9 Reranker on the project-document retrieval path](https://github.com/MathematicGuy/EMAIL-AGENT-v1/issues/9) | Q-2 |
| Q-7 | [#10 Deferred alternatives — pgvector, ParadeDB pg_search](https://github.com/MathematicGuy/EMAIL-AGENT-v1/issues/10) | — |
| Q-8 | [#11 Docs owing an update after Qdrant is deleted](https://github.com/MathematicGuy/EMAIL-AGENT-v1/issues/11) | Q-1 |

Critical path: **Q-3 → Q-2 → Q-1 → Q-8.**
