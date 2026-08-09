# Task Checklist — Cowork Agent Roadmap

Checklist companion to `tasks/plan.md`. One line per task; tick when the task's
acceptance criteria AND the Definition of Done are met. `[O]` = orchestrator
(main agent) task; others are subagent-dispatchable.

V2 scope authority: `docs/references/doc-update-scope-memory-chat.md` and
`docs/references/memory-system-and-chat-demo-analysis.md` realign memory to AI
Chat and define `@Email` as an executable in-chat skill.

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
- [ ] PostgreSQL persona/profile store; explicit-only writes
- [ ] Compact per-turn profile loading + degraded fallback
- [ ] Preference/profile deletion + retention
- [ ] Checkpoint: preferences affect later chat; failure does not block chat or `@Email`

## V2-M3 — Chat and `@Email` episodic persistence
- [ ] Bounded chat summaries + idempotent `@Email` Action Plan episodes (`retrieval_eligible=false`)
- [ ] Eligibility enforcement at write + read boundaries (code, not prompts)
- [ ] Chat session/turn/tool provenance mandatory; no-raw-body validation
- [ ] Checkpoint: system-generated episodes unretrievable

## V2-M4 — AI Chat Controller, SSE, and `@Email` tool
- [ ] Chat session/message APIs + Chat Controller event loop + typed SSE handler
- [ ] `@Email` Skill Tool wrapper returning structured Action Plan cards
- [ ] Inline approve/complete/reject transitions on cards
- [ ] Eligibility rule table on every transition; invalid transitions refused
- [ ] Checkpoint: stream/tool lifecycle works; approval/completion flips eligibility

## V2-M5 — Selective episodic and RAG retrieval for chat
- [ ] Episodic retrieval request with eligibility filters + bounded relevance scoring
- [ ] Selective chat-intent trigger policy (never every turn)
- [ ] Labeled persona/session/episode/semantic context + conflict precedence (FR-13)
- [ ] Checkpoint: approved/completed-only retrieval, even against model request

## V2-M6 — AI Chat memory evaluation and governance
- [ ] Memory-enabled vs memory-disabled chat evaluation on labeled set
- [ ] Retention, purge, deletion audits, index propagation
- [ ] Zero-tolerance safety metrics/alerts
- [ ] Launch thresholds established
- [ ] Checkpoint: PRD-v2 §16 criteria 1–20 pass

## Gate: PRD-v2 §16 acceptance review
- [ ] [O] Acceptance review passed → unlock DEMO Increment B

## DEMO — Showcase frontend
- [ ] DEMO-A AI Chat Assistant primary screen + embedded `@Email` Action Plan cards
- [ ] DEMO-A supporting Connect, Knowledge, and Run audit screens per SPEC §3.1
- [ ] DEMO-A Playwright FE review per SPEC §9 (snapshots, screenshots, console/network/storage)
- [ ] DEMO-B inline task controls + persona/preferences + memory transparency/provenance/deletion
- [ ] DEMO-B Playwright live browser verification
- [ ] Checkpoint: SPEC §8 criteria 1–16 with evidence
