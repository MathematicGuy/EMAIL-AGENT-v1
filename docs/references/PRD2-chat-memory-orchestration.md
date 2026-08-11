# PRD-v2 Delivery Dashboard

> Start here in a new coding session. This is the operational current-state
> view: acceptance status, next work, blockers, and latest proof. Read
> `AGENTS.md` first, then this file and
> `docs/references/improve-orchestration-efficiency.md`. Git history holds
> superseded implementation detail; do not recreate an append-only ledger here.

**Pruning rule:** at each verified milestone transition and before a session
handoff, replace stale dashboard facts rather than append history. Retain only
the current acceptance state, ordered next work, active blockers, and the
newest proof needed for the next gate. Move durable methods to the playbook and
historical detail to Git or a task handoff. Do not change `Updated` for
formatting-only edits or unverified claims.

| Field | Current value |
|---|---|
| Updated | 2026-08-11 (Asia/Bangkok) |
| Branch / implementation baseline | `a33ce71` on `feature/v2-m3-chat-summary`; live `dev` is `91dff59`, merge-base remains `148a779`; M3.1-M3.4a are committed; no merge, rebase, reset, or push without explicit authorization |
| Product frontier | PRD-v2 Multi-Turn AI Chat Memory |
| Active milestone | **V2-M3 — Generic TaskEpisode contract migration (ACTIVE)**; V2-M5 semantic runtime remains verified but generic episodic runtime is dependency-blocked |
| PRD-v2 progress | M3.4a final review is the active gate; use §5–§7 for live status |
| Tech stack authority | PostgreSQL (authoritative durable store); Qdrant (rebuildable enterprise RAG index) |

## 1. New-session launch sequence

Do these in order; do not reread the entire documentation set first.

1. Read `AGENTS.md` (project rules and four non-negotiable invariants).
2. Read this dashboard and `docs/references/improve-orchestration-efficiency.md`.
3. Run `git status --short --branch` and inspect uncommitted worktree changes.
4. For V2-M3/V2-M4A, read only:
   - `docs/references/handoff-prd-v2-implementation.md` §0, §3, §7;
   - `docs/PRD-v2-Memory-Extension.md` FR-01, FR-02, FR-06..FR-10, FR-15..FR-18;
   - `docs/references/qdrant-postgresql-techstack-evaluation.md` §1–3.
5. Execute the immediate next task plan from §7 below.
6. Update only the affected dashboard rows and latest-proof snapshot at a verified boundary.

Recommended skills for implementation: `api-and-interface-design`, `test-driven-development`, `incremental-implementation`, `security-and-hardening`, `git-workflow-and-versioning`.

## 2. Target summary

Cowork Agent is a FastAPI/Streamlit project with a completed read-only Gmail
workflow. PRD-v2 adds a multi-turn AI Chat Assistant whose controller accesses
working, declarative, episodic, and semantic memory only through `MemoryGateway`.
The durable/index split and safety model are fixed in §3.

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
- Branch `feature/v2-m3-chat-summary` is at `a33ce71`. M3.1-M3.4a are committed; M3.4a still needs a fresh final Sol verdict before acceptance.
- `dev` is `91dff59`; merge-base is `148a779` with 7 feature commits and 5 dev commits unique. Reconciliation is a later explicit task.
- Latest parent verification after the M3.4a citation-allowlist correction: 30 live PostgreSQL persistence tests passed with zero skips; 198 domain + AI Chat tests passed; full suite 696 passed, 25 skipped, 4 xfailed using an explicit writable external pytest temp directory; Ruff, `mypy src` across 79 files, and `git diff --check` were clean.
- Docker Desktop is currently unavailable. The PostgreSQL results above are retained evidence from the earlier live `cowork-pg` run, not a new rerun.

## 4. Authority and selective document routing

| Need | Read only this |
|---|---|
| Always-needed rules | `AGENTS.md` |
| Sol Advisor routing, packets, and lessons | `docs/references/improve-orchestration-efficiency.md` |
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

## 7. Active execution queue

Work only the first item whose prerequisite is accepted. Each item is a
test-first, acceptance-sized slice with focused verification, parent diff review,
and dashboard update afterward.

1. **M3.4a final review — NEXT:** capture status/hashes, obtain fresh Sol verdict,
   compare post-review state, then accept or return concrete corrections.
2. **M3.4b-A producer:** explicit-request idempotent creation through Gateway
   (AC-06/AC-07).
3. **M3.4b-B lifecycle/deletion:** originating-session transitions and deletion
   through Gateway (AC-08/AC-09/AC-15).
4. **M3.4b-C retrieval:** eligible, bounded episode context through Gateway; prove
   model isolation (AC-10–AC-12).
5. **Controller/reply completion:** close AC-01, AC-05, AC-13, and AC-17.
6. **Governance completion:** close AC-16–AC-18 with operational evidence.

## 8. Source and test map for next tasks

Read these before editing:

| Component | Target Files |
|---|---|
| Domain Contracts | `src/cowork_agent/domain/chat_contracts.py`, `_chat_contracts_*.py` |
| AI Chat Feature | `src/cowork_agent/features/ai_chat/{memory_gateway,episode_policy,profile_policy,session_buffer,ports}.py` |
| Chat Controller | `src/cowork_agent/features/ai_chat/controller.py` |
| Persistence & Migrations | `src/cowork_agent/persistence/repositories/postgres.py`, `src/cowork_agent/persistence/migrations/001..004_*.sql` |
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
1. **Branch reconciliation:** feature HEAD is `a33ce71`, live `dev` is `91dff59`, and their merge-base remains `148a779` (`7` feature commits and `5` dev commits are unique). Do not merge, rebase, reset, or push unless explicitly authorized. Reconcile in a separate task.
2. **Acceptance verification:** M3.4a is centrally green (30 live PostgreSQL persistence tests; 198 domain + AI Chat tests; full suite 696 passed, 25 skipped, 4 xfailed; Ruff, mypy, and diff check clean), but a fresh final Sol verdict is required after the citation-allowlist correction. Final post-reconciliation verification also remains required.
3. **Durability boundary:** M3.1-M3.4a are committed. M3.4a is not finally
   accepted until a fresh final Sol verdict is recorded;
   M3.4b Gateway lifecycle wiring remains a separate next increment.
4. **Local PostgreSQL availability:** Docker Desktop is currently unavailable. Do
   not convert this into skipped acceptance evidence; restart Docker and confirm
   `cowork-pg` before the next PostgreSQL verification run.

## 11. Latest verification snapshot

Keep only the latest proof required to decide the next gate. Detailed historical
results live in Git and reusable process lessons live in the orchestration playbook.

| Scope | Latest evidence | Use |
|---|---|---|
| M3.4a PostgreSQL durability | 30 live persistence tests passed, zero skips | Fresh final review and later regression comparison |
| Shared contracts | 198 domain + AI Chat tests passed | Required after a shared contract or Gateway change |
| Broad regression | Full suite: 696 passed, 25 skipped, 4 xfailed using writable external pytest temp | Run once after the final correction before an acceptance review |
| Static / hygiene | Ruff clean; `mypy src` clean across 79 files; `git diff --check` clean | Required for source changes |

Evidence rule: a task report is a claim. Retain the exact command and output in the
task handoff or review packet, not in this dashboard. Do not mark an AC `DONE` from
a skipped database test.

## 12. End-of-session handoff template

```text
ACTIVE MILESTONE / SLICE: V2-M3 TaskEpisode PostgreSQL durability (M3.4a final review)
STATUS AND PERCENT: ACTIVE, 90%; implementation and parent gates green, fresh final Sol verdict pending
COMMITS: a33ce71 on feature/v2-m3-chat-summary; dev 91dff59; merge-base 148a779; no merge/rebase/push without explicit authorization
TESTS / LINT / TYPES: live PostgreSQL 30 passed, zero skips; domain + AI Chat 198 passed; full 696 passed, 25 skipped, 4 xfailed; Ruff pass; mypy 79 files pass; diff check clean
AC EVIDENCE ADDED: durable scoped idempotency, immutable identity, storage-derived eligibility, bounded FTS retrieval, exact citation keys, deletion, and purge
EXACT NEXT ACTION: start Docker/cowork-pg if needed, capture M3.4a status and hashes, obtain a fresh behaviorally read-only Sol final review, compare post-review hashes, then either correct findings or accept M3.4a and dispatch M3.4b Gateway lifecycle wiring
```
