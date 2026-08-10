# Session Bootstrap & PRD-v2 Delivery Dashboard

> Start here in a new coding session. This file is the compact operational
> view for planning, implementation, and project tracking. Read `AGENTS.md`
> first, then this file. Open a larger PRD/architecture document only when the
> routing table below says the current task needs it or a conflict appears.

| Field | Current value |
|---|---|
| Updated | 2026-08-10 (Asia/Bangkok) |
| Branch / implementation baseline | `main` / `2a29e29` (implementation; run `git rev-parse --short HEAD` for the current handoff commit) |
| Product frontier | PRD-v2 Multi-Turn AI Chat Memory + executable `@Email` tool |
| Active milestone | **V2-M2 — AI Chat Declarative Profile** |
| PRD-v2 progress | **1/6 milestones; 2/20 acceptance criteria complete** |
| V1 foundation | PRD-v1 §15 passed; Email RAG pipeline implemented and memory-free |
| Formal readiness caveat | V1-H task 5.5 and final hardening checkpoint remain open |
| Repository docs | Dashboard and trackers reconciled through V2-M1 |

## 1. New-session launch sequence

Do these in order; do not reread the entire documentation set first.

1. Read `AGENTS.md` (project rules and four non-negotiable invariants).
2. Read this handoff completely.
3. Run `git status --short --branch` and preserve the user-owned dirty files
   listed in §3 unless their scope is explicitly changed.
4. For V2-M2, read only:
   - `docs/PRD-v2-Memory-Extension.md` §10, FR-03..FR-05, FR-15,
     FR-16, FR-18, §16, §17;
   - `docs/master-comparison.md` V2-M2 and the profile/provenance contracts;
   - the source/test slice in §8 below.
5. Produce a thin-slice plan from §7, then implement with tests first.
6. Update the dashboard using §12 before ending the session.

Recommended skills for implementation: `api-and-interface-design`,
`test-driven-development`, `incremental-implementation`,
`security-and-hardening`, and `git-workflow-and-versioning`. Use
`playwright-cli` when a frontend slice becomes runnable.

## 2. Project brain dump

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

MEMORY TYPES:
  1. Working: bounded active-session turns + transient tool state.
  2. Declarative: explicit persona/preferences only.
  3. Episodic: chat summaries + derived @Email Action Plans.
  4. Semantic: enterprise RAG; no direct Chat Controller writes.

NAMESPACE:
  tenant_id / user_id / session_id / feature: ai_chat /
  memory_type / record_id

SAFETY MODEL:
  New tool episodes start system_generated + retrieval_eligible=false.
  Only approved/completed episodes become retrievable. Raw Gmail bodies are
  transient and never enter DB rows, chat history, logs, traces, prompts
  stored for replay, browser storage, or indexes.
```

## 3. Guardrails and workspace state

Non-negotiable:

- Gmail scope stays `gmail.readonly`; never send, modify, delete, or move mail.
- Raw email bodies and attachment content are never persisted or logged.
- Attachments are presence-only (`attachments_processed=false`, ADR-003).
- `HybridSemanticMemory` is the V1-M3 local retrieval implementation.
- Qdrant and the four-type production memory system are milestone-gated; do
  not scaffold beyond the active PRD-v2 milestone.
- Domain and feature code remain framework-free; dependency direction is
  `domain ← features ← integrations/orchestration/persistence ← app`.

User-owned worktree changes present on 2026-08-10—do not overwrite, stage, or
commit unless the user explicitly puts them in scope:

```text
D  .claude/skills/caveman/README.md
D  .claude/skills/caveman/SKILL.md
M  scripts/run_gui.py
?? skills-lock.json
?? uv.lock
```

Local runtime context (informational; recheck rather than assume it survived):

- Backend was healthy at `http://127.0.0.1:8000` via
  `.venv/bin/mail-todo-api`.
- GUI was healthy at `http://127.0.0.1:8501` via
  `.venv/bin/python scripts/run_gui.py`.
- Gmail OAuth was completed locally on 2026-08-10. Never copy credentials or
  token material into docs, tests, logs, or commits.

## 4. Authority and selective document routing

| Need | Read only this |
|---|---|
| Always-needed rules | `AGENTS.md` |
| Product behavior / acceptance | `docs/PRD-v2-Memory-Extension.md` relevant FR + §16–17 |
| Component ownership, APIs, SSE | `docs/architectures/TARGET-ARCHITECTURE.md` §5, §7–10, §16–17 |
| Exact DTO contracts / sequencing | `docs/master-comparison.md` §6 and V2 milestone in §7 |
| Frontend slice | `docs/SPEC-Demo-Frontend.md` relevant increment + §8–9 |
| Detailed task inventory | `tasks/plan.md`, then `tasks/todo.md` |
| Why memory moved to chat | `docs/references/memory-system-and-chat-demo-analysis.md` §5 only |

Conflict precedence:

1. `AGENTS.md` invariants.
2. PRD-v2 product behavior and acceptance criteria.
3. Target Architecture component ownership and service boundaries.
4. Master Comparison concrete contracts and migration ordering.
5. This dashboard and task trackers.

Surface a conflict; do not silently invent a hybrid design.

## 5. PRD-v2 project-management dashboard

Status legend: `NEXT` = ready now, `BLOCKED` = dependency not met,
`ACTIVE` = implementation in progress, `VERIFY` = code done/evidence pending,
`DONE` = exit gate evidenced. Percent is evidence-based, not effort-based.

| Milestone | Status | % | Deliverable / exit gate | Depends on | Evidence |
|---|---:|---:|---|---|---|
| V2-M1 Gateway + session working memory | **DONE** | 100 | Chat/memory contracts; fail-closed namespace; bounded session TTL; no gateway bypass | PRD-v1 baseline | `7e42784..2a29e29`; 73 focused tests; deterministic suite exit 0 |
| V2-M2 Declarative chat profile | **NEXT** | 0 | Explicit-only profile CRUD; per-turn compact load; fallback; deletion/retention | V2-M1 | — |
| V2-M3 Chat + `@Email` episodes | BLOCKED | 0 | Idempotent summaries/tool episodes; mandatory provenance; raw-body rejection; ineligible default | V2-M1, V2-M2 contracts | — |
| V2-M4 Chat Controller + SSE + tool | BLOCKED | 0 | Session/message APIs; typed SSE; `@Email` wrapper; inline lifecycle commands | V2-M1–M3 | — |
| V2-M5 Selective episodic + RAG retrieval | BLOCKED | 0 | Intent-triggered bounded retrieval; eligibility filters; labeled context; conflict precedence | V2-M4 | — |
| V2-M6 Evaluation + governance | BLOCKED | 0 | Memory on/off evaluation; retention/purge/deletion audit; safety alerts; launch thresholds | V2-M5 | — |

### Cross-cutting gates

| Gate | Status | Owner / next proof |
|---|---|---|
| PRD-v1 §15 product gate | DONE | Existing evidence in `tasks/plan.md` |
| V1-H 5.5 advanced observability/numeric gates | OPEN | Live-run evidence and user-approved thresholds |
| V1-H final separate-process/restart checkpoint | OPEN | Evidence-based orchestration sign-off |
| PRD-v2 §16 gate | BLOCKED | Requires AC-01..AC-20 below |
| DEMO-B unlock | BLOCKED | Requires PRD-v2 §16 pass |

Operational rule: V2-M2 contract/policy and profile persistence may start now.
Do not scaffold episodes, controller/SSE, or production vector infrastructure.

## 6. PRD-v2 acceptance dashboard

| ID | Acceptance statement | Status | Evidence |
|---|---|---:|---|
| AC-01 | Chat Controller accesses all four memories only through Gateway | TODO | Gateway-only feature boundary: `2a29e29`; actual Chat Controller proof remains V2-M4 |
| AC-02 | Every operation carries tenant/user/session/`feature: ai_chat`/type | DONE | `310d2fd`, `2a29e29`; domain namespace + foreign-scope gateway tests |
| AC-03 | Bounded working buffer preserves turns and expires by policy | DONE | `7e42784`, `2a29e29`; `tests/unit/features/ai_chat/test_session_buffer.py` |
| AC-04 | Explicit persona/preferences persist and load in later sessions | TODO | — |
| AC-05 | User can invoke `@Email` inside a chat thread | TODO | — |
| AC-06 | `@Email` stays stateless and owns no durable memory | TODO | — |
| AC-07 | Assistant/tool/card events stream to the active session | TODO | — |
| AC-08 | Rendered tool plan writes one idempotent system-generated episode | TODO | — |
| AC-09 | New tool episode is retrieval-ineligible | TODO | — |
| AC-10 | Inline approval/completion makes episode eligible | TODO | — |
| AC-11 | Inline rejection keeps episode ineligible | TODO | — |
| AC-12 | Retrieval returns approved/completed episodes only | TODO | — |
| AC-13 | Model cannot retrieve unvalidated episodes directly | TODO | — |
| AC-14 | Episodic and semantic retrieval are selective and bounded | TODO | — |
| AC-15 | Current company evidence outranks prior episode guidance | TODO | — |
| AC-16 | Raw email absent durable memory, chat, telemetry, browser storage | TODO | — |
| AC-17 | Deletion prevents later retrieval | TODO | — |
| AC-18 | Production telemetry is metadata-only | TODO | — |
| AC-19 | Memory outage degrades chat without corrupting `@Email` | TODO | — |
| AC-20 | No scheduler, recurring scan, or autonomous email action added | TODO | — |

Evidence format: link a test path, commit SHA, migration, browser artifact, or
gate record. `DONE` without evidence is invalid. Update the top-level `2/20`
counter whenever this table changes.

## 7. Immediate implementation plan — V2-M2

Goal: persist and load a compact, explicit-only declarative chat profile
without coupling memory into the Email Action Plan workflow.

### Slice M2.0 — explicit-write policy and repository contract

1. Extend the existing `DeclarativeProfile` contract only for fields required
   by FR-03's first UI slice; keep the payload compact and bounded.
2. Define a framework-free profile repository port and pure write policy that
   accepts explicit user configuration, explicit remember requests, or trusted
   admin configuration only.
3. Freeze rejection tests for passive chat/email inference and foreign scope.

### Slice M2.1 — PostgreSQL profile persistence

1. Add an additive migration for the namespaced profile row, expiration, and
   timestamps; never add raw email or ordinary chat payload columns.
2. Implement idempotent profile upsert/read/delete behind the repository port.
3. Prove tenant/user isolation, explicit-only provenance, expiry, and deletion
   with deterministic repository tests and PostgreSQL integration evidence.

### Slice M2.2 — gateway integration and degraded fallback

1. Add the explicit profile write/delete operations to the Memory Gateway;
   semantic and episodic writes remain impossible.
2. Load a compact profile per requested chat turn; absence is a normal empty
   result, while adapter outage returns the typed long-term degradation.
3. Prove profile failure leaves working memory and the stateless `@Email`
   pipeline untouched. Do not add Chat API routes or UI in this milestone.

### V2-M2 exit checklist

- [ ] Explicit-only profile write policy rejects passive inference.
- [ ] Profile CRUD is tenant/user isolated, idempotent, and expiry-aware.
- [ ] Gateway returns compact profile data or typed long-term degradation.
- [ ] Deletion prevents later profile retrieval.
- [ ] No email body or ordinary chat transcript enters the profile schema.
- [ ] Focused tests, PostgreSQL integration proof, Ruff, and mypy pass.
- [ ] AC-04 plus applicable AC-17/AC-19 evidence is recorded.
- [ ] Dashboard, `tasks/plan.md`, and `tasks/todo.md` agree.

## 8. Source and test map for V2-M2

Read these before editing; do not load unrelated provider/UI files.

| File | Why it matters |
|---|---|
| `src/cowork_agent/domain/chat_contracts.py` | Public chat/profile contract facade |
| `src/cowork_agent/features/ai_chat/ports.py` | Add the profile repository boundary here |
| `src/cowork_agent/features/ai_chat/memory_gateway.py` | Enforce explicit writes and degraded reads |
| `src/cowork_agent/persistence/repositories/postgres.py` | Existing PostgreSQL adapter conventions |
| `src/cowork_agent/persistence/migrations/001_mail_todo.sql` | Baseline only; add a new migration, never rewrite it |
| `tests/integration/persistence/test_postgres_repositories.py` | PostgreSQL integration/skip pattern |

Expected new test homes:

```text
tests/unit/features/ai_chat/test_profile_policy.py
tests/unit/features/ai_chat/test_memory_gateway.py
tests/integration/persistence/test_chat_profile_repository.py
```

Naming may follow existing repository conventions discovered during M1.0;
avoid creating generic `utils.py` or premature storage abstractions.

## 9. Verification commands

Use `.venv/bin/python` on this machine; plain `python` was unavailable.

```bash
.venv/bin/python -m pytest <smallest-relevant-test-path> -q
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src
```

Because V2-M1 adds shared contracts, expand to the full suite before its exit
gate if the focused tests pass:

```bash
.venv/bin/python -m pytest -q
```

For later frontend slices:

```bash
.venv/bin/mail-todo-api
.venv/bin/python scripts/run_gui.py
playwright-cli open http://127.0.0.1:8501
```

## 10. Known frontend/runtime evidence

Playwright review on 2026-08-10 established:

- Gmail OAuth connection was recognized; a bounded five-message read-only run
  completed `5/5` with status `succeeded` and the correct empty-task state.
- Knowledge retrieval returned five provenance-linked chunks; console had no
  errors; DOM/storage/result schema showed no raw-email fields.
- Current GUI is still email-first and has no chat input—expected until V2-M4.
- Accessibility debt: major sections are `h3`; no `h1` or `h2`.
- Knowledge UI returns ranked chunks, not the grounded answer promised by the
  updated frontend spec.
- One observed retrieval took 3411 ms versus the 3000 ms target; one sample is
  diagnostic, not a p95 measurement.
- At 320 px the page avoids root overflow, but the Knowledge table is cramped
  and long source paths clip or require internal scrolling.

Do not "fix while here" during V2-M1. Track these for the appropriate DEMO or
performance slice.

## 11. Decisions, risks, and blockers

### Locked decisions

| Decision | Consequence |
|---|---|
| Chat Controller owns all Memory Gateway access | Tool/cards return commands/results to Chat Controller; no direct memory writes |
| Email RAG remains stateless | No profile/episode reads inside `email_action_plan/workflow.py` |
| New episodes default ineligible | Eligibility enforced in code/storage filters, never prompts |
| Semantic memory is retrieval-only | Chat and tool can read; neither writes enterprise corpus |
| SSE is the chat delivery contract | Typed delta/tool/citation/completed/error events |
| Chat contracts live behind a focused facade | `domain/chat_contracts.py` re-exports focused private chat/common/memory modules |
| Working-memory TTL is inactivity-based | Appends refresh a 1,800-second default; reads do not; newest 20 logical turns retained |

### Open decisions (resolve only when their milestone needs them)

| Decision | Needed by | Current handling |
|---|---|---|
| PostgreSQL profile/episode schema migration shape | V2-M2/M3 | Resolve profile shape in M2.0; add a new migration in M2.1 |
| Episode relevance algorithm/threshold | V2-M5 | Deferred |
| Memory quality launch threshold | V2-M6 | User/product decision |

Primary risks:

- accidentally coupling memory into the standalone Email pipeline;
- treating `run_id` as chat working-memory identity instead of `session_id`;
- implementing storage before fail-closed contracts/policy;
- allowing a provider/model to override retrieval eligibility;
- logging assembled context or raw email during debugging;
- mixing the user-owned dirty worktree changes into V2 commits.

## 12. Dashboard update protocol

The orchestration agent owns this file as the PRD-v2 project dashboard.
Update it at every verified slice or changed blocker:

1. Change `Updated`, branch/baseline, active milestone, and counters at top.
2. Change milestone status/percent only from verified evidence:
   - 0%: no evidence;
   - 25%: contracts/tests landed;
   - 50%: core behavior landed;
   - 75%: integration and failure paths landed;
   - 100%: exit checklist and required verification pass.
3. Update AC rows with exact test/commit/browser evidence.
4. Record a decision when a public interface, storage contract, or boundary is
   chosen; do not bury it only in conversation.
5. Record blockers with owner and next proof; never label ordinary incomplete
   work as blocked.
6. Reconcile the affected checkboxes in `tasks/todo.md` and descriptive plan
   in `tasks/plan.md`.
7. Replace §7 with the next milestone's immediate slices after the current
   exit gate passes; archive no long logs here.
8. Keep this file compact (target under 400 lines). Link evidence instead of
   pasting command logs or entire PRD sections.

### Evidence ledger

| Date | Evidence | Meaning |
|---|---|---|
| 2026-08-10 | `e25a674..e339f6f` | PRD-v2, target architecture, contracts, frontend spec, and trackers realigned to AI Chat |
| 2026-08-10 | Playwright live review summarized in §10 | Current authenticated V1 frontend/runtime baseline |
| 2026-08-10 | `7e42784..2a29e29` | V2-M1 contracts, deep immutability, fail-closed gateway, bounded session buffer |
| 2026-08-10 | 73 focused tests + deterministic full suite exit 0 | V2-M1 code gate; unfiltered live E2E separately failed on Gmail reauth/HTTP timeout |

## 13. End-of-session handoff template

Before stopping, replace this block's placeholders with current facts:

```text
ACTIVE MILESTONE / SLICE: V2-M2 / M2.0 explicit profile policy and repository contract
STATUS AND PERCENT: V2-M1 DONE 100%; V2-M2 NEXT 0%
COMMITS: 7e42784, 310d2fd, 1d92fa1, 2a29e29
TESTS / LINT / TYPES: 73 focused pass; Ruff pass; mypy 67 files pass; deterministic suite exit 0
AC EVIDENCE ADDED: AC-02 and AC-03 complete; AC-01 gateway boundary only (controller proof V2-M4)
FILES CHANGED: chat/memory contracts; ai_chat gateway/ports/session buffer; config/tests; dashboard/trackers
USER-OWNED DIRTY FILES PRESERVED: caveman deletions, scripts/run_gui.py, skills-lock.json, uv.lock
OPEN BLOCKER + OWNER + NEXT PROOF: live Gmail needs reauthorization; user/runtime owner; rerun live E2E after reconnect
EXACT NEXT ACTION: implement M2.0 explicit profile write policy and repository port test-first
```

The next agent should be able to act from those fields plus §§1, 7, and 8
without replaying prior conversation or rereading all architecture documents.
