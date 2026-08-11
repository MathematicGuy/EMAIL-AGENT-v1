# Session Bootstrap & PRD-v2 Delivery Dashboard

> Start here in a new coding session. This file is the compact operational
> view for planning, implementation, and project tracking. Read `AGENTS.md`
> first, then this file. Open a larger PRD/architecture document only when the
> routing table below says the current task needs it or a conflict appears.

| Field | Current value |
|---|---|
| Updated | 2026-08-11 (Asia/Bangkok) |
| Branch / implementation baseline | `cedd563` on `feature/v2-m3-chat-summary`; live `dev` is `91dff59`, merge-base remains `148a779`; M3.1-M3.3 are committed and M3.4a is uncommitted; no merge, rebase, reset, or push performed by this orchestration session |
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
- Branch `feature/v2-m3-chat-summary` is at `cedd563`. M3.1-M3.3 are committed; M3.4a PostgreSQL TaskEpisode durability is present as an uncommitted migration/repository/test slice. Unrelated user-owned documentation changes remain outside this acceptance scope.
- `dev` is `91dff59`; merge-base is `148a779` with 6 feature commits and 5 dev commits unique. Reconciliation is a later explicit task.
- Latest parent verification after the M3.4a citation-allowlist correction: 30 live PostgreSQL persistence tests passed with zero skips; 198 domain + AI Chat tests passed; full suite 696 passed, 25 skipped, 4 xfailed using an explicit writable external pytest temp directory; Ruff, `mypy src` across 79 files, and `git diff --check` were clean.
- Docker Desktop is currently unavailable. The PostgreSQL results above are retained evidence from the earlier live `cowork-pg` run, not a new rerun.

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
| V2-M3 Chat-native episodes | **ACTIVE** | 90 | M3.1-M3.3 accepted and committed; M3.4a PostgreSQL durability implemented and parent-verified; fresh final review and later Gateway lifecycle wiring remain | V2-M1, V2-M2 | M3.4a: 30 live PostgreSQL persistence tests, 198 domain + AI Chat tests, full 696 passed/25 skipped/4 xfailed; Ruff, mypy, and diff check clean; fresh Sol verdict pending |
| V2-M4 Chat Controller + SSE | **VERIFY** | 35 | Session/message APIs, Chat Controller loop, and typed assistant SSE landed | V2-M1–M3 | 12 focused controller/API tests; principal binding, cancellation, replay, typed provider failure proven |
| V2-M5 Selective episodic + RAG retrieval | **ACTIVE** | 70 | Query-scoped contracts, deterministic intent policy, Gateway filtering/degradation, ready-only Qdrant/semantic runtime, and labeled precedence assembler landed; eligible episodic runtime depends on M3.4b Gateway wiring | V2-M4A | Central post-fix focus 96 passed; generation-context 2 passed; Ruff and narrowed mypy clean |
| V2-M6 Evaluation + governance | **ACTIVE** | 55 | Metadata-only Gateway events, paired launch gate, exact-scope retryable bulk deletion, optional durable retention settings, and explicit purge coordinator landed; production sink/alerts, backup/restore, and end-to-end runtime deletion proof remain | V2-M5 core contracts | Central governance focuses green; M3.4a live PostgreSQL deletion/purge paths pass, while Gateway runtime wiring remains |

## 6. PRD-v2 acceptance dashboard

| ID | Acceptance statement | Status | Evidence |
|---|---|---:|---|
| AC-01 | Chat Controller accesses all four memories only through Gateway | PARTIAL | V2-M4A Controller reads working/profile context only through Gateway; episodic/semantic wiring pending V2-M5 |
| AC-02 | Every operation carries tenant/user/session/`feature: ai_chat`/type | DONE | `310d2fd`, `2a29e29`; domain namespace + gateway tests |
| AC-03 | Bounded working buffer preserves turns and expires by policy | DONE | `7e42784`, `2a29e29`; `tests/unit/features/ai_chat/test_session_buffer.py` |
| AC-04 | Explicit persona/preferences persist and load in later sessions | DONE | Profile policy/gateway/PostgreSQL repo green (`test_chat_profile_repository.py` 15 passed) |
| AC-05 | Assistant events stream to the active session | PARTIAL | Typed assistant delta/error/completed SSE proven by controller/API tests |
| AC-06 | Only explicit user task requests create idempotent TaskEpisodes | PARTIAL | ADR-004 explicit-request contract plus scoped idempotent PostgreSQL writes are proven; Chat Controller producer wiring remains |
| AC-07 | New TaskEpisodes are system-generated and retrieval-ineligible | PARTIAL | Domain contract and PostgreSQL storage-generated eligibility are proven; producer wiring remains |
| AC-08 | Inline approval/completion makes episode eligible | PARTIAL | Session-scoped PostgreSQL transitions derive eligibility from approved/completed status; inline controller wiring remains |
| AC-09 | Inline rejection keeps episode ineligible | PARTIAL | PostgreSQL lifecycle transitions keep non-approved/non-completed states ineligible; inline controller wiring remains |
| AC-10 | Retrieval returns approved/completed episodes only | PARTIAL | PostgreSQL retrieval filters to eligible validated lifecycle states with expiry and scope enforcement; Gateway runtime wiring remains |
| AC-11 | Model cannot retrieve unvalidated episodes directly | PARTIAL | Gateway filtering and PostgreSQL eligibility/status predicates are proven; live reply-provider consumption remains |
| AC-12 | Episodic and semantic retrieval are selective and bounded | PARTIAL | Intent policy, Gateway bounds, ready-only semantic runtime, and bounded PostgreSQL FTS retrieval with timeout/max-items/min-score are proven; live combined consumption remains |
| AC-13 | Current company evidence outranks prior episode guidance | PARTIAL | Typed assembler precedence and labeled advisory episodes proven; live reply-provider consumption pending |
| AC-14 | TaskEpisodes exclude raw source content and tool payloads | PARTIAL | Domain guards plus PostgreSQL constraints enforce bounded body-free fields and the exact citation-key allowlist; fresh final review remains |
| AC-15 | Exact-scope deletion prevents later retrieval without deleting semantic RAG | PARTIAL | Gateway semantic exclusion plus scoped PostgreSQL single/bulk deletion and expiry purge are proven; lifecycle wiring remains |
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
4. **M3.4a PostgreSQL durability — VERIFY:** migration/down migration, repository,
   lifecycle, bounded cross-session retrieval, deletion/purge, privacy constraints,
   and live PostgreSQL tests are implemented and parent-verified. Two fresh Sol
   reviews returned `fix-first`; query/min-score semantics and exact citation keys
   were corrected. Because the last correction invalidated the prior verdict, one
   new final review is still required.
5. **M3.4b Gateway lifecycle wiring — NEXT:** connect write/transition/delete and
   eligible retrieval through `MemoryGateway`. No PRD-v1 task FK or Qdrant
   TaskEpisode store.

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
1. **Branch reconciliation:** feature HEAD is `cedd563`, live `dev` is `91dff59`, and their merge-base remains `148a779` (`6` feature commits and `5` dev commits are unique). This session did not merge, rebase, reset, or push. Reconcile explicitly in a later session.
2. **Acceptance verification:** M3.4a is centrally green (30 live PostgreSQL persistence tests; 198 domain + AI Chat tests; full suite 696 passed, 25 skipped, 4 xfailed; Ruff, mypy, and diff check clean), but a fresh final Sol verdict is required after the citation-allowlist correction. Final post-reconciliation verification also remains required.
3. **Durability boundary:** M3.1-M3.3 are accepted and committed. M3.4a PostgreSQL
   migration/repository is implemented but uncommitted and not finally accepted;
   M3.4b Gateway lifecycle wiring remains a separate next increment.
4. **Local PostgreSQL availability:** Docker Desktop is currently unavailable. Do
   not convert this into skipped acceptance evidence; restart Docker and confirm
   `cowork-pg` before the next PostgreSQL verification run.

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
| 2026-08-11 | TaskEpisode commitment review and execution stop | **Superseded by ADR-004 after product retired in-chat `@Email`.** The earlier Email-task FK verdict no longer applies. At that checkpoint, migration `004` and the TaskEpisode repository were still absent; the later M3.4a row records their implementation. |
| 2026-08-11 | ADR-004 generic TaskEpisode decision | User-confirmed chat-native task contract: explicit request only, Chat Controller producer, initial system-generated/ineligible state, user lifecycle controls, chat-scoped opaque idempotency, no Email task FK or Gmail/run/tool fields. M3.2 domain migration dispatched tests first. |
| 2026-08-11 | M3.1-M3.3 generic TaskEpisode contract acceptance | Public chat contract `2.0.0`; no request/tool SSE surface; explicit-request-only provenance; fixed compact payload/citation bounds; direct and deserialized inputs are deeply immutable, typed, and raw/tool-payload guarded. FastAPI uses a strict tool-free transport model; retired `tool_choices` returns HTTP 422 before reply dispatch. TDD tool retirement RED: 25 failed/141 passed, then GREEN 166 passed; bounds RED failed on missing public limits; HTTP RED returned 200 before the boundary fix. Final focused parent gate: 206 passed. Full suite outside the managed sandbox: 663 passed, 28 skipped, 4 xfailed; Ruff clean; mypy clean across 79 source files. Accepted in `cedd563`. |
| 2026-08-11 | M3.4a PostgreSQL TaskEpisode durability — final review pending | Added reversible migration `004`, scoped idempotent repository writes, immutable identity guards, storage-derived lifecycle eligibility, cross-session tenant/user retrieval, expiry and server bounds, PostgreSQL FTS relevance/min-score, exact citation keys, deletion, and purge. Live PostgreSQL persistence: 30 passed, zero skips; domain + AI Chat: 198 passed; full suite with writable external temp: 696 passed, 25 skipped, 4 xfailed; Ruff, mypy across 79 files, and diff check clean. First final review found missing query/min-score behavior; second found permissive citation keys. Both were corrected and reverified, invalidating the previous verdict; a new fresh final review is required before acceptance. |

## 12. End-of-session handoff template

```text
ACTIVE MILESTONE / SLICE: V2-M3 TaskEpisode PostgreSQL durability (M3.4a final review)
STATUS AND PERCENT: ACTIVE, 90%; implementation and parent gates green, fresh final Sol verdict pending
COMMITS: cedd563 on feature/v2-m3-chat-summary; dev 91dff59; merge-base 148a779; no merge/rebase/push in this orchestration session
TESTS / LINT / TYPES: live PostgreSQL 30 passed, zero skips; domain + AI Chat 198 passed; full 696 passed, 25 skipped, 4 xfailed; Ruff pass; mypy 79 files pass; diff check clean
AC EVIDENCE ADDED: durable scoped idempotency, immutable identity, storage-derived eligibility, bounded FTS retrieval, exact citation keys, deletion, and purge
EXACT NEXT ACTION: start Docker/cowork-pg if needed, capture M3.4a status and hashes, obtain a fresh behaviorally read-only Sol final review, compare post-review hashes, then either correct findings or accept M3.4a and dispatch M3.4b Gateway lifecycle wiring
```
