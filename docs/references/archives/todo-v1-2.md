# Task Checklist — Cowork Agent Roadmap

Checklist companion to `tasks/plan.md`. One line per task; tick when the task's
acceptance criteria AND the Definition of Done are met. `[O]` = orchestrator
(main agent) task; others are subagent-dispatchable.

V2 scope authority: `docs/references/memory-system-and-chat-demo-analysis.md`
realigns memory to AI Chat.

## Phase 0 — Fixtures, baseline, contract freeze
- [x] P0-A Compatibility test suite (`tests/compatibility/`) — lanes: API contract · ordering/dedupe · query guard · privacy
- [x] P0-B Labeled routing fixture set (`tests/fixtures/routing/`, ≥25 cases)
- [x] P0-C Combined-extractor baseline capture (`scripts/capture_baseline.py` + `docs/baselines/`) — needs P0-B
- [x] [O] P0-D Correlation contract freeze (`docs/references/task-candidate-correlation-contract.md`)
- [ ] Checkpoint: full `pytest` + `ruff` + `mypy` green; commit; user review

## V1-M1 — Core contracts and Gmail entry
- [x] 1.1 Versioned contracts: EphemeralEmailEnvelope, EmailRouteDecision, ActionPlanOutput, TraceEvent
- [x] 1.2 Verified Principal boundary (remove `user_id` query params; Mailbox-Connection principal) — needs 1.1
- [x] 1.3 Envelope consolidation + attachment presence-only — needs 1.1
- [x] 1.4 Short-term cleanup + safety TTL — needs 1.3
- [x] 1.5 Remove fake queue/outbox wiring + MailTodoApi — needs P0-A, 1.2
- [x] Checkpoint: V1-M1 exit criteria (plan.md)

## V1-M2 — Classification and routing
- [x] 2.1 Split RouteClassifierPort/ActionPlanGeneratorPort; move shaping out of provider adapters
- [x] 2.2 Bounded classifier batching; schema-valid decision per email; repair retry
- [x] 2.3 Deterministic task-candidate correlation (source_message_ids, incident_key) — needs 2.2
- [x] 2.4 Pure route resolver + FR-07 policy guards (parallel-safe with 2.1/2.2)
- [x] 2.5 Classifier fallback: retry once → conservative RETRIEVE_RAG
- [x] 2.6 Routing evaluation harness over routing_labels.json
- [x] Checkpoint: V1-M2 exit criteria

## V1-M3 — RAG, generation, validation
- [x] 3.1 SemanticMemoryPort + null adapter (Lane RAG)
- [x] 3.2 In-repo RAG adapter: corpus loader + embeddings + vector store + ACL filter (Lane RAG) — needs 3.1
- [x] 3.3 Generator: one call per non-NO_ACTION candidate (Lane Core)
- [x] 3.4 Validators: schema, grounding, citation, privacy, unsupported-procedure — needs 3.3
- [x] 3.5 RAG failure → Partial Plan path — needs 3.2, 3.3
- [x] 3.6 Generation failure → schema-repair retry path; DIRECT_PLAN zero-retrieval test
- [x] Checkpoint: V1-M3 exit criteria

## V1-M4 — Persistence and presentation
- [x] 4.1 Task persistence port + local adapter (idempotent key)
- [x] 4.2 Versioned compatibility mapper (Tasks → legacy result shape) — needs 4.1
- [x] 4.3 Cowork GUI task presentation — needs 4.2
- [x] 4.4 Basic metadata-only telemetry (parallel with 4.1/4.2)
- [x] 4.5 Development trace (marker, encryption, TTL, prod guard)
- [x] Checkpoint: V1-M4 exit criteria

## Gate: PRD-v1 §15 acceptance review (19 criteria, evidence-backed)
- [x] [O] Acceptance review passed → unlock V1-H and DEMO Increment A (PASSED 2026-08-08; verdict + evidence in plan.md gate section)

## V1-H — Durable control plane
- [x] [O] Resolve PostgreSQL deployment/migration owner with user (resolved 2026-08-07: orchestrator owns; see plan.md Open Questions)
- [x] 5.1 PostgreSQL run/task/outbox repositories (atomic create, CAS claim)
- [x] 5.2 Redis Streams queue + DLQ worker (no sensitive payloads in DLQ) — needs 5.1
- [x] 5.3 Observable metadata-only lifecycle events (outbox replacement)
- [x] 5.4 Timeout/retry budgets (Gmail backoff/jitter, token refresh, partial batch)
- [ ] 5.5 Advanced observability, alerts, numeric launch gates, scaled evaluation (lifecycle publication + DLQ events delivered at `479bf2d`; numeric gates/scaled eval BLOCKED on user threshold decisions + authorized live runs)
- [ ] Checkpoint: V1-H exit criteria (separate-process claim, restart durability) — mechanics evidenced by T5.1/T5.2/T5.4 tests; formal sign-off waits on 5.5 blocked items

## V2-M1 — Chat Memory Gateway and session working memory
- [x] ChatMessageRequest/SSE, TaskEpisode, MemoryContextRequest, profile/transition/provenance contracts (`310d2fd`)
- [x] In-process Memory Gateway: namespace, eligibility, fail-closed (`2a29e29`)
- [x] Chat Session Working Memory keyed by mandatory `session_id` + `feature: ai_chat`, with TTL/compaction (`7e42784`, `2a29e29`)
- [x] Freeze the Gateway as the sole feature-level memory access boundary (`2a29e29`; controller wiring is V2-M4)
- [x] Checkpoint: cross-tenant/cross-user/session fail-closed + TTL tests (73 focused tests; deterministic suite exit 0)

## V2-M2 — AI Chat declarative profile
- [x] PostgreSQL persona/profile store (`002_chat_profiles.sql`, `PostgresChatProfileRepository`); explicit-only writes enforced by `profile_policy.authorize_profile_write` + a `source_type` CHECK
- [x] Compact per-turn profile loading + degraded fallback (gateway `read_context` long-term path; `expires_at` filtered in SQL)
- [x] Preference/profile deletion + retention (`delete_profile`, `purge_expired`)
- [x] Checkpoint: preferences affect later chat; failure does not block chat — unit/gateway proof plus `tests/integration/persistence` 15 passed against `cowork-pg` (2026-08-10)

## V2-M3 — Chat episodic persistence
- [x] [O] M3.1 ADR-004 + PRD-v2 v2.2 generic TaskEpisode contract approved
- [x] [O] M3.1 Sync Target Architecture, master comparison, and orchestration evidence
- [x] M3.2 Tests-first generic TaskEpisode domain contract
- [x] M3.3 Consumer fixtures + port alignment; full AI Chat unit scope green
- [x] M3.4a PostgreSQL migration/down + repository lifecycle/retrieval/deletion tests; parent gates green
- [x] [O] M3.4a fresh final Sol review after citation-key correction + before/after status/hash comparison (verdict `ship`)
- [x] M3.4b Gateway write/transition/delete/retrieval wiring — accepted in `0d0bc22`
- [x] Checkpoint: system-generated episodes unretrievable; approved/completed only retrievable (186 impacted tests; live PostgreSQL 20 passed)

## V2-M4 — AI Chat Controller and SSE
- [x] Chat session/message APIs + Chat Controller event loop + typed SSE handler (12 focused controller/API tests)
- [x] Inline approve/complete/reject transitions on episodes (`0d0bc22`)
- [x] Eligibility rule table on every transition; invalid transitions refused
- [x] Checkpoint: stream/task lifecycle works; approval/completion flips eligibility (included in 186-test gate; verdict `ship`)

## V2-M5 — Selective episodic and RAG retrieval for chat
- [x] Episodic query contract + eligible-state filters + bounded PostgreSQL FTS relevance/min-score
- [x] Selective chat-intent trigger policy (never every turn)
- [x] Labeled persona/session/episode/semantic context + conflict precedence (FR-13)
- [x] Wire durable episodic retrieval through MemoryGateway and live reply-provider consumption (`0d0bc22`)
- [x] Checkpoint: approved/completed-only retrieval, even against model request (verdict `ship`)

## V2-M6 — AI Chat memory evaluation and governance
- [x] Memory-enabled vs memory-disabled paired evaluation contract and launch gate
- [x] Retention, purge, and exact-scope deletion coordination; M3.4a SQL deletion/purge live-tested
- [x] Zero-tolerance safety metrics encoded in the paired launch gate
- [x] Production telemetry sink/alerts, backup/restore, index propagation, and end-to-end runtime deletion proof (2026-08-11: LoggingMemoryOperationSink+Metrics runtime injection; scripts/backup_restore_chat_memory.py live proof; index propagation N/A — no user-memory index; deletion audit live tests)
- [x] PRD-v2 launch thresholds established and evidenced on the labeled set (2026-08-11: product-approved 90d retention + Moderate-MVP thresholds in .env.example; scripts/run_paired_chat_evaluation.py exit 0, safety counters zero)
- [x] Checkpoint: PRD-v2 §16 criteria 1–18 pass (AC-01..AC-18 DONE; fresh final review verdict `ship`)

## Gate: PRD-v2 §16 acceptance review
- [x] [O] Acceptance review passed → unlock DEMO Increment B (2026-08-11, verdict `ship`)

## DEMO — Showcase frontend
- [ ] DEMO-A AI Chat Assistant primary screen
- [ ] DEMO-A supporting Connect, Knowledge, and Run audit screens per SPEC §3.1
- [ ] DEMO-A Playwright FE review per SPEC §9 (snapshots, screenshots, console/network/storage)
- [ ] DEMO-B inline task controls + persona/preferences + memory transparency/provenance/deletion
- [ ] DEMO-B Playwright live browser verification
- [ ] Checkpoint: SPEC §8 criteria 1–16 with evidence

## Do Later
- [ ] Retired `@Email` in-chat feature and its Action Plan card lifecycle. Do not implement unless explicitly reactivated.
