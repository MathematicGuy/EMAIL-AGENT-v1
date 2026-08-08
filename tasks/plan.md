# Implementation Plan — Cowork Agent: PRD-v1 → PRD-v2 → DEMO Roadmap

## Overview

Execution roadmap for EMAIL-AGENT-v1, decomposing `docs/master-comparison.md` Step 7
(single source of truth for sequencing) into subagent-dispatchable tasks. Scope:
Phase 0 (fixtures + decisions) → V1-M1..M4 → PRD-v1 §15 gate → V1-H hardening →
V2-M1..M6 → PRD-v2 §16 gate → DEMO showcase (SPEC-Demo-Frontend). The main agent
(Orchestrator) owns specs, plans, gate reviews, and merges; subagents implement
tasks inside one phase, parallelized along the lanes defined below.

**Baseline (verified 2026-08-07):** combined classify+plan extraction in
`features/email_action_plan/` + `integrations/llm/providers/{gemini,groq}.py`;
`DigestWorker` workflow; in-memory repos (`persistence/repositories/local.py`);
write-only fake queue/outbox (`orchestration/local.py`); FastAPI
`BackgroundTasks` dispatch; Streamlit test GUI. Branch: `qoder/target-architecture`.
Doc reorganization + Phase 0 blocking decisions committed in `6fea71a`.

## Architecture Decisions (locked — do not reopen)

- **Verified Principal (V1-M1):** `user_id` = Gmail account email from the verified
  OAuth grant (Mailbox Connection); `tenant_id` = fixed local-tenant constant;
  caller-provided `user_id` query parameter removed.
- **RAG (V1-M3):** minimal in-repo RAG (embedding + vector store) behind the
  retrieval-only `SemanticMemoryPort`; ACL = tenant/user namespace filtering
  before ranking/return.
- **Queue/DLQ (V1-H):** Redis Streams (consumer groups, retry/claim, DLQ stream).
  MVP loop (V1-M1..M4) stays on the in-process runtime per PRD-v1 FR-02.
- **PostgreSQL owner:** resolved 2026-08-07 (orchestrator owns the migration;
  keep the `001_mail_todo.sql` lineage, evolve `action_items` → target `tasks`;
  see Open Questions below).
- **Attachments:** presence-only (ADR-003, supersedes ADR-002).
- **Vocabulary:** `CONTEXT.md` terms are mandatory in all new code (Task not
  "action item"; Run not "digest" in new identifiers; `classifier_confidence` /
  `generation_confidence` always qualified).
- **Invariants (AGENTS.md):** raw email never persisted/logged; Gmail read-only;
  no target-state scaffolding without an explicit milestone citation.

## Dependency Graph and Parallel Lanes

```text
P0 fixtures (compat + routing) ─┬─► V1-M1 contracts/principal/envelope/cleanup/removals
                                │         │
                                │         ▼
                                └─► V1-M2 classifier/correlation/resolver ──┐
                                                        Lane RAG (parallel):  │
                                      SemanticMemoryPort + in-repo RAG ──────┤
                                                                              ▼
                                    V1-M3 generator/validators/fallbacks (joins)
                                              │
                                              ▼
                                    V1-M4 persistence/presentation/telemetry
                                              │
                                   PRD-v1 §15 gate review (orchestrator)
                                              │
                                              ▼
                       V1-H PostgreSQL repos + Redis queue/DLQ + observability
                                              │
                                              ▼
            V2-M1 gateway → V2-M2 profile → V2-M3 episodes → V2-M4 lifecycle
                                              │
                                              ▼
                       V2-M5 selective retrieval → V2-M6 evaluation/governance
                                              │
                                   PRD-v2 §16 gate review (orchestrator)
                                              │
                                              ▼
                          DEMO Increment A (v1) then Increment B (v2)
```

**Parallel lanes inside a phase** (safe for concurrent subagents):

| Phase | Lane A | Lane B | Lane C |
|---|---|---|---|
| Phase 0 | compatibility suite (P0-A) | routing fixtures (P0-B) | baseline capture script (P0-C) |
| V1-M1 | contracts + principal (T1.1/T1.2) | envelope consolidation (T1.3) after contracts merge | cleanup/TTL (T1.4) after T1.3 |
| V1-M2 | classifier port + batching (T2.1/T2.2) | route resolver + guards (T2.4) pure-function, fixture-driven | correlation (T2.3) after T2.2 |
| V1-M3 | in-repo RAG + SemanticMemoryPort (T3.1/T3.2) — fully independent | generator + validators (T3.3/T3.4) | fallbacks (T3.5/T3.6) after T3.3 |
| V1-M4 | persistence + mapper (T4.1/T4.2) | telemetry + dev trace (T4.4/T4.5) | presentation (T4.3) after T4.2 |
| V1-H | PostgreSQL adapters (T5.1) | Redis queue/DLQ (T5.2) after T5.1 run store | lifecycle events + retries (T5.3/T5.4) |
| V2 | mostly sequential; V2-M5 relevance scoring can parallel V2-M4 UI API | | |
| DEMO | Increment A screens are sequential on one GUI file; fixtures/test-data prep parallel | | |

**Must stay sequential:** anything touching `workflow.py` core loop in the same
phase; migrations; shared contracts (define contract first, then parallelize).

## Subagent Dispatch Protocol (standing rules)

1. Orchestrator writes spec + plan first (this document + phase refinement).
2. Each dispatch includes: task IDs, the authority doc list (AGENTS.md →
   PRD-v1/v2 → master-comparison → TARGET-ARCHITECTURE → CONTEXT.md), the
   verification rule, and the vocabulary rule.
3. Every task clears the Definition of Done: focused pytest green,
   `python -m ruff check .` clean, `python -m mypy src` clean (when `src/`
   touched), no invariant violations.
4. Orchestrator reviews each completed task (`code-review-and-quality`) before
   merging; phase ends only when its exit criteria (master-comparison §7) have
   evidenced tests.

## Definition of Done (project-wide)

- [x] Focused pytest scope passes (`python -m pytest <scope> -q`)
- [x] `python -m ruff check .` clean
- [x] `python -m mypy src` clean (strict) when `src/` changed
- [x] Vocabulary per `CONTEXT.md`; no new "digest"/"action item"/"extractor" names
- [x] Raw email bodies absent from any persisted/logged/response payload
- [x] Compatibility suite (`tests/compatibility/`) still green after V1-M1 starts

---

## Phase 0 — Fixtures, baseline, contract freeze

Exit criteria (master-comparison): compatibility and routing fixtures detect
regressions before provider prompts change; attachment scope unambiguous (done:
ADR-003); blocking decisions resolved (done: 2026-08-07 commit).

### Task P0-A: Compatibility test suite (freeze current behavior)

**Description:** Pin the legacy API/result contract as a dedicated suite so
V1-M* refactors cannot silently change behavior the compatibility contract
(master-comparison §7 "Compatibility contract") promises.

**Acceptance criteria:**
- [ ] `tests/compatibility/test_api_contract.py`: `POST /v1/mail-todo/runs`
      returns 202 + `{id, status, statusUrl}`; duplicate `Idempotency-Key`
      returns the same run id; `GET runs/{id}` shape (`progress`, `error`);
      result-before-terminal returns 409 `RUN_NOT_COMPLETE`; result shape keys
      exactly `{run, actionItems, nextActions, attachmentWarnings,
      processedEmails, message}`; `nextActions == actionItems[:3]`;
      empty-state message `"Không có công việc cần xử lý"`;
      `processedEmails` present only when `APP_ENV` is development.
- [ ] `tests/compatibility/test_ordering_and_dedupe.py`: priority ordering
      urgent→high→medium→low then deadline-presence then deadline; fingerprint
      dedupe within a run; `freshness=seen` across runs via `fingerprint_seen`.
- [ ] `tests/compatibility/test_query_guard.py`: `normalize_query` can narrow
      but never broadens beyond `is:unread in:inbox`; `max_emails` clamp 1..500.
- [ ] `tests/compatibility/test_privacy_boundary.py`: no response payload or
      stored record contains an email body (assert against fixture bodies).

**Verification:** `python -m pytest tests/compatibility -q` green.

**Dependencies:** None. **Files:** `tests/compatibility/` (new dir, ~4 files).
**Scope:** M.

### Task P0-B: Labeled routing fixture set

**Description:** Build the first labeled routing evaluation dataset (PRD-v1 §14,
§16 M2): representative emails with human-labeled expected actionability,
sufficiency, route, and reason codes.

**Acceptance criteria:**
- [ ] `tests/fixtures/routing/routing_labels.json`: ≥25 cases covering all five
      actionability labels, all three routes, each FR-07 guard category
      (policy, governance, procedure, forms, templates, tax/regulatory,
      internal term), the false-negative-retrieval risk case (PRD-v1 §14),
      correlated-thread cases, and Vietnamese + English content. Reuse/adapt
      `tests/fixtures/emails/sample_emails.json` entries.
- [ ] Schema documented in `tests/fixtures/routing/README.md`: fields
      `id, subject, sender, body, labels{actionability, email_is_sufficient,
      expected_route, reason_codes[]}`.
- [ ] Loader `tests/fixtures/routing/loader.py` (typed, mypy-clean) used by
      later evaluation tests.

**Verification:** loader unit test passes; JSON validates against documented
schema.

**Dependencies:** None. **Files:** `tests/fixtures/routing/` (3 files).
**Scope:** M.

### Task P0-C: Combined-extractor baseline capture

**Description:** Capture today's combined-extraction quality/latency/call-count
baseline on the routing fixtures so the split-call migration (V1-M2) has a
regression gate (master-comparison Phase 0 item 3).

**Acceptance criteria:**
- [ ] `scripts/capture_baseline.py`: runs the configured LLM provider
      (`LLM_PROVIDER`) over the routing fixtures via the current
      `ActionExtractorPort`; records per-email classification agreement vs
      labels, call count, latency, and writes
      `docs/baselines/combined-extractor-baseline-<date>.json`.
- [ ] Marked live-provider-only (skips gracefully with a message when no API
      keys); never persists raw bodies beyond the report's metadata fields
      (agreement stats only).
- [ ] `docs/baselines/README.md` explains regeneration command.

**Verification:** `python scripts/capture_baseline.py --help` works; dry-run
mode over fakes passes a smoke test.

**Dependencies:** P0-B. **Files:** `scripts/`, `docs/baselines/`. **Scope:** M.

### Task P0-D: Correlation contract freeze (orchestrator, no subagent)

Write `docs/references/task-candidate-correlation-contract.md` freezing:
bounded classifier batches; one Route Decision per selected email;
deterministic thread/incident correlation preserving `source_message_ids` +
`incident_key`; one Route per Task Candidate; zero-or-one retrieval; exactly
one Generator call per resolved non-`NO_ACTION` candidate
(master-comparison §7 "Target execution unit and call cardinality" already
decides this — formalize only).

### Checkpoint: Phase 0
- [ ] `python -m pytest -q` full suite green (shared-contract addition)
- [ ] `ruff` + `mypy` clean
- [ ] Commit Phase 0 artifacts; review with user before V1-M1

---

## V1-M1 — Core contracts and Gmail entry

Satisfies PRD-v1 FR-01, FR-03, FR-04, FR-14, §13.

### Task 1.1: Versioned target contracts (domain layer)

Define in `src/cowork_agent/domain/` (or `features/email_action_plan/contracts/`):
`EphemeralEmailEnvelope` (6.1), `EmailRouteDecision` (6.2), `ActionPlanOutput`
(6.6), `TraceEvent` skeleton (6.8) — fields exactly per master-comparison
Step 6, including `source_message_ids`, `incident_key`, `urgent` priority,
`attachments_processed: false` literal. `TaskEpisode`/`MemoryContextRequest`
deferred to V2-M1. Round-trip serialization tests.

**Acceptance:** contract unit tests green; mypy strict clean; zero framework
imports in domain. **Deps:** Phase 0. **Scope:** M.

### Task 1.2: Verified Principal boundary

Remove `user_id` query parameters from all endpoints; resolve principal from
the Mailbox Connection's verified OAuth identity (`email_address`);
`tenant_id` = fixed local constant module (`identity.py`). All ownership checks
(`connection.user_id != user_id` → `principal owns connection`) centralized in
one guard. OAuth connect flow binds identity at callback. Update integration
tests + GUI call sites.

**Acceptance:** no endpoint accepts caller-provided identity; authorization
tests (cross-connection access → 404); compatibility suite green for shapes
(status URL no longer carries `user_id`). **Deps:** 1.1. **Scope:** L — split
into 1.2a (identity module + guard) and 1.2b (endpoint/GUI migration) if a
subagent prefers. **Files:** `app.py`, `api/handlers.py`,
`integrations/gmail/auth.py`, `gui/app.py`, tests.

### Task 1.3: Ephemeral Envelope consolidation + attachment non-processing

Consolidate `EmailEnvelope`/`ThreadContext` into `EphemeralEmailEnvelope`;
Gmail adapter records `attachments_present` only — `download_attachment`
removed from the production path (keep behind deprecation marker for tests per
ADR-003 transition clause); `attachments_processed=false` always.

**Acceptance:** existing no-download test stays green; envelope carries all 6.1
fields; `fetch_status` complete/partial reported. **Deps:** 1.1. **Scope:** M.

### Task 1.4: Explicit short-term cleanup + safety TTL

Add run finalizer clearing envelope/body state after success AND failure paths
(FR-14); safety TTL sweep for incomplete cleanup; tests assert no raw body
survives completion (object lifetime + store inspection).

**Acceptance:** cleanup tested on success, failure, and partial paths; TTL
tested with injected clock. **Deps:** 1.3. **Scope:** M.

### Task 1.5: Remove fake queue wiring + unwired MailTodoApi

Delete production wiring of write-only `InMemoryQueue`/`InMemoryOutbox` from
`app.py` (keep classes as deterministic test fakes per master-comparison §4.4);
delete unwired `MailTodoApi` from `api/handlers.py`. Only after
`tests/compatibility/` covers the live routes (P0-A done).

**Acceptance:** compatibility suite green; no production code path references
the fakes; grep confirms removal. **Deps:** P0-A, 1.2. **Scope:** S.

### Checkpoint: V1-M1 exit criteria
- [ ] Duplicate create → exactly one logical run (tested)
- [ ] Run/status/result compatibility suite passes
- [ ] Attachment presence never triggers download/extraction
- [ ] No raw email survives run completion (cleanup + TTL tested)
- [ ] Gmail access gated by verified identity

---

## V1-M2 — Classification and routing

Satisfies PRD-v1 FR-05..FR-07, §12.2.

- **T2.1 Split ports:** `RouteClassifierPort` + `ActionPlanGeneratorPort`
  replace `ActionExtractorPort` usage in the workflow; Gemini/Groq adapters
  implement the classifier port first; deterministic plan shaping
  (sanitization, caps, dedupe, priority, ordering, incident merge) moves from
  `providers/gemini.py` to application services. Provider adapters keep only
  transport + structured parsing. (Scope L — split per provider if parallel.)
- **T2.2 Bounded classification:** batched classifier calls, one
  schema-validated `EmailRouteDecision` per selected email; schema validation
  + one repair retry per PRD-v1 §12.2.
- **T2.3 Task-candidate correlation:** deterministic thread/incident grouping
  preserving `source_message_ids`/`incident_key` per P0-D contract.
- **T2.4 Route Resolver + Policy Guards:** pure `resolve_route()` implementing
  FR-06 ladder + FR-07 guard categories; deterministic, table-driven tests
  over routing fixtures.
- **T2.5 Classifier fallback:** retry once → conservative `RETRIEVE_RAG`
  (exact §12.2 sequence).
- **T2.6 Routing evaluation:** evaluation test harness over
  `routing_labels.json`; runs after every prompt/provider change; reports
  precision/recall incl. the false-negative-retrieval metric.

**Exit criteria:** batch/decision counts observable and schema-valid;
correlation preserves legacy behavior (compat suite); resolver pure +
guard-covered; fallback matches §12.2. **Deps:** V1-M1. **Scope total:** L
(6 tasks, 2 lanes parallel).

---

## V1-M3 — RAG, generation, validation

Satisfies PRD-v1 FR-08..FR-11, §12.3, §12.4.

- **T3.1 SemanticMemoryPort + null adapter:** retrieval-only contract per 6.4/
  6.5; null adapter returns structured `no_results`.
- **T3.2 In-repo RAG production adapter:** minimal embedding + vector store
  (dependency choice recorded in the task spec at phase start — prefer
  stdlib-friendly options; e.g. provider embeddings + numpy cosine or
  sqlite-vec); corpus loader for local `knowledge/` documents; tenant/user
  ACL filtering **before** ranking; never ingests email content.
- **T3.3 Generator:** exactly one final generation call per resolved
  non-`NO_ACTION` candidate; v1 inputs = email context + route decision +
  optional RAG context + system defaults only (no profile/episodes).
- **T3.4 Validators:** schema, grounding (company-specific step ⇒ citation
  from current retrieval), citation-id integrity, privacy (no raw body in
  output), unsupported-procedure detection.
- **T3.5 RAG failure path:** bounded retry once → structured empty → Partial
  Plan with `missing_information`; never invents procedure.
- **T3.6 Generation failure path:** one schema-repair retry → fail per error
  policy. `DIRECT_PLAN` asserts zero retrievals (call-counter test).

**Exit criteria:** generator count == non-`NO_ACTION` candidates; null/no-result
⇒ partial plan; grounded steps require current citations; no retrieval on
`DIRECT_PLAN`; raw email absent from outputs. **Deps:** V1-M2. T3.1/T3.2 run
in a parallel lane. **Scope total:** L (6 tasks).

---

## V1-M4 — Persistence and presentation

Satisfies PRD-v1 FR-12, FR-13, FR-15, FR-16, §12.5.

- **T4.1 Task persistence port + local adapter:** idempotent, keyed
  `tenant_id:user_id:gmail_message_id:pipeline_version`; no raw body stored.
- **T4.2 Compatibility mapper:** persisted Tasks → legacy result shape
  (`actionItems`, `nextActions`, warnings, empty-state); versioned.
- **T4.3 Cowork presentation:** GUI shows tasks with Gmail pointer, citations,
  priority/deadline, missing-information warnings.
- **T4.4 Basic telemetry:** metadata-only run status, route/reason codes,
  `classifier_confidence`, retrieval status/count, validation status, stage
  latency, errors/fallbacks (TraceEvent 6.8, production-safe fields only).
- **T4.5 Development trace:** marker "ALLOW ONLY FOR CURRENT DEVELOPMENT
  STAGE", encrypted at rest, TTL, hard production guard (cannot enable when
  `APP_ENV` is production).

**Exit criteria:** task rows body-free; idempotent replay safe; compatibility
suite green against persisted outputs; production telemetry metadata-only;
dev trace prod-guarded. **Deps:** V1-M3. **Scope total:** L (5 tasks).

### Gate: PRD-v1 §15 acceptance review (orchestrator)

Walk acceptance criteria 1–19 with test evidence; only then start V1-H and
DEMO Increment A.

**Verdict: PASS (2026-08-08, orchestrator).** Suite 255 passed at `d93ec3b`.
Evidence per criterion (file::test; `tw` = `tests/integration/email_action_plan/test_workflow.py`):

1. Idempotent run — `tw::test_same_idempotency_key_creates_only_one_run`,
   `tests/compatibility/test_api_contract.py::test_duplicate_idempotency_key_returns_same_run`.
2. Read-only scope — `tests/unit/integrations/gmail/*::test_gmail_settings_allow_readonly_scope_and_redact_secrets`,
   `::test_gmail_settings_reject_write_scope`.
3. Scope unbroadenable — `tests/compatibility/test_query_guard.py::test_broadening_attempts_always_keep_unread_inbox_guard`,
   `tests/unit/features/email_action_plan/test_policies.py::test_query_is_always_read_only_unread_inbox_scope`.
4. Ephemeral envelopes — `tw::test_envelopes_reaching_extraction_carry_stamped_run_identity` + gmail parser tests.
5. Attachments presence-only — `tw::test_attachment_is_recorded_without_download_or_extraction`,
   `tests/unit/domain/test_target_contracts.py::test_attachments_processed_rejects_true`.
6. Classifier decision or §12.2 fallback — `tests/unit/integrations/llm/*::test_both_attempts_invalid_fall_back_only_for_affected_messages`,
   `::test_transport_outage_falls_back_for_every_message_without_raising`.
7. Three-route resolution — `tests/unit/features/test_routing.py::test_resolve_route_reproduces_fixture_labels`,
   `::test_candidate_route_retrieve_rag_wins_over_direct_plan`, `::test_candidate_route_all_no_action`.
8. DIRECT_PLAN zero retrieval — `tw::test_direct_plan_candidate_makes_zero_retrieval_calls`,
   `tests/unit/features/test_validation.py::test_direct_plan_strips_all_citations_even_supported`.
9. Retrieval-only semantic interface — `tests/unit/features/test_ports.py::test_semantic_memory_port_is_retrieval_only`,
   `tw::test_retrieve_rag_candidate_retrieves_once_and_feeds_generator`.
10. One generation per actionable candidate — `tw::test_pipeline_orders_items_by_priority_before_deadline`
    (`call_count == 3`), `tw::test_generation_failure_fails_run_with_safe_error` (`call_count == 1`).
11. Valid current-retrieval citations — `test_validation.py::test_supported_citations_pass_without_violation`,
    `::test_only_unsupported_citations_are_stripped`, `::test_empty_retrieval_strips_every_citation`.
12. RAG failure → partial plan + missing info — `tw::test_retrieval_failure_retries_once_then_degrades_to_structured_empty`,
    `tw::test_genuine_empty_retrieval_marks_missing_info_without_degraded_marker`.
13. Persisted with Gmail pointers, body-free — `tw::test_validated_tasks_are_persisted_with_identity_and_pipeline_version`,
    `tw::test_sqlite_persisted_tasks_are_body_free`,
    `tests/compatibility/test_privacy_boundary.py::test_email_body_never_appears_in_responses_or_stored_records`.
14. Tasks visible in Cowork — API: `tests/compatibility/test_api_contract.py::test_tasks_endpoint_exposes_persisted_task_contracts`.
    **Documented gap:** GUI (`src/cowork_agent/gui/app.py`) has no automated tests; live-browser
    verification deferred to DEMO Increment A screens work.
15. Ephemeral content cleared/TTL — `tests/unit/features/test_short_term.py` (TTL, sweep, finalizer),
    `tw::test_successful_run_finalizer_clears_short_term_memory`, `tw::test_failed_run_finalizer_clears_short_term_memory`.
16. Telemetry metadata-only — `tw::test_telemetry_emits_metadata_only_candidate_and_run_events` (dump-scan),
    `tw::test_telemetry_marks_failed_run_with_error_code_only`.
17. Dev trace guarded + TTL — `tests/unit/features/email_action_plan/test_observability.py`
    (`::test_dev_trace_sink_refuses_construction_in_production`, `::test_dev_trace_write_read_round_trip_is_encrypted_markered_ttls`,
    `::test_dev_trace_expired_records_are_not_returned`), `tw::test_dev_trace_writes_encrypted_full_content_with_marker`.
18. Metrics emitted — `tw::test_telemetry_emits_metadata_only_candidate_and_run_events` (routing/validation/latency),
    `tw::test_telemetry_marks_degraded_retrieval_fallback` (retrieval), error-code-only events.
    NIT: emitted `reason_codes` only covered via contract round-trip.
19. No scheduler — no scheduler/schedule-config/recurring code in `src/` (SCHEDULED/schedule_id
    removed at `d93ec3b`; the `001_mail_todo.sql` baseline dropped the schedule tables at V1-H T5.1).

Follow-ups (non-blocking): GUI live verification via DEMO-A; emitted
`reason_codes` assertion; FR-11 note localization. `digest_schedules`
resolved at V1-H T5.1 (2026-08-08): dropped from the PostgreSQL baseline —
scheduling is a PRD-v1 non-goal. Live routing-accuracy evaluation (§14
metrics, T2.6) remains pending user-authorized runs but is not a §15
criterion.

---

## V1-H — Durable control plane (hardening)

PostgreSQL ownership resolved 2026-08-07 (see Open Questions): orchestrator
owns the migration; T5.1 evolves the `001_mail_todo.sql` lineage per §6.6.

- **T5.1 PostgreSQL run/task/outbox repositories:** use existing
  `persistence/migrations/001_mail_todo.sql` lineage as migration input;
  atomic idempotent create; compare-and-set claim; migrate from local adapters.
- **T5.2 Redis Streams queue + DLQ:** producer/consumer worker, bounded retry,
  claim semantics; DLQ payloads exclude email body/attachment bytes/OAuth
  tokens; replaces `BackgroundTasks` dispatch. Done 2026-08-08: the executor
  (DigestWorker) owns the CAS claim; queue-level retry covers worker raises
  only — in-run pipeline failures stay terminal inside the worker.
- **T5.3 Lifecycle events:** replace unreadable outbox with metadata-only
  observable events wired to trace/metrics sinks.
- **T5.4 Timeout/retry budgets:** Gmail backoff/jitter, token refresh retry,
  partial-batch continuation. Also owns stuck-run recovery: sweep orphaned
  runs in RUNNING (hard worker crash, or execution longer than the queue's
  claim idle threshold) AND QUEUED-without-message (enqueue-after-create
  crash; requeue/reset race), re-enqueueing or failing them safely. Done
  2026-08-08: 3-attempt full-jitter backoff (429/5xx/transport; auth errors
  immediate); thread-level skip continues the run and marks it PARTIAL;
  compare-and-set `reset_stuck_run` keeps concurrent sweepers safe.
- **T5.5 Observability + launch gates:** alerts, numeric gates, scaled
  evaluation harness. Also owns lifecycle-event publication: a publisher
  consuming `CompletionOutboxPort.pending()`/`mark_published` into
  trace/metrics sinks, and completion events for runs forced terminal by
  DLQ retry exhaustion (T5.3 review: those currently get no event).
  Delivered 2026-08-08: `LifecycleEventPublisher` (outbox → trace sink,
  at-least-once), DLQ completion events, worker wiring. **Blocked:**
  numeric launch gates/alerts + scaled evaluation need the user's
  threshold decisions (Open Questions) and user-authorized live runs.

**Exit criteria (old Milestone 1):** API-created run visible to a separate
worker process; single-claim enforced; retry exhaustion reaches DLQ without
sensitive payloads; restart loses nothing. **Deps:** §15 gate. **Scope:** L.

---

## V2 group — Memory Extension (PRD-v2)

Each milestone mirrors master-comparison §7 V2-M*; detailed task refinement
happens at phase start (gate discipline). Granularity here is work-item level.

- **V2-M1 (gateway):** `TaskEpisode`/`MemoryContextRequest` + profile/episode/
  transition/provenance contracts; in-process Memory Gateway with namespace
  resolution, read/write eligibility, fail-closed on missing namespace; all
  Agent Core memory access routed through it. Exit: cross-tenant/cross-user
  tests fail closed.
- **V2-M2 (long-term):** PostgreSQL profile store (explicit-only writes),
  compact loading with degraded fallback (default profile + warning), deletion.
  Exit: stored preferences change later output; read failure never blocks v1.
- **V2-M3 (episodic writes):** one idempotent episode per persisted task,
  `system_generated`, `retrieval_eligible=false`; eligibility enforced at
  write AND read boundaries in code; mandatory provenance; no raw body.
- **V2-M4 (lifecycle):** approve/complete/reject API + minimal GUI control;
  transactional/idempotent transitions; eligibility rule table enforced;
  provenance timestamps.
- **V2-M5 (selective retrieval):** eligibility-filtered episodic retrieval,
  bounded + relevance-scored; selective trigger policy (never every run);
  labeled generator context (email/preference/episode/company evidence);
  conflict precedence rules (FR-13).
- **V2-M6 (evaluation/governance):** memory-enabled vs v1-baseline evaluation
  on labeled set; retention + purge + deletion audits; zero-tolerance safety
  counters; launch thresholds.

**Gate:** PRD-v2 §16 acceptance criteria 1–20 with evidence → DEMO Increment B.

---

## DEMO — Showcase frontend (SPEC-Demo-Frontend)

- **DEMO-A (Increment A, after §15):** Connect / Run / Tasks / Task detail /
  Run audit screens in `gui/app.py` (Streamlit); idempotent Run creation;
  Partial-Plan treatment; citation chips; all states (loading/empty/error/
  success); bilingual-ready copy. Live-verify per SPEC §9 with browser-use MCP
  + RunPreview; screenshot evidence in the merge record.
  - Status 2026-08-08: five-screen implementation + `tests/unit/gui/` helper
    suite landed (import-safe module, VI/EN catalogs, Idempotency-Key reuse on
    retry); ruff/mypy/full suite green; reviewer SHOULD-FIX items applied.
    Outstanding: SPEC §9 live-browser verification with the user's Gmail OAuth.
- **DEMO-B (Increment B, after §16):** Preferences / Task lifecycle / Memory
  insight / Memory effect / Deletion screens, feature-flagged off until
  endpoints exist.

**Exit:** SPEC §8 criteria 1–10 with browser-verified evidence.

---

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Prompt drift breaks routing mid-migration | High | P0 fixtures frozen first; routing evaluation after every prompt change |
| Compatibility shape regressions during refactor | High | `tests/compatibility/` runs in every phase checkpoint |
| Provider SDK surprises (Gemini/Groq structured output) | Med | `source-driven-development` skill at V1-M2/M3 |
| In-repo RAG scope creep | Med | Minimal adapter: loader + embeddings + cosine top-k; no reranker/admin UI in v1 |
| Subagent invariant violations (raw email logging) | High | Privacy test in every phase; orchestrator review before merge |
| Grounded-step validation false positives | Med | Validator tests use routing fixtures incl. partial-plan cases |
| Windows/PowerShell path or env quirks | Low | Verification rule uses module invocation (`python -m …`) |

## Open Questions

- ~~PostgreSQL deployment/migration owner (before V1-H).~~ **Resolved 2026-08-07**
  (user delegated): orchestrator owns the PostgreSQL migration. Decision: keep
  the `persistence/migrations/001_mail_todo.sql` lineage as the migration input —
  `mailbox_connections` and `digest_runs` match the current models; evolve
  `action_items` into the target `tasks` table per master-comparison §6.6
  (idempotent key `tenant_id:user_id:gmail_message_id:pipeline_version`); drop
  `attachment_extractions` (ADR-003), `digest_schedules`, `schedule_occurrences`
  (scheduling out of PRD scope); replace `outbox_events` with the V1-H
  observable lifecycle events (T5.3).
- Numeric launch thresholds (before V1-H gates).
- Preference field set narrowing + approval-UI shape (before V2-M2/M4).
- Episodic relevance algorithm/thresholds (before V2-M5).
- Retention periods + memory quality-improvement threshold (before V2-M6).
