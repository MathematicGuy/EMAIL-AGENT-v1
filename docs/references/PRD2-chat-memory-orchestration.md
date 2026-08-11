# Session Bootstrap & PRD-v2 Delivery Dashboard

> Start here in a new coding session. This file is the compact operational
> view for planning, implementation, and project tracking. Read `AGENTS.md`
> first, then this file. Open a larger PRD/architecture document only when the
> routing table below says the current task needs it or a conflict appears.

| Field | Current value |
|---|---|
| Updated | 2026-08-11 (Asia/Bangkok) |
| Branch / implementation baseline | Orchestration checkpoint `dc2fb08` on `feature/v2-m3-chat-summary`; live `dev` is `15ea2b9`, merge-base remains `148a779`; M3.1-M3.3 are uncommitted; no merge, rebase, reset, or push performed |
| Product frontier | PRD-v2 Multi-Turn AI Chat Memory |
| Active milestone | **V2-M3 — Generic TaskEpisode contract migration (ACTIVE)**; V2-M5 semantic runtime remains verified but generic episodic runtime is dependency-blocked |
| PRD-v2 progress | **2.75 / 6 milestones; 3 / 18 acceptance criteria complete; 4 criteria partial** |
| Tech stack authority | PostgreSQL (authoritative durable store); Qdrant (rebuildable enterprise RAG index) |

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
  types through one Memory Gateway.

STORAGE & MEMORY ARCHITECTURE:
  1. Working: bounded active-session turns (In-process TTL).
  2. Declarative: explicit persona/preferences only (PostgreSQL `chat_profiles`).
  3. Episodic: chat summaries + explicitly requested, validated chat-native TaskEpisodes (PostgreSQL).
  4. Semantic: enterprise RAG (Qdrant Cloud/in-repo index via `SemanticMemoryPort`).

SAFETY MODEL:
  New chat-native TaskEpisodes and summaries start system_generated + retrieval_eligible=false.
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
- Branch `feature/v2-m3-chat-summary` at `dc2fb08` has uncommitted M3.1-M3.3 authority, generic TaskEpisode contract, consumer, and strict HTTP-boundary changes. The unrelated concurrent `README.md` edit is excluded from this acceptance scope.
- `dev` is `15ea2b9`; merge-base is `148a779` with 3 feature commits and 2 dev commits unique. Reconciliation is a later explicit task.
- Verification status: 206 focused tests passed; full suite 663 passed, 28 skipped, 4 xfailed outside the managed sandbox; Ruff clean; `mypy src` clean across 79 files.

## 4. Authority and selective document routing

| Need | Read only this |
|---|---|
| Always-needed rules | `AGENTS.md` |
| Focused implementation restart | `docs/references/handoff-prd-v2-implementation.md` |
| Tech stack & storage authority | `docs/references/qdrant-postgresql-techstack-evaluation.md` |
| Product behavior / acceptance | `docs/PRD-v2-Memory-Extension.md` relevant FR + §16–17 |
| Component ownership, APIs, SSE | `docs/architectures/TARGET-ARCHITECTURE.md` accepted ADR-004 target in §20 |
| Exact DTO contracts / sequencing | `docs/master-comparison.md` active §6 contracts and accepted ADR-004 V2 milestone group |

Conflict precedence: `AGENTS.md` > PRD-v2 > Target Architecture > Master Comparison > Dashboard/Trackers.

## 5. PRD-v2 project-management dashboard

Status legend: `NEXT` = ready now, `BLOCKED` = dependency not met, `ACTIVE` = implementation in progress, `VERIFY` = code done/evidence pending, `DONE` = exit gate evidenced.

| Milestone | Status | % | Deliverable / exit gate | Depends on | Evidence |
|---|---:|---:|---|---|---|
| V2-M1 Gateway + session working memory | **DONE** | 100 | Chat/memory contracts; fail-closed namespace; bounded session TTL; no gateway bypass | PRD-v1 baseline | `7e42784..2a29e29`; 73 focused tests; deterministic suite exit 0 |
| V2-M2 Declarative chat profile | **DONE** | 100 | Explicit-only profile CRUD; per-turn compact load; fallback; deletion/retention | V2-M1 | Policy/port/gateway/migration/repo landed; 496 passed; PostgreSQL gate 15 passed against `cowork-pg` |
| V2-M3 Chat-native episodes | **ACTIVE** | 75 | Chat summaries verified; ADR-004 authority plus bounded generic TaskEpisode contract and consumers accepted; PostgreSQL durability is next | V2-M1, V2-M2 | M3.1-M3.3: focused 206 passed; full 663 passed, 28 skipped, 4 xfailed; Ruff and mypy clean; generic migration/repository not yet landed |
| V2-M4 Chat Controller + SSE | **VERIFY** | 35 | Session/message APIs, Chat Controller loop, and typed assistant SSE landed | V2-M1–M3 | 12 focused controller/API tests; principal binding, cancellation, replay, typed provider failure proven |
| V2-M5 Selective episodic + RAG retrieval | **ACTIVE** | 70 | Query-scoped contracts, deterministic intent policy, Gateway filtering/degradation, ready-only Qdrant/semantic runtime, and labeled precedence assembler landed; eligible episodic runtime depends on generic TaskEpisode persistence | V2-M4A | Central post-fix focus 96 passed; generation-context 2 passed; Ruff and narrowed mypy clean |
| V2-M6 Evaluation + governance | **ACTIVE** | 55 | Metadata-only Gateway events, paired launch gate, exact-scope retryable bulk deletion, optional durable retention settings, and explicit purge coordinator landed; production sink/alerts, backup/restore, and runtime DB deletion proof remain | V2-M5 core contracts | Central governance focuses green; PostgreSQL deletion test present but server-gated skip |

## 6. PRD-v2 acceptance dashboard

| ID | Acceptance statement | Status | Evidence |
|---|---|---:|---|
| AC-01 | Chat Controller accesses all four memories only through Gateway | PARTIAL | V2-M4A Controller reads working/profile context only through Gateway; episodic/semantic wiring pending V2-M5 |
| AC-02 | Every operation carries tenant/user/session/`feature: ai_chat`/type | DONE | `310d2fd`, `2a29e29`; domain namespace + gateway tests |
| AC-03 | Bounded working buffer preserves turns and expires by policy | DONE | `7e42784`, `2a29e29`; `tests/unit/features/ai_chat/test_session_buffer.py` |
| AC-04 | Explicit persona/preferences persist and load in later sessions | DONE | Profile policy/gateway/PostgreSQL repo green (`test_chat_profile_repository.py` 15 passed) |
| AC-05 | Assistant events stream to the active session | PARTIAL | Typed assistant delta/error/completed SSE proven by controller/API tests |
| AC-06 | Only explicit user task requests create idempotent TaskEpisodes | TODO | ADR-004/PRD-v2 contract approved; producer implementation pending |
| AC-07 | New TaskEpisodes are system-generated and retrieval-ineligible | PARTIAL | Generic contract enforces lifecycle/eligibility consistency; durable storage derivation remains M3.4 |
| AC-08 | Inline approval/completion makes episode eligible | TODO | — |
| AC-09 | Inline rejection keeps episode ineligible | TODO | — |
| AC-10 | Retrieval returns approved/completed episodes only | TODO | — |
| AC-11 | Model cannot retrieve unvalidated episodes directly | TODO | Gateway filtering exists; generic durable runtime pending |
| AC-12 | Episodic and semantic retrieval are selective and bounded | PARTIAL | Deterministic intent triggers, server-owned bounds, Gateway filtering, and ready-only Qdrant enforcement proven; generic episodic runtime pending |
| AC-13 | Current company evidence outranks prior episode guidance | PARTIAL | Typed assembler precedence and labeled advisory episodes proven; live reply-provider consumption pending |
| AC-14 | TaskEpisodes exclude raw source content and tool payloads | PARTIAL | Direct and deserialized domain inputs enforce bounded typed payloads plus recursive raw/tool-shaped-key rejection; PostgreSQL proof remains M3.4 |
| AC-15 | Exact-scope deletion prevents later retrieval without deleting semantic RAG | PARTIAL | Gateway bulk deletion and semantic exclusion unit-proven; generic TaskEpisode SQL path pending |
| AC-16 | Production telemetry is metadata-only | PARTIAL | Typed Gateway events exclude content, identity, query, URLs, citations, and exception text; production sink/alerts remain |
| AC-17 | Memory outage degrades chat and preserves standalone Email Agent | PARTIAL | Gateway degradation proven; standalone Email Agent remains separate by contract |
| AC-18 | No in-chat tool, scheduler, recurring processing, or autonomous email action | PARTIAL | Public contract/SSE expose no tool surface; FastAPI rejects retired `tool_choices` with 422 before reply dispatch; final product-wide audit remains |

## 7. Active implementation plan — full PRD-v2 continuation

Goal: complete the remaining PRD-v2 acceptance criteria in dependency order.
Each task is an acceptance-sized slice: tests first, focused verification, supervisor
diff review, then dashboard evidence before the next dependency starts.

### Task 1: V2-M3 generic TaskEpisode contract — CONTRACT ACCEPTED
1. **M3.1 authority — DONE:** ADR-004 and PRD-v2 v2.2 approved; Target Architecture,
   master comparison, roadmap, and this ledger now describe chat-native tasks.
2. **M3.2 domain contract — DONE:** tests-first generic TaskEpisode removes
   Email/run/tool ownership, requires explicit-request provenance, preserves the
   eligibility state machine, and enforces user-approved compact bounds on direct
   construction and deserialization. `CHAT_CONTRACTS_VERSION` is `2.0.0`.
3. **M3.3 consumers — DONE:** AI Chat fixtures and public annotations migrated;
   request tool choices and tool-only SSE variants are retired. AI Chat is chat +
   memory only; retired `tool_choices` wire input fails strict deserialization.
4. **M3.4 durability — NEXT:** split PostgreSQL migration/repository from Gateway
   write/transition/delete wiring. No PRD-v1 task FK or Qdrant TaskEpisode store.

### Task 2: V2-M4A Chat Controller & SSE Engine
1. **Controller Core:** Build `src/cowork_agent/features/ai_chat/controller.py` to validate scope, load working memory and compact declarative profile via `MemoryGateway`, and process chat turns.
2. **SSE Streaming Event Generator:** Emit typed stream events (`ChatMessageStreamEvent`, `ChatEventType`) for message start, content delta, and completion.
3. **API Adapter:** Add route handlers in `src/cowork_agent/api/` for session lifecycle and chat SSE streaming.

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
- **New episodes default to `system_generated` and `retrieval_eligible=false`**.
- **ADR-004:** generic TaskEpisodes are created only after an explicit user task
  request; no Email task FK, Gmail/run/tool fields, or automatic extraction.

### Active blockers
1. **Branch reconciliation:** feature HEAD is `dc2fb08`, live `dev` is `15ea2b9`, and their merge-base remains `148a779` (`3` feature commits and `2` dev commits are unique). The new `dev` commit changes GUI/performance files and removes tracked pytest artifacts; this session did not merge, rebase, reset, or push. Reconcile explicitly in a later session.
2. **Acceptance Verification:** the current pre-reconciliation tree is centrally green (206 focused; full suite 663 passed, 28 skipped, 4 xfailed; Ruff and mypy clean), but final post-reconciliation verification remains required.
3. **Durability boundary:** M3.1-M3.3 are accepted but uncommitted. M3.4 PostgreSQL
   migration/repository and later Gateway lifecycle wiring remain separate,
   undispatched increments. No persistence evidence is claimed by contract tests.

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
| 2026-08-11 | TaskEpisode commitment review and execution stop | **Superseded by ADR-004 after product retired in-chat `@Email`.** The earlier Email-task FK verdict no longer applies; migration `004` and TaskEpisode repository remain absent. |
| 2026-08-11 | ADR-004 generic TaskEpisode decision | User-confirmed chat-native task contract: explicit request only, Chat Controller producer, initial system-generated/ineligible state, user lifecycle controls, chat-scoped opaque idempotency, no Email task FK or Gmail/run/tool fields. M3.2 domain migration dispatched tests first. |
| 2026-08-11 | M3.1-M3.3 generic TaskEpisode contract acceptance | Public chat contract `2.0.0`; no request/tool SSE surface; explicit-request-only provenance; fixed compact payload/citation bounds; direct and deserialized inputs are deeply immutable, typed, and raw/tool-payload guarded. FastAPI uses a strict tool-free transport model; retired `tool_choices` returns HTTP 422 before reply dispatch. TDD tool retirement RED: 25 failed/141 passed, then GREEN 166 passed; bounds RED failed on missing public limits; HTTP RED returned 200 before the boundary fix. Final focused parent gate: 206 passed. Full suite outside the managed sandbox: 663 passed, 28 skipped, 4 xfailed; Ruff clean; mypy clean across 79 source files. M3.4 persistence remains unimplemented. |

## 12. End-of-session handoff template

```text
ACTIVE MILESTONE / SLICE: V2-M3 TaskEpisode PostgreSQL durability (M3.4a next)
STATUS AND PERCENT: use dashboard sections 5-6; no percentage changed during the 2026-08-11 checkpoint
COMMITS: dc2fb08 on feature/v2-m3-chat-summary; dev 15ea2b9; merge-base 148a779; no merge/rebase/push
TESTS / LINT / TYPES: focused 206 passed; full 663 passed, 28 skipped, 4 xfailed; Ruff pass; mypy 79 files pass
AC EVIDENCE ADDED: M3.1-M3.3 contract accepted; AI Chat has no public tool surface; bounded generic TaskEpisode contract is green
EXACT NEXT ACTION: fresh final review, then dispatch M3.4a migration/down + PostgreSQL repository tests as a separate slice
```
