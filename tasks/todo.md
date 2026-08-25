### Architecture Improvement Program — opened 2026-08-25

Register: [SPEC-architecture-improvement-program.md](specs/SPEC-architecture-improvement-program.md)
— the source of truth for status. ADRs: [013](adr/ADR-013-composition-as-typed-value.md),
[014](adr/ADR-014-turn-pipeline-stays-one-function.md),
[015](adr/ADR-015-routers-own-their-transport.md).

- [x] C01 Report artifacts get a module (`9c4e5fc`)
- [x] C02 Composition is a typed value — `CoworkRuntime` (ADR-013)
- [x] C04 Turn pipeline stays one function; settlement extracted (ADR-014)
- [x] C03 Routers own their transport; `app.py` 1581 → 507 (ADR-015)
- [ ] C07 `api/chat.py` mail-scan cluster — **needs a go/no-go before any code**
- [ ] C05 `Settings.from_env` reads disk behind the caller — unscheduled
- [ ] C06 `useStreamingChat` runs two protocols — unscheduled, frontend-only
- [ ] C08 PDF renderer — blocked on a dependency decision (ask first)
- [ ] C09 RAG corpus lost its test guard — product question, not a test one
- [ ] C10 Three `app.state` survivors; two ADR-013 corrections pending

---

Agent-tracked backlogs (GitHub Issues is the source of truth; these files are indexes):

- [Qdrant retirement](ISSUES-qdrant-retirement.md) — backend deleted (ADR-009). Remaining: project-document reranker (#9), pgvector/ParadeDB (#10).
- Handoffs for the next agents: [ADR-008 migration](../docs/handoffs/HANDOFF-adr-008-turbovec-migration.md) (main)
