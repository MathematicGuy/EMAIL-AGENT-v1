# Session Bootstrap & PRD-v2 Delivery Dashboard

> Start here in a new coding session. This file is the compact operational
> view for planning, implementation, and project tracking. Read `AGENTS.md`
> first, then this file. Open a larger PRD/architecture document only when the
> routing table below says the current task needs it or a conflict appears.

| Field | Current value |
|---|---|
| Updated | 2026-08-11 (Asia/Bangkok) |
| Branch / implementation baseline | PRD-v2 foundation checkpoint `fc3c0b7` on `feature/v2-m3-chat-summary`; live `dev` is `15ea2b9`, merge-base remains `148a779`; no merge, rebase, reset, or push performed |
| Product frontier | PRD-v2 Multi-Turn AI Chat Memory + executable `@Email` tool |
| Active milestone | **V2-M5 — Selective Chat Retrieval (ACTIVE: semantic runtime + labeled precedence verified; episodic runtime depends on deferred tool lifecycle)**; V2-M4A local controller/SSE is VERIFY; `@Email` lifecycle remains LAST |
| PRD-v2 progress | **2.75 / 6 milestones; 3 / 20 acceptance criteria complete; 4 criteria partial** |
| Tech stack authority | PostgreSQL (authoritative durable store); Qdrant (rebuildable enterprise RAG index) |
| Priority override | `@Email` tool slices are deprioritized to LAST (user, 2026-08-10) — see §5 |

## 1. New-session launch sequence

Do these in order; do not reread the entire documentation set first.

1. Read `AGENTS.md` (project rules and four non-negotiable invariants).
2. Read this handoff completely.
3. Run `git status --short --branch` and inspect uncommitted worktree changes.
4. For V2-M3/V2-M4A, read only:
   - `docs/references/handoff-prd-v2-implementation.md` §0, §3, §7;
   - `docs/PRD-v2-Memory-Extension.md` FR-01, FR-02, FR-06..FR-10, FR-15..FR-18;
   - `docs/references/qdrant-postgresql-techstack-evaluation.md` §1–3.
5. Execute the immediate next task plan from §7 below.
6. Update the dashboard using §12 before ending the session.

Recommended skills for implementation: `api-and-interface-design`, `test-driven-development`, `incremental-implementation`, `security-and-hardening`, `git-workflow-and-versioning`.

## 2. Project brain dump & tech stack authority

```text
PROJECT:
  Cowork Agent — FastAPI + Python 3.12-style typed code + Streamlit demo.

CURRENT PRODUCT:
  A completed deterministic, read-only Gmail → classification → optional
  HybridSemanticMemory RAG → cited Action Plan pipeline.

TARGET PRODUCT:
  A multi-turn AI Chat Assistant. The Chat Controller owns all four memory
  types through one Memory Gateway. @Email is an allow-listed in-chat tool
  that executes the existing Email RAG pipeline statelessly.

STORAGE & MEMORY ARCHITECTURE:
  1. Working: bounded active-session turns + transient tool state (In-process TTL).
  2. Declarative: explicit persona/preferences only (PostgreSQL `chat_profiles`).
  3. Episodic: chat summaries + derived @Email Action Plans (PostgreSQL `chat_summary_episodes`).
  4. Semantic: enterprise RAG (Qdrant Cloud/in-repo index via `SemanticMemoryPort`).

SAFETY MODEL:
  New tool/summary episodes start system_generated + retrieval_eligible=false.
  Only approved/completed episodes become retrievable. Raw Gmail bodies are
  transient and never enter DB rows, chat history, logs, traces, prompts
  stored for replay, browser storage, or indexes.
```

## 3. Guardrails and workspace state

Non-negotiable:

- Gmail scope stays `gmail.readonly`; never send, modify, delete, or move mail.
- Raw email bodies and attachment content are never persisted or logged.
- Attachments are presence-only (`attachments_processed=false`, ADR-003).
- PostgreSQL is the authoritative application store for runs, tasks, outbox, profiles, episodes, and retention state.
- Qdrant is a rebuildable derived index for approved company-knowledge RAG only.
- SQLite is explicitly local-only / dev baseline; do not rely on it as a production store.
- Domain and feature code remain framework-free (`domain ← features ← integrations/orchestration/persistence ← app`).

Current active worktree state:
- Branch `feature/v2-m3-chat-summary` at base `eb474b8` has uncommitted V2-M3 chat summary code (domain contracts, `episode_policy.py`, `memory_gateway.py`, `003_chat_summary_episodes.sql` migration, `PostgresChatSummaryEpisodeRepository`, unit/integration tests).
- `dev` at `148a779` contains 9 Qdrant integration commits. Reconciliation via `git merge-tree` is needed before commit/merge.
- Verification status: 104 AI Chat focused tests passed, 18 PostgreSQL repository tests passed against `cowork-pg`, 20 Qdrant unit tests passed, Ruff clean, `mypy src` clean across 69 files.

## 4. Authority and selective document routing

| Need | Read only this |
|---|---|
| Always-needed rules | `AGENTS.md` |
| Focused implementation restart | `docs/references/handoff-prd-v2-implementation.md` |
| Tech stack & storage authority | `docs/references/qdrant-postgresql-techstack-evaluation.md` |
| Product behavior / acceptance | `docs/PRD-v2-Memory-Extension.md` relevant FR + §16–17 |
| Component ownership, APIs, SSE | `docs/architectures/TARGET-ARCHITECTURE.md` §5, §7–10, §16–17 |
| Exact DTO contracts / sequencing | `docs/master-comparison.md` §6 and V2 milestone in §7 |

Conflict precedence: `AGENTS.md` > PRD-v2 > Target Architecture > Master Comparison > Dashboard/Trackers.

## 5. PRD-v2 project-management dashboard

Status legend: `NEXT` = ready now, `BLOCKED` = dependency not met, `ACTIVE` = implementation in progress, `VERIFY` = code done/evidence pending, `DONE` = exit gate evidenced.

| Milestone | Status | % | Deliverable / exit gate | Depends on | Evidence |
|---|---:|---:|---|---|---|
| V2-M1 Gateway + session working memory | **DONE** | 100 | Chat/memory contracts; fail-closed namespace; bounded session TTL; no gateway bypass | PRD-v1 baseline | `7e42784..2a29e29`; 73 focused tests; deterministic suite exit 0 |
| V2-M2 Declarative chat profile | **DONE** | 100 | Explicit-only profile CRUD; per-turn compact load; fallback; deletion/retention | V2-M1 | Policy/port/gateway/migration/repo landed; 496 passed; PostgreSQL gate 15 passed against `cowork-pg` |
| V2-M3 Chat summary episodes | **ACTIVE** | 75 | Bounded chat summaries (500 char); mandatory provenance; `retrieval_eligible=false` default; `003_chat_summary_episodes.sql` + PostgreSQL repo verified | V2-M1, V2-M2 | `test_episode_policy.py`, `test_memory_gateway.py`, `test_chat_summary_repository.py` green (104 AI chat tests + 18 Postgres tests) |
| V2-M4 Chat Controller + SSE + tool | **VERIFY** | 35 | Session/message APIs, Chat Controller loop, typed assistant SSE landed; `@Email` tool/card/transitions deferred LAST | V2-M1–M3 | 12 focused controller/API tests; principal binding, cancellation, replay, typed provider failure proven |
| V2-M5 Selective episodic + RAG retrieval | **ACTIVE** | 70 | Query-scoped contracts, deterministic intent policy, Gateway filtering/degradation, ready-only Qdrant/semantic runtime, and labeled precedence assembler landed; real eligible episodic records/runtime and reply-provider consumption depend on deferred tool lifecycle | V2-M4A | Central post-fix focus 96 passed; generation-context 2 passed; Ruff and narrowed mypy clean |
| V2-M6 Evaluation + governance | **ACTIVE** | 55 | Metadata-only Gateway events, paired launch gate, exact-scope retryable bulk deletion, optional durable retention settings, and explicit purge coordinator landed; production sink/alerts, backup/restore, and runtime DB deletion proof remain | V2-M5 core contracts | Central governance focuses green; PostgreSQL deletion test present but server-gated skip |

**Priority override (user, 2026-08-10): executable `@Email` chat tool is built LAST.**

## 6. PRD-v2 acceptance dashboard

| ID | Acceptance statement | Status | Evidence |
|---|---|---:|---|
| AC-01 | Chat Controller accesses all four memories only through Gateway | PARTIAL | V2-M4A Controller reads working/profile context only through Gateway; episodic/semantic wiring pending V2-M5 |
| AC-02 | Every operation carries tenant/user/session/`feature: ai_chat`/type | DONE | `310d2fd`, `2a29e29`; domain namespace + gateway tests |
| AC-03 | Bounded working buffer preserves turns and expires by policy | DONE | `7e42784`, `2a29e29`; `tests/unit/features/ai_chat/test_session_buffer.py` |
| AC-04 | Explicit persona/preferences persist and load in later sessions | DONE | Profile policy/gateway/PostgreSQL repo green (`test_chat_profile_repository.py` 15 passed) |
| AC-05 | User can invoke `@Email` inside a chat thread | TODO | Deferred to last (§5 priority override) |
| AC-06 | `@Email` stays stateless and owns no durable memory | TODO | — |
| AC-07 | Assistant/tool/card events stream to the active session | PARTIAL | Typed assistant delta/error/completed SSE proven by controller/API tests; tool/card events deferred LAST |
| AC-08 | Rendered tool plan writes one idempotent system-generated episode | TODO | Chat summary episode half proven in V2-M3; tool plan episode deferred |
| AC-09 | New tool episode is retrieval-ineligible | TODO | Policy and schema `CHECK (retrieval_eligible = FALSE)` proven in V2-M3 |
| AC-10 | Inline approval/completion makes episode eligible | TODO | — |
| AC-11 | Inline rejection keeps episode ineligible | TODO | — |
| AC-12 | Retrieval returns approved/completed episodes only | TODO | — |
| AC-13 | Model cannot retrieve unvalidated episodes directly | TODO | — |
| AC-14 | Episodic and semantic retrieval are selective and bounded | PARTIAL | Deterministic independent intent triggers, server-owned bounds, Gateway eligibility filtering, ready-only Qdrant enforcement, and production semantic composition are proven; real eligible episodic runtime awaits deferred tool episodes |
| AC-15 | Current company evidence outranks prior episode guidance | PARTIAL | Pure typed assembler declares `current_instruction > current_company_evidence > stored_preference > advisory_episode`, preserves citations, and labels episodes advisory; live reply-provider consumption is not yet configured |
| AC-16 | Raw email absent durable memory, chat, telemetry, browser storage | TODO | Checked in V2-M2 & V2-M3 repository schema tests |
| AC-17 | Deletion prevents later retrieval | PARTIAL | Individual deletion plus retryable all-memory Gateway deletion are unit-proven; SQL is exact tenant/user/feature and foreign-row integration test is present, but local PostgreSQL runtime was unavailable so that node skipped |
| AC-18 | Production telemetry is metadata-only | PARTIAL | Typed Gateway memory events exclude content, identity, query, URLs, citations, and exception text; sentinel and failing-sink tests pass. Production sink/alert wiring remains. |
| AC-19 | Memory outage degrades chat without corrupting `@Email` | PARTIAL | Gateway fallback plus PostgreSQL OperationalError translation proven; stateless `@Email` half pending |
| AC-20 | No scheduler, recurring scan, or autonomous email action added | TODO | — |

## 7. Active implementation plan — full PRD-v2 continuation

Goal: complete the remaining PRD-v2 acceptance criteria in dependency order while
preserving the user priority that executable `@Email` work is implemented last.
Each task is an acceptance-sized slice: tests first, focused verification, supervisor
diff review, then dashboard evidence before the next dependency starts.

### Task 1: V2-M3/V2-M4A acceptance stabilization — IN PROGRESS
1. **Diff Review:** Review the accumulated chat-summary, controller, API, and SSE changes.
2. **Runtime Safety:** Prove principal binding, idempotency, cancellation, bounded state,
   and typed optional-memory degradation.
3. **Verification:** Run focused AI Chat/API/persistence tests, `ruff check .`, and
   `mypy src`; do not perform Git history operations without separate authorization.

### Task 2: V2-M4A Chat Controller & SSE Engine
1. **Controller Core:** Build `src/cowork_agent/features/ai_chat/controller.py` to validate scope, load working memory and compact declarative profile via `MemoryGateway`, and process chat turns.
2. **SSE Streaming Event Generator:** Emit typed stream events (`ChatMessageStreamEvent`, `ChatEventType`) for message start, content delta, and completion.
3. **API Adapter:** Add route handlers in `src/cowork_agent/api/` for session lifecycle and chat SSE streaming.
4. **Exclusions:** No `@Email` execution, no Action Plan cards, no inline approval controls in this slice.

### Task 3: V2-M5 Selective Episodic and Semantic Retrieval
1. Add typed intent-gated retrieval policy tests before implementation.
2. Request eligible, approved/completed episodic context and bounded company RAG
   context only when the verified chat intent requires it.
3. Keep `MemoryGateway` as the sole feature-level boundary, preserve source labels
   and citations, and enforce current company evidence over older episodic guidance.
4. Degrade optional retrieval failures explicitly while preserving bounded working
   memory and compact profile behavior.

### Task 4: V2-M6 Evaluation and Governance
1. Implement exact-scope retention, purge, deletion audit, propagation, idempotent
   replay, tombstones/reconciliation, and recovery behavior in independent slices.
2. Add raw-content sentinel tests across durable stores, indexes, telemetry, outbox,
   DLQ, snapshots, and fixtures.
3. Add memory-on/off evaluation, citation/continuity thresholds, safety counters,
   backup/restore ownership evidence, staged rollout, and rollback gates.

### Task 5: Deferred V2-M3/V2-M4 `@Email` Lifecycle — LAST
1. Wrap the existing deterministic read-only Email RAG pipeline as the allow-listed
   chat tool without giving it memory ownership.
2. Stream typed tool/Action Plan card events and write one idempotent,
   `system_generated`, retrieval-ineligible episode per persisted derived task.
3. Implement transactional approve/complete/reject transitions with provenance and
   timestamps; invalid transitions fail closed.
4. Run the complete PRD-v2 AC-01..AC-20 acceptance sweep.

## 8. Source and test map for next tasks

Read these before editing:

| Component | Target Files |
|---|---|
| Domain Contracts | `src/cowork_agent/domain/chat_contracts.py`, `_chat_contracts_*.py` |
| AI Chat Feature | `src/cowork_agent/features/ai_chat/{memory_gateway,episode_policy,profile_policy,session_buffer,ports}.py` |
| Chat Controller (New) | `src/cowork_agent/features/ai_chat/controller.py` |
| Persistence & Migrations | `src/cowork_agent/persistence/repositories/postgres.py`, `src/cowork_agent/persistence/migrations/001..003_*.sql` |
| Test Suites | `tests/unit/features/ai_chat/`, `tests/integration/persistence/` |

## 9. Verification commands

```bash
# Focused AI Chat & persistence test suite
python -m pytest tests/unit/features/ai_chat tests/integration/persistence -q

# Code quality & static typing
python -m ruff check .
python -m mypy src

# Full suite (at merge / acceptance boundary)
python -m pytest -q
```

## 10. Decisions, risks, and blockers

### Locked decisions
- **PostgreSQL is authoritative durable application store** (runs, tasks, outbox, profiles, episodes).
- **Qdrant is rebuildable enterprise knowledge index** for grounded RAG (`SemanticMemoryPort`).
- **Chat Controller owns all memory access** exclusively through `MemoryGateway`.
- **`@Email` tool is stateless and built LAST**; no memory reads inside `email_action_plan/workflow.py`.
- **New episodes default to `system_generated` and `retrieval_eligible=false`**.

### Active blockers
1. **Branch reconciliation:** feature HEAD is `fc3c0b7`, live `dev` is `15ea2b9`, and their merge-base remains `148a779` (`2` commits unique on each side after the checkpoint). The new `dev` commit changes GUI/performance files and removes tracked pytest artifacts; this session did not merge, rebase, reset, or push. Reconcile explicitly in a later session.
2. **Acceptance Verification:** the checkpoint is centrally green (206 focused; full suite 624 passed, 28 skipped, 4 xfailed; Ruff and mypy clean), but final post-reconciliation verification remains required.
3. **Delegated TaskEpisode implementation availability:** the last direct quota evidence remains the earlier Terra/high hard usage-limit error with reported retry time `2026-08-16 04:26`. On 2026-08-11, configured Sol Advisor high and routine Terra/high roles accepted dispatches but produced no first write, no terminal report, and no surfaced runtime error across bounded windows; all hashes stayed unchanged. This is consistent with an unavailable implementation lane but is not fresh proof that quota was the cause. Fail-closed orchestration forbids silent parent/model substitution; explicitly authorize the Luna task lane or retry Terra later.

### Delegation incident: Luna `xhigh` and `max` did not return

This incident is classified as a **delivery-completion failure, not an
implementation-quality failure**. Both Luna attempts ended without a final,
verifiable handoff inside the supervisor's observation window. The worktree
nevertheless contained a coherent partial Qdrant security implementation. The first
Terra/high recovery pass changed only Ruff import ordering and made the narrow focused
checks pass. A later fresh full-stack review correctly returned `fix-first`: adjacent
callers still supplied an empty lifecycle allowlist, arbitrary non-ready statuses were
accepted, operational failures were collapsed into `no_results`, and the timeout was
not end-to-end. Those are integration-quality gaps, but they do not explain why both
Luna attempts failed to return any final handoff.

Ranked likely causes:

| Rank | Likely cause | Confidence | Evidence / interpretation |
|---:|---|---|---|
| 1 | The agent did not finish the verification-and-reporting tail before the supervision window ended | High | Neither `xhigh` nor `max` returned a final report, while their substantive patch was present and later passed 25 focused Qdrant tests. The observable task failure is therefore failure to complete and report the turn, independent of later review findings. |
| 2 | The one-shot packet bundled security implementation, adjacent-caller migration, test expansion, formatting, type checking, and final evidence into too broad a completion boundary | High | The narrow adapter tests passed, but fresh review found omitted full-stack caller, outage, allowlist, and deadline cases. The task needed smaller acceptance slices and an explicit cross-caller regression sweep. |
| 3 | Execution/tool latency or an interrupted child-task lifecycle prevented a clean terminal response | Medium | The supervisor observed non-return, but no trustworthy child-runtime diagnostic identifies whether a command stalled, the task was interrupted, or the response deadline expired. These mechanisms remain indistinguishable. |
| 4 | Insufficient task context | Low-medium | The inherited patch correctly implemented exact tenant equality before embedding, document-status filtering, payload indexes, timeout propagation, and focused tests, so the core constraints were understood. However, the one-shot context did not lead to complete adjacent-caller and end-to-end deadline verification; that is better explained by scope/acceptance breadth than by wholesale context loss. |

What this does **not** prove: it does not establish an intrinsic Luna model defect,
nor distinguish a platform timeout from a stalled verification command or failure to
synthesize the final handoff. Internal runtime telemetry was not available, so the
ranking is based on observable repository artifacts and supervisor lifecycle events.

Operational response for this PRD-v2 session: use the explicit escalation ladder
`Luna/xhigh -> Luna/max -> Terra/high`. On escalation, preserve the existing patch,
give the next worker the failed attempt's exact owned files and verification state,
and split future Luna packets so implementation and expensive acceptance evidence do
not share an unnecessarily large one-shot boundary.

## 11. Evidence ledger

| Date | Evidence | Meaning |
|---|---|---|
| 2026-08-10 | `7e42784..2a29e29` | V2-M1 contracts, fail-closed gateway, session buffer (73 tests pass) |
| 2026-08-10 | V2-M2 slices M2.0–M2.2 | Explicit profile policy, PostgreSQL `002_chat_profiles.sql`, `PostgresChatProfileRepository` (15 PostgreSQL tests pass) |
| 2026-08-10 | V2-M3 chat summary slice | `ChatSummaryEpisode` (500 char), `003_chat_summary_episodes.sql`, `PostgresChatSummaryEpisodeRepository` (104 AI chat tests + 18 Postgres tests pass) |
| 2026-08-10 | Tech Stack Evaluation | `qdrant-postgresql-techstack-evaluation.md` (PostgreSQL durable, Qdrant index; 20 Qdrant tests pass) |
| 2026-08-10 | V2-M4A acceptance review | 12 controller/API tests pass; Ruff clean; local-MVP lifecycle limits recorded as V2-M6 launch blocker |
| 2026-08-10 | PostgreSQL optional-profile degradation | `OperationalError` translates to typed Gateway degradation while programming errors remain visible; 29 Gateway tests pass |
| 2026-08-10 | V2-M5 retrieval contract slice | Enabled episodic/semantic reads require query + independent server bounds; 71 domain tests and combined 134 passed, 1 PostgreSQL skip |
| 2026-08-10 | Luna `xhigh` -> `max` Qdrant delegation incident | Both attempts left substantive implementation but no final report. A narrow recovery pass changed import ordering and reached 25 focused tests; fresh full-stack review then found caller/status/outage/deadline gaps and returned `fix-first`. Non-return remains the top task-failure cause; oversized acceptance scope ranks second. |
| 2026-08-10 | V2-M5 semantic runtime fix-first closure | App composition uses `SemanticChatMemoryAdapter` only through `MemoryGateway`; Qdrant requires exact `ready`, enforces an end-to-end deadline, and exposes outages as degradation. Central combined focus: 96 passed; Ruff clean; mypy clean in 4 source files. |
| 2026-08-10 | V2-M5 labeled generation context | Immutable labeled sections plus explicit conflict precedence landed; central focused test 2 passed, Ruff clean. Post-fix fresh Sol review could not be spawned because the host retained completed/interrupted child-thread slots; primary diff/test/type review is recorded without mislabeling it as fresh review. |
| 2026-08-10 | V2-M6 metadata-only memory observability | Gateway emits bounded read/write/delete/degradation/denial metadata through a non-interfering sink; no content/identity/query/URL/exception fields. Central focus 36 passed; Ruff clean; mypy clean in 3 source files. |
| 2026-08-10 | V2-M6 paired evaluation launch gate | Metadata-only paired memory-disabled/enabled scores use caller-required quality thresholds and non-weakenable zero-tolerance safety gates, including rejected/unvalidated/cross-tenant/raw-email/expired retrieval. Central focus 4 passed; Ruff clean. |
| 2026-08-10 | V2-M6 exact-scope bulk deletion | Working session, exact profile, and all current user chat summaries delete idempotently; semantic company RAG is excluded. Central unit focus 36 passed; PostgreSQL exact/foreign/retry node skipped because no reachable server was configured. |
| 2026-08-10 | V2-M6 retention and purge coordination | Optional profile/episode retention seconds have no invented default; explicit UTC purge coordinator reuses repository purge operations and exposes no scheduler API. Central config/retention focus 10 passed; Ruff clean. |
| 2026-08-10 | Deferred TaskEpisode persistence/lifecycle dispatch | Terra/high implementation did not start: child returned an account usage-limit error with retry time `2026-08-16 04:26`. No files from that requested slice were accepted or claimed complete. |
| 2026-08-11 | PRD-v2 foundation checkpoint `fc3c0b7` | 44 code/test/config paths committed. Focused scope: 206 passed, 1 skipped. Full suite outside the managed sandbox: 624 passed, 28 skipped, 4 xfailed. Ruff clean; mypy clean across 79 source files. The first sandboxed full-suite attempts failed only because Windows ACLs made pytest/tempfile child directories unwritable. |
| 2026-08-11 | TaskEpisode commitment review and execution stop | Fresh Sol verdict: proceed with `TaskEpisode.record_id == tasks.task_key`, composite ownership FK `(record_id, tenant_id, user_id)`, cross-session same-user retrieval, and origin-session mutation predicates. Subsequent configured Terra/high roles produced zero file delta and no observable error; migration `004` and TaskEpisode repository remain absent. |

## 12. End-of-session handoff template

```text
ACTIVE MILESTONE / SLICE: V2-M3 TaskEpisode persistence/lifecycle is the first incomplete slice; executable @Email remains LAST
STATUS AND PERCENT: use dashboard sections 5-6; no percentage changed during the 2026-08-11 checkpoint
COMMITS: fc3c0b7 on feature/v2-m3-chat-summary; dev 15ea2b9; merge-base 148a779; no merge/rebase/push
TESTS / LINT / TYPES: focused 206 passed, 1 skipped; full 624 passed, 28 skipped, 4 xfailed; Ruff pass; mypy 79 files pass
AC EVIDENCE ADDED: checkpoint and TaskEpisode commitment decision recorded in section 11; no TaskEpisode implementation accepted
EXACT NEXT ACTION: activate an available implementation lane, then add tests-first migration 004 and the body-free PostgreSQL TaskEpisode store using the section 11 Sol decision
```
