# Session Bootstrap & PRD-v2 Delivery Dashboard

> Start here in a new coding session. This file is the compact operational
> view for planning, implementation, and project tracking. Read `AGENTS.md`
> first, then this file. Open a larger PRD/architecture document only when the
> routing table below says the current task needs it or a conflict appears.

| Field | Current value |
|---|---|
| Updated | 2026-08-10 (Asia/Bangkok) |
| Branch / implementation baseline | `feature/v2-m3-chat-summary` at base `eb474b8` (reconciling with `dev` at `148a779`) |
| Product frontier | PRD-v2 Multi-Turn AI Chat Memory + executable `@Email` tool |
| Active milestone | **V2-M3 — Chat Summary Episodic Persistence (ACTIVE / VERIFIED)**; V2-M4A Chat Controller is NEXT |
| PRD-v2 progress | **2.75 / 6 milestones; 3 / 20 acceptance criteria complete** |
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
| V2-M4 Chat Controller + SSE + tool | **NEXT** | 0 | Start V2-M4A: session/message APIs, Chat Controller loop, typed SSE; `@Email` wrapper deferred | V2-M1–M3 | — |
| V2-M5 Selective episodic + RAG retrieval | BLOCKED | 0 | Intent-triggered bounded retrieval; eligibility filters; labeled context; conflict precedence | V2-M4 | — |
| V2-M6 Evaluation + governance | BLOCKED | 0 | Memory on/off evaluation; retention/purge/deletion audit; safety alerts; launch thresholds | V2-M5 | — |

**Priority override (user, 2026-08-10): executable `@Email` chat tool is built LAST.**

## 6. PRD-v2 acceptance dashboard

| ID | Acceptance statement | Status | Evidence |
|---|---|---:|---|
| AC-01 | Chat Controller accesses all four memories only through Gateway | TODO | Gateway boundary landed (`2a29e29`); Chat Controller proof pending V2-M4A |
| AC-02 | Every operation carries tenant/user/session/`feature: ai_chat`/type | DONE | `310d2fd`, `2a29e29`; domain namespace + gateway tests |
| AC-03 | Bounded working buffer preserves turns and expires by policy | DONE | `7e42784`, `2a29e29`; `tests/unit/features/ai_chat/test_session_buffer.py` |
| AC-04 | Explicit persona/preferences persist and load in later sessions | DONE | Profile policy/gateway/PostgreSQL repo green (`test_chat_profile_repository.py` 15 passed) |
| AC-05 | User can invoke `@Email` inside a chat thread | TODO | Deferred to last (§5 priority override) |
| AC-06 | `@Email` stays stateless and owns no durable memory | TODO | — |
| AC-07 | Assistant/tool/card events stream to the active session | TODO | Pending V2-M4A SSE engine |
| AC-08 | Rendered tool plan writes one idempotent system-generated episode | TODO | Chat summary episode half proven in V2-M3; tool plan episode deferred |
| AC-09 | New tool episode is retrieval-ineligible | TODO | Policy and schema `CHECK (retrieval_eligible = FALSE)` proven in V2-M3 |
| AC-10 | Inline approval/completion makes episode eligible | TODO | — |
| AC-11 | Inline rejection keeps episode ineligible | TODO | — |
| AC-12 | Retrieval returns approved/completed episodes only | TODO | — |
| AC-13 | Model cannot retrieve unvalidated episodes directly | TODO | — |
| AC-14 | Episodic and semantic retrieval are selective and bounded | TODO | — |
| AC-15 | Current company evidence outranks prior episode guidance | TODO | — |
| AC-16 | Raw email absent durable memory, chat, telemetry, browser storage | TODO | Checked in V2-M2 & V2-M3 repository schema tests |
| AC-17 | Deletion prevents later retrieval | PARTIAL | Profile & chat summary deletion proven at gateway and PostgreSQL storage levels |
| AC-18 | Production telemetry is metadata-only | TODO | — |
| AC-19 | Memory outage degrades chat without corrupting `@Email` | PARTIAL | Gateway profile fallback proven (`test_profile_outage_leaves_working_memory_and_tool_availability_intact`) |
| AC-20 | No scheduler, recurring scan, or autonomous email action added | TODO | — |

## 7. Immediate next tasks plan — V2-M3 Reconciliation & V2-M4A

Goal: Checkpoint/reconcile V2-M3 chat summary slice with `dev`, then implement the framework-free Chat Controller + SSE streaming engine (V2-M4A) while keeping `@Email` tool slices deferred.

### Task 1: V2-M3 Branch Reconciliation & Checkpoint
1. **Diff Review:** Confirm uncommitted V2-M3 chat summary slice on `feature/v2-m3-chat-summary` is clean and passing.
2. **Reconciliation:** Reconcile base `eb474b8` with 9 Qdrant commits on `dev` (`148a779`) using `git merge-tree`.
3. **Verification:** Run `pytest tests/unit/features/ai_chat tests/integration/persistence`, `ruff check .`, and `mypy src`.

### Task 2: V2-M4A Chat Controller & SSE Engine
1. **Controller Core:** Build `src/cowork_agent/features/ai_chat/controller.py` to validate scope, load working memory and compact declarative profile via `MemoryGateway`, and process chat turns.
2. **SSE Streaming Event Generator:** Emit typed stream events (`ChatMessageStreamEvent`, `ChatEventType`) for message start, content delta, and completion.
3. **API Adapter:** Add route handlers in `src/cowork_agent/api/` for session lifecycle and chat SSE streaming.
4. **Exclusions:** No `@Email` execution, no Action Plan cards, no inline approval controls in this slice.

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
1. **Branch Reconciliation:** `feature/v2-m3-chat-summary` base `eb474b8` needs reconciliation with 9 Qdrant commits on `dev` (`148a779`).
2. **Acceptance Verification:** Final full-suite verification pass after branch integration.

## 11. Evidence ledger

| Date | Evidence | Meaning |
|---|---|---|
| 2026-08-10 | `7e42784..2a29e29` | V2-M1 contracts, fail-closed gateway, session buffer (73 tests pass) |
| 2026-08-10 | V2-M2 slices M2.0–M2.2 | Explicit profile policy, PostgreSQL `002_chat_profiles.sql`, `PostgresChatProfileRepository` (15 PostgreSQL tests pass) |
| 2026-08-10 | V2-M3 chat summary slice | `ChatSummaryEpisode` (500 char), `003_chat_summary_episodes.sql`, `PostgresChatSummaryEpisodeRepository` (104 AI chat tests + 18 Postgres tests pass) |
| 2026-08-10 | Tech Stack Evaluation | `qdrant-postgresql-techstack-evaluation.md` (PostgreSQL durable, Qdrant index; 20 Qdrant tests pass) |

## 12. End-of-session handoff template

```text
ACTIVE MILESTONE / SLICE: V2-M3 complete and verified; V2-M4A Chat Controller + SSE is NEXT
STATUS AND PERCENT: V2-M1 DONE 100%; V2-M2 DONE 100%; V2-M3 ACTIVE 75% (chat summary done, @Email deferred)
COMMITS: uncommitted V2-M3 diff on feature/v2-m3-chat-summary (base eb474b8, dev at 148a779)
TESTS / LINT / TYPES: 104 AI Chat unit tests pass; 18 PostgreSQL repo tests pass against cowork-pg; 20 Qdrant tests pass; Ruff pass; mypy src pass
AC EVIDENCE ADDED: AC-01..04 DONE; AC-17/19 PARTIAL; AC-08/09 chat summary half proven
EXACT NEXT ACTION: Obtain approval to checkpoint/reconcile V2-M3 diff with dev (148a779), then implement V2-M4A Chat Controller & typed SSE engine
```

