# Task Checklist — Cowork Agent Roadmap

Checklist companion to `tasks/plan.md`. One line per task; tick when the task's
acceptance criteria AND the Definition of Done are met. `[O]` = orchestrator
(main agent) task; others are subagent-dispatchable.

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
- [ ] 4.2 Versioned compatibility mapper (Tasks → legacy result shape) — needs 4.1
- [ ] 4.3 Cowork GUI task presentation — needs 4.2
- [ ] 4.4 Basic metadata-only telemetry (parallel with 4.1/4.2)
- [ ] 4.5 Development trace (marker, encryption, TTL, prod guard)
- [ ] Checkpoint: V1-M4 exit criteria

## Gate: PRD-v1 §15 acceptance review (19 criteria, evidence-backed)
- [ ] [O] Acceptance review passed → unlock V1-H and DEMO Increment A

## V1-H — Durable control plane
- [x] [O] Resolve PostgreSQL deployment/migration owner with user (resolved 2026-08-07: orchestrator owns; see plan.md Open Questions)
- [ ] 5.1 PostgreSQL run/task/outbox repositories (atomic create, CAS claim)
- [ ] 5.2 Redis Streams queue + DLQ worker (no sensitive payloads in DLQ) — needs 5.1
- [ ] 5.3 Observable metadata-only lifecycle events (outbox replacement)
- [ ] 5.4 Timeout/retry budgets (Gmail backoff/jitter, token refresh, partial batch)
- [ ] 5.5 Advanced observability, alerts, numeric launch gates, scaled evaluation
- [ ] Checkpoint: V1-H exit criteria (separate-process claim, restart durability)

## V2-M1 — Memory Gateway
- [ ] Memory contracts: TaskEpisode, MemoryContextRequest, profile/transition/provenance
- [ ] In-process Memory Gateway: namespace, eligibility, fail-closed
- [ ] Route all Agent Core memory access through gateway
- [ ] Checkpoint: cross-tenant/cross-user fail-closed tests

## V2-M2 — Long-term declarative memory
- [ ] PostgreSQL profile store; explicit-only writes
- [ ] Compact profile loading + degraded fallback
- [ ] Preference/profile deletion + retention
- [ ] Checkpoint: preferences affect later plans; read failure never blocks v1

## V2-M3 — Episodic persistence
- [ ] Idempotent system-generated episode writes (`retrieval_eligible=false`)
- [ ] Eligibility enforcement at write + read boundaries (code, not prompts)
- [ ] Provenance mandatory; no-raw-body validation
- [ ] Checkpoint: system-generated episodes unretrievable

## V2-M4 — Validation lifecycle
- [ ] Approve/complete/reject transitions (API + minimal GUI control)
- [ ] Eligibility rule table on every transition; invalid transitions refused
- [ ] Provenance + timestamps per transition
- [ ] Checkpoint: approval/completion flips eligibility; rejection does not

## V2-M5 — Selective episodic retrieval
- [ ] Episodic retrieval request with eligibility filters + bounded relevance scoring
- [ ] Selective trigger policy (never every run)
- [ ] Labeled generator context sources + conflict precedence (FR-13)
- [ ] Checkpoint: approved/completed-only retrieval, even against model request

## V2-M6 — Evaluation and governance
- [ ] Memory-enabled vs v1-baseline evaluation on labeled set
- [ ] Retention, purge, deletion audits, index propagation
- [ ] Zero-tolerance safety metrics/alerts
- [ ] Launch thresholds established
- [ ] Checkpoint: PRD-v2 §16 criteria 1–20 pass

## Gate: PRD-v2 §16 acceptance review
- [ ] [O] Acceptance review passed → unlock DEMO Increment B

## DEMO — Showcase frontend
- [ ] DEMO-A Increment A screens (Connect/Run/Tasks/Detail/Audit) per SPEC §3.1
- [ ] DEMO-A live browser verification per SPEC §9 (screenshots)
- [ ] DEMO-B Increment B screens (Preferences/Lifecycle/Insight/Effect/Deletion)
- [ ] DEMO-B live browser verification
- [ ] Checkpoint: SPEC §8 criteria 1–10 with evidence
