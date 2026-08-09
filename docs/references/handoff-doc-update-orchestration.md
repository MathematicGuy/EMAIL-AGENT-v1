# Session Bootstrap & PRD-v2 Delivery Dashboard

> Start here in a new coding session. This file is the compact operational
> view for planning, implementation, and project tracking. Read `AGENTS.md`
> first, then this file. Open a larger PRD/architecture document only when the
> routing table below says the current task needs it or a conflict appears.

| Field | Current value |
|---|---|
| Updated | 2026-08-10 (Asia/Bangkok) |
| Branch / implementation baseline | `main` / `e339f6f` (run `git rev-parse --short HEAD` for current handoff commit) |
| Product frontier | PRD-v2 Multi-Turn AI Chat Memory + executable `@Email` tool |
| Active milestone | **V2-M1 — Chat Memory Gateway & Session Working Memory** |
| PRD-v2 progress | **0/6 milestones; 0/20 acceptance criteria evidenced** |
| V1 foundation | PRD-v1 §15 passed; Email RAG pipeline implemented and memory-free |
| Formal readiness caveat | V1-H task 5.5 and final hardening checkpoint remain open |
| Repository docs | Clean after `e339f6f`; no uncommitted doc/task changes |

## 1. New-session launch sequence

Do these in order; do not reread the entire documentation set first.

1. Read `AGENTS.md` (project rules and four non-negotiable invariants).
2. Read this handoff completely.
3. Run `git status --short --branch` and preserve the user-owned dirty files
   listed in §3 unless their scope is explicitly changed.
4. For V2-M1, read only:
   - `docs/PRD-v2-Memory-Extension.md` §10, FR-01, FR-02, FR-18, §16, §17;
   - `docs/master-comparison.md` contracts 6.3–6.5, 6.9, and V2-M1;
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
| V2-M1 Gateway + session working memory | **NEXT** | 0 | Chat/memory contracts; fail-closed namespace; bounded session TTL; no gateway bypass | PRD-v1 baseline | — |
| V2-M2 Declarative chat profile | BLOCKED | 0 | Explicit-only profile CRUD; per-turn compact load; fallback; deletion/retention | V2-M1 | — |
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

Operational rule: V2-M1 contract-first code and deterministic local tests may
start now. Do not claim production readiness or unlock later milestones until
their dependency and acceptance evidence is recorded here.

## 6. PRD-v2 acceptance dashboard

| ID | Acceptance statement | Status | Evidence |
|---|---|---:|---|
| AC-01 | Chat Controller accesses all four memories only through Gateway | TODO | — |
| AC-02 | Every operation carries tenant/user/session/`feature: ai_chat`/type | TODO | — |
| AC-03 | Bounded working buffer preserves turns and expires by policy | TODO | — |
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
gate record. `DONE` without evidence is invalid. Update the top-level `0/20`
counter whenever this table changes.

## 7. Immediate implementation plan — V2-M1

Goal: land the smallest contract-first, fail-closed memory slice without
touching the Email Action Plan workflow.

### Slice M1.0 — contract placement decision

1. Inspect `src/cowork_agent/domain/target_contracts.py` and its round-trip
   tests; it already holds V1 target DTO conventions and explicitly deferred
   `TaskEpisode` / `MemoryContextRequest`.
2. Decide whether chat/memory DTOs remain in that module or move to a focused
   `domain/chat_contracts.py`. Prefer the focused module if the existing file
   would become harder to navigate; record the choice here under Decisions.
3. Freeze contract tests before implementing services.

### Slice M1.1 — typed contracts and namespace policy

Implement and test at minimum:

- `ChatMessageRequest(session_id, user_message, tool_choices, idempotency_key)`;
- `ChatMessageStreamEvent` typed event variants;
- `MemoryNamespace` with mandatory tenant/user/session, fixed
  `feature="ai_chat"`, memory type, and record/source identifiers;
- `MemoryContextRequest` and degraded-source response shape;
- minimal `TaskEpisode` fields needed by later milestones;
- pure fail-closed namespace validation and logical-key construction.

Do not add API routes, databases, LLM chat loops, or profile/episode storage in
this slice.

### Slice M1.2 — Gateway and bounded session buffer

1. Add framework-free feature ports/policies under a focused
   `features/ai_chat/` package.
2. Implement an in-memory Chat Session Buffer adapter with bounded turn count,
   TTL, explicit cleanup, and an injectable clock for deterministic tests.
3. Implement the Memory Gateway facade so all reads/writes validate namespace
   and policy first; unavailable optional memory returns typed degradation.
4. Prove cross-tenant, cross-user, cross-session, wrong-feature, expiry,
   compaction, and no-bypass behavior.

### V2-M1 exit checklist

- [ ] Contract round trips and invalid-schema tests pass.
- [ ] Missing/inconsistent namespace fails closed.
- [ ] Session turns are bounded and expire deterministically.
- [ ] Gateway is the only feature-level memory access boundary.
- [ ] No raw-email-shaped field exists in a durable memory DTO.
- [ ] Smallest relevant pytest scope, Ruff, and mypy pass.
- [ ] AC-01, AC-02, and AC-03 have evidence links.
- [ ] Dashboard, `tasks/plan.md`, and `tasks/todo.md` agree.

## 8. Source and test map for V2-M1

Read these before editing; do not load unrelated provider/UI files.

| File | Why it matters |
|---|---|
| `src/cowork_agent/domain/target_contracts.py` | Existing immutable DTO and serialization conventions |
| `tests/unit/domain/test_target_contracts.py` | Contract round-trip/error pattern |
| `src/cowork_agent/features/email_action_plan/ports.py` | Protocol style and dependency-boundary example only |
| `src/cowork_agent/features/email_action_plan/short_term.py` | Existing TTL/cleanup concept; do not reuse email-run semantics as chat semantics |
| `tests/unit/features/test_short_term.py` | Deterministic short-term testing precedent |
| `src/cowork_agent/integrations/rag/null_memory.py` | Typed degraded/no-results adapter precedent |
| `src/cowork_agent/app.py` | Composition root; do not modify until a slice needs wiring |

Expected new test homes:

```text
tests/unit/domain/test_chat_contracts.py
tests/unit/features/ai_chat/test_namespace.py
tests/unit/features/ai_chat/test_memory_gateway.py
tests/unit/features/ai_chat/test_session_buffer.py
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

### Open decisions (resolve only when their milestone needs them)

| Decision | Needed by | Current handling |
|---|---|---|
| Chat contract module placement | V2-M1 M1.0 | Inspect convention, record decision |
| PostgreSQL profile/episode schema migration shape | V2-M2/M3 | Do not migrate during M1 |
| Session max turns and TTL numeric defaults | V2-M1 | Define config-backed safe defaults with tests; surface if PRD conflict |
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

## 13. End-of-session handoff template

Before stopping, replace this block's placeholders with current facts:

```text
ACTIVE MILESTONE / SLICE:
STATUS AND PERCENT:
COMMITS:
TESTS / LINT / TYPES:
AC EVIDENCE ADDED:
FILES CHANGED:
USER-OWNED DIRTY FILES PRESERVED:
OPEN BLOCKER + OWNER + NEXT PROOF:
EXACT NEXT ACTION:
```

The next agent should be able to act from those fields plus §§1, 7, and 8
without replaying prior conversation or rereading all architecture documents.
