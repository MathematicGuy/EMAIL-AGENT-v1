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
| Branch / implementation baseline | `dev` includes `0d0bc22` (`feat(chat): complete memory-aware chat runtime`); M3.1-M3.4b-C, V2-M4, and V2-M5 are committed and accepted; no history rewrite without explicit authorization |
| Product frontier | PRD-v2 Multi-Turn AI Chat Memory |
| Active milestone | **PRD-v2 complete — V2-M6 DONE (fresh final review verdict `ship` 2026-08-11)**; next frontier is the separate DEMO frontend workstream |
| PRD-v2 progress | All milestones M3.4a through V2-M6 accepted; AC-01..AC-18 DONE |
| Tech stack authority | PostgreSQL (authoritative durable store); Qdrant (rebuildable enterprise RAG index) |

## 1. New-session launch sequence

Do these in order; do not reread the entire documentation set first.

1. Read `AGENTS.md` (project rules and four non-negotiable invariants).
2. Read this dashboard and `docs/references/improve-orchestration-efficiency.md`.
3. Run `git status --short --branch` and inspect uncommitted worktree changes.
4. Execute the immediate next task plan from §7 below.
5. Update only the affected dashboard rows and latest-proof snapshot at a verified boundary.

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
- Branch `dev` includes implementation commit `0d0bc22`. The accepted M3.4b-B whitespace-ID correction and the complete M3.4b-C/V2-M4/V2-M5 runtime are committed. Do not rewrite or reorder accepted history without explicit authorization.
- The cumulative corrected final verdict is `ship`. Parent proof on the accepted snapshot is 186 impacted AI Chat/runtime tests and 20 live PostgreSQL persistence tests with zero skips, plus scoped Ruff, mypy, and diff-check green.
- The merged repository-wide suite currently cannot collect because unrelated knowledge-ingestion modules lack `docx` and two unrelated test modules share `test_query_guard`; milestone verification stays scoped rather than changing those components.
- Docker Desktop and `cowork-pg` were available for the final live persistence gate. Preflight them before a future database gate; do not treat a connection timeout or temp ACL cascade as a code regression.

## 4. Authority and selective document routing

| Need | Read only this |
|---|---|
| Always-needed rules | `AGENTS.md` |
| Sol Advisor routing, packets, and lessons | `docs/references/improve-orchestration-efficiency.md` |
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
| V2-M3 Chat-native episodes | **DONE** | 100 | M3.1-M3.4b-C accepted: PostgreSQL durability, fail-closed producer/lifecycle/deletion, and eligible runtime wiring through Gateway | V2-M1, V2-M2 | Corrected cumulative verdict `ship`; 186 impacted tests; live PostgreSQL 20 passed; scoped Ruff, mypy, and diff check clean |
| V2-M4 Chat Controller + SSE | **DONE** | 100 | Verified-principal session/message APIs, configured reply adapters, typed SSE, explicit bounded proposals, and originating-session approve/complete/reject/delete controls | V2-M1–M3 | Explicit-policy, proposal, SSE citation, lifecycle, cancellation, idempotency, and retry paths included in the 186-test parent gate; final verdict `ship` |
| V2-M5 Selective episodic + RAG retrieval | **DONE** | 100 | Selective bounded episodic + semantic reads, eligible-only model context, real company-evidence consumption, citation allowlisting, precedence, and graceful degradation | V2-M4 | Real semantic-adapter/provider seam, cross-session eligible episodes, company-evidence precedence, and outage behavior included in the 186-test parent gate; final verdict `ship` |
| V2-M6 Evaluation + governance | **DONE** | 100 | Production metadata-only sink/metrics injected at runtime; env-driven retention applied with retry-safe expiry; explicit purge CLI; exact-scope deletion audit; backup/restore proof; paired evaluation runner with product-approved thresholds; AC-18 audit 6/6 PASS | V2-M5 core contracts | 224 unit + 41 live-DB/API tests green; launch gate exit 0 (Moderate-MVP thresholds, safety counters zero); AC-16 and AC-18 evidence recorded |

## 6. PRD-v2 acceptance dashboard

| ID | Acceptance statement | Status | Evidence |
|---|---|---:|---|
| AC-01 | Chat Controller accesses all four memories only through Gateway | DONE | Production controller composition injects working, profile, PostgreSQL episodic, and semantic adapters only through `MemoryGateway` |
| AC-02 | Every operation carries tenant/user/session/`feature: ai_chat`/type | DONE | `310d2fd`, `2a29e29`; domain namespace + gateway tests |
| AC-03 | Bounded working buffer preserves turns and expires by policy | DONE | `7e42784`, `2a29e29`; `tests/unit/features/ai_chat/test_session_buffer.py` |
| AC-04 | Explicit persona/preferences persist and load in later sessions | DONE | Profile policy/gateway/PostgreSQL repo green (`test_chat_profile_repository.py` 15 passed) |
| AC-05 | Assistant events stream to the active session | DONE | Active-session delta/error/episodic-citation/completed SSE, cancellation, and idempotent replay are proven |
| AC-06 | Only explicit user task requests create idempotent TaskEpisodes | DONE | Full-message deterministic finite grammar is fail-closed on pre/post-cue negation; transient writes retry the same server-built episode without a second reply or turn |
| AC-07 | New TaskEpisodes are system-generated and retrieval-ineligible | DONE | Controller owns identity/provenance/status and Gateway validates canonical `system_generated` / `retrieval_eligible=false` writes |
| AC-08 | Inline approval/completion makes episode eligible | DONE | Originating-session controls call Gateway transitions; storage derives approved/completed eligibility atomically |
| AC-09 | Inline rejection keeps episode ineligible | DONE | Originating-session rejection calls Gateway; storage-derived rejected eligibility remains false |
| AC-10 | Retrieval returns approved/completed episodes only | DONE | PostgreSQL and Gateway apply same-tenant/user/feature, status, eligibility, expiry, relevance, timeout, and result bounds |
| AC-11 | Model cannot retrieve unvalidated episodes directly | DONE | Configured reply adapters consume only labeled `GenerationContext`; advisory episodes are the Gateway-filtered eligible set |
| AC-12 | Episodic and semantic retrieval are selective and bounded | DONE | Deterministic intent selection, bounded PostgreSQL FTS, ready-only semantic retrieval, and provider-context bounds are proven |
| AC-13 | Current company evidence outranks prior episode guidance | DONE | Configured provider payload declares and enforces current instruction → current company evidence → stored preference → advisory episode precedence |
| AC-14 | TaskEpisodes exclude raw source content and tool payloads | DONE | Server-owned bounded proposals exclude raw email/transcript/tool/Gmail/run/mailbox fields; persisted citations must match current company-evidence coordinates |
| AC-15 | Exact-scope deletion prevents later retrieval without deleting semantic RAG | DONE | Originating-session Gateway deletion and scoped PostgreSQL deletion/purge are proven; semantic company RAG is excluded |
| AC-16 | Production telemetry is metadata-only | DONE | `LoggingMemoryOperationSink` + thread-safe `MemoryOperationMetrics` injected into every `MemoryGateway` at runtime composition; only validated 8-field `to_dict()` metadata reaches logs/metrics; DENIED outcomes log ERROR as alertable safety incidents; sink failure can never block chat; purge emits metadata-only events |
| AC-17 | Memory outage degrades chat and preserves standalone Email Agent | DONE | Optional reads degrade safely; transient episode writes preserve the reply and retry safely; standalone Email Agent remains separate and unchanged |
| AC-18 | No in-chat tool, scheduler, recurring processing, or autonomous email action | DONE | 2026-08-11 audit 6/6 PASS: SSE/DTO contracts carry no tool fields (tool-shaped keys rejected, `extra="forbid"`); retired `tool_choices` rejected 422 before dispatch; purge/backup scripts are explicit-invocation only and unreferenced by src; Gmail adapter is read-only with `gmail.readonly` scope enforced; chat feature imports no email path; PRD-v1 digest workflow diff-empty since `ff614f0` |

## 7. Active execution queue

Work only the first item whose prerequisite is accepted. Each item is a
test-first, acceptance-sized slice with focused verification, parent diff review,
and dashboard update afterward.

1. **M3.4a final review — DONE:** fresh verdict `ship`; reviewer ran under
   `workspace-write` / `managed` rather than enforced read-only, remained
   behaviorally read-only, and post-review status/hashes were unchanged.
2. **M3.4b-A producer — DONE:** explicit-request initial writes are authorized,
   reconstructed to a canonical bounded `TaskEpisode`, and dispatched through
   the Gateway at exact scope/identity; fresh verdict `ship` and hashes unchanged.
3. **M3.4b-B lifecycle/deletion — DONE:** originating-session transitions and deletion
   through Gateway; corrected final verdict `ship` (AC-08/AC-09/AC-15).
4. **Combined M3.4b-C + V2-M4 controller/reply completion — DONE:** production
   episodic composition, model isolation, configured reply adapters, explicit
   proposal SSE, lifecycle controls, and retry-safe degradation are accepted.
5. **V2-M5 selective retrieval completion — DONE:** eligible episodic and current
   company evidence are selectively bounded, labeled, allowlisted, and consumed
   with deterministic precedence; corrected final verdict `ship`.
6. **Governance completion — DONE:** V2-M6 closed AC-16 (runtime production sink/metrics) and
   AC-18 (2026-08-11 product audit 6/6 PASS); retention, purge CLI, deletion audit,
   backup/restore, and the paired evaluation launch gate are evidenced; final delta review pending.

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
1. **History boundary:** the accepted M3.4/V2-M4/V2-M5 runtime is committed in
   `0d0bc22`; do not reset, rebase, amend, or otherwise rewrite that accepted
   boundary without explicit authorization.
2. **Broad-suite boundary:** unrelated missing-`docx` imports and duplicate
   `test_query_guard` module names prevent repository-wide collection; do not
   broaden this completed milestone to repair them.
3. **Final acceptance boundary — cleared:** fresh final review of the accumulated V2-M6 delta
   returned `ship` (2026-08-11); reviewer operated read-only and before/after hashes matched.

## 11. Latest verification snapshot

Keep only the latest proof required to decide the next gate. Detailed historical
results live in Git and reusable process lessons live in the orchestration playbook.

| Scope | Latest evidence | Use |
|---|---|---|
| V2-M6 cumulative unit | 224 passed: `tests/unit/features/ai_chat` + runtime composition + memory config + purge script + evaluation runner | Current observability/retention/evaluation regression evidence |
| Live PostgreSQL + Chat API | 41 passed, zero skips against running `cowork-pg` (`tests/integration/api/test_chat_api.py` + `tests/integration/persistence`), incl. deletion audit and backup/restore proof | Deletion non-retrievability, expiry-before-purge, exact-scope deletion, restore lifecycle/expiry evidence |
| Launch gate | `scripts/run_paired_chat_evaluation.py --json` exit 0 with product-approved Moderate-MVP thresholds; all five hard safety counters zero; deltas 0.13/0.09/0.09, degradation 0.0 | AC-16/FR-17 evaluation evidence |
| Governance audit | AC-18 audit 6/6 PASS with file:line citations; PRD-v1 digest path diff-empty since `ff614f0` | AC-18 evidence |
| Static / hygiene | Scoped Ruff clean; `python -m mypy src` shows only the 5 pre-existing unrelated `docx_extractor` errors; `git diff --check` clean | Required for changed milestone paths |
| Broad regression | Unavailable on merged baseline because unrelated `docx` imports and duplicate `test_query_guard` module names fail collection | Do not broaden milestone scope to repair unrelated baseline |

Evidence rule: a task report is a claim. Retain the exact command and output in the
task handoff or review packet, not in this dashboard. Do not mark an AC `DONE` from
a skipped database test.

## 12. End-of-session handoff template

```text
ACTIVE MILESTONE / SLICE: V2-M6 governance (separate future delivery)
STATUS AND PERCENT: V2-M3 DONE 100%; V2-M4 DONE 100%; V2-M5 DONE 100%
COMMITS: dev includes accepted runtime commit 0d0bc22; no history rewrite without explicit authorization
TESTS / LINT / TYPES: final impacted runtime 186 passed; live PostgreSQL 20 passed, zero skips; scoped Ruff, mypy, and diff check clean; broad suite blocked by unrelated collection errors
AC EVIDENCE ADDED: production four-memory Gateway composition, configured labeled reply context, explicit bounded proposals, originating-session lifecycle, same-episode retry, selective eligible retrieval, real company-evidence precedence, and grounded citation allowlisting
EXACT NEXT ACTION: start V2-M6 governance from the milestone-focused OS-temp handoff; do not replay V2-M3/V2-M4/V2-M5
```
