# Session Bootstrap & PRD-v2 Delivery Dashboard

> Start here in a new coding session. This file is the compact operational
> view for planning, implementation, and project tracking. Read `AGENTS.md`
> first, then this file. Open a larger PRD/architecture document only when the
> routing table below says the current task needs it or a conflict appears.

| Field | Current value |
|---|---|
| Updated | 2026-08-10 (Asia/Bangkok) |
| Branch / implementation baseline | `dev` / `e5be750` (implementation; run `git rev-parse --short HEAD` for the current handoff commit) |
| Product frontier | PRD-v2 Multi-Turn AI Chat Memory + executable `@Email` tool |
| Active milestone | **V2-M2 — AI Chat Declarative Profile (DONE)**; V2-M3 is next |
| PRD-v2 progress | **2/6 milestones; 3/20 acceptance criteria complete** |
| V1 foundation | PRD-v1 §15 passed; Email RAG pipeline implemented and memory-free |
| Formal readiness caveat | V1-H task 5.5 and final hardening checkpoint remain open |
| Repository docs | Dashboard and trackers reconciled through V2-M2 |
| Priority override | `@Email` tool slices are deprioritized to LAST (user, 2026-08-10) — see §5 |

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

The earlier user-owned worktree changes (caveman deletions, `scripts/run_gui.py`,
`skills-lock.json`, `uv.lock`) are no longer outstanding. The only untracked
file at V2-M2 handoff is `.claude/.headroom_wrap_marker.json`; leave it alone.

Local runtime context (informational; recheck rather than assume it survived):

- Backend was healthy at `http://127.0.0.1:8000` via
  `mail-todo-api`.
- GUI was healthy at `http://127.0.0.1:8501` via
  `python scripts/run_gui.py`.
- Gmail OAuth was completed locally on 2026-08-10. Never copy credentials or
  token material into docs, tests, logs, or commits.
- Docker Desktop is running as of 2026-08-10; `docker start cowork-pg` brings
  the database up (verified). Recreate it only if that fails:
  `docker run -d --name cowork-pg -e POSTGRES_USER=cowork
  -e POSTGRES_PASSWORD=cowork_dev_only -e POSTGRES_DB=cowork_mail_todo
  -p 5432:5432 postgres:16-alpine`.
- An unrelated project's container (`advisor-data-platform-db-1`,
  pgvector:pg16, host port 55432) is also up. It is **not** this project's
  database; do not point `PG_TEST_URL` at it or migrate into it.

Two environment hazards observed on 2026-08-10, both outside this milestone:

- **A concurrent `wgm` process operates on this repo and ran `git stash`
  mid-session**, silently reverting every tracked V2-M2 file (untracked new
  files survived). Recovery was `git checkout stash@{0} -- <files>`; the stash
  was left in place. It also rewrites `.wgm/IMPLEMENTATION_PLAN.md` — which now
  contains an unapproved *Qdrant Vector Store Migration* plan (deprecate
  `HybridSemanticMemory`, add a `qdrant` compose service). That plan is **not**
  authorized: it contradicts guardrail 4 above and pre-empts the open question
  in §11. Check `git stash list` and `git status` before trusting the worktree.
- `SSL_CERT_FILE` in the local shell points at a nonexistent
  `E:\CODE\Anaconda/ssl/cacert.pem`, so every httpx client construction raises
  `FileNotFoundError`. This makes all 24 tests in
  `tests/integration/api/test_e2e_frontend_api.py` error at fixture setup.
  Pre-existing, unrelated to V2-M2, and an environment fix (unset or repoint
  the variable), not a code fix.

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
| V2-M2 Declarative chat profile | **DONE** | 100 | Explicit-only profile CRUD; per-turn compact load; fallback; deletion/retention | V2-M1 | Policy/port/gateway/migration/repo landed; 496 passed / 1 skipped / 4 xfailed, Ruff, mypy clean; PostgreSQL gate 15 passed against `cowork-pg` |
| V2-M3 Chat + `@Email` episodes | **NEXT** | 0 | Idempotent summaries/tool episodes; mandatory provenance; raw-body rejection; ineligible default | V2-M1, V2-M2 contracts | — |
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

Operational rule: V2-M2 contract/policy and profile persistence are landed.
Do not scaffold episodes, controller/SSE, or production vector infrastructure
beyond the active milestone.

**Priority override (user, 2026-08-10): the executable `@Email` chat tool is
the LAST thing to build.** Within each milestone, do the chat-native half
first and defer the tool half:

- V2-M3: build bounded chat summary episodes; defer `@Email` Action Plan
  episodes and their provenance wiring.
- V2-M4: build session/message APIs, the Chat Controller loop, and typed SSE;
  defer the `@Email` skill wrapper, Action Plan cards, and inline
  approve/complete/reject controls.
- The deferred slices still gate AC-05..AC-11, AC-15, and AC-16, so PRD-v2 §16
  cannot pass until they are picked back up. This reorders work; it does not
  drop it.

## 6. PRD-v2 acceptance dashboard

| ID | Acceptance statement | Status | Evidence |
|---|---|---:|---|
| AC-01 | Chat Controller accesses all four memories only through Gateway | TODO | Gateway-only feature boundary: `2a29e29`; actual Chat Controller proof remains V2-M4 |
| AC-02 | Every operation carries tenant/user/session/`feature: ai_chat`/type | DONE | `310d2fd`, `2a29e29`; domain namespace + foreign-scope gateway tests |
| AC-03 | Bounded working buffer preserves turns and expires by policy | DONE | `7e42784`, `2a29e29`; `tests/unit/features/ai_chat/test_session_buffer.py` |
| AC-04 | Explicit persona/preferences persist and load in later sessions | DONE | `tests/unit/features/ai_chat/test_profile_policy.py`, `test_memory_gateway.py`; profile key is session-independent; `tests/integration/persistence/test_chat_profile_repository.py` green against `cowork-pg` on 2026-08-10 (15 passed in the module set) |
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
| AC-17 | Deletion prevents later retrieval | TODO | Profile half proven at gateway level (`test_profile_deletion_prevents_later_retrieval`) **and at storage level** (`test_deletion_prevents_later_retrieval`, `test_expired_profile_is_never_returned_and_purges` on real PostgreSQL); episode deletion pending V2-M3 |
| AC-18 | Production telemetry is metadata-only | TODO | — |
| AC-19 | Memory outage degrades chat without corrupting `@Email` | TODO | Gateway half proven (`test_profile_outage_leaves_working_memory_and_tool_availability_intact`); end-to-end proof needs the V2-M4 controller |
| AC-20 | No scheduler, recurring scan, or autonomous email action added | TODO | — |

Evidence format: link a test path, commit SHA, migration, browser artifact, or
gate record. `DONE` without evidence is invalid. Update the top-level `3/20`
counter whenever this table changes.

## 7. Immediate implementation plan — V2-M2

Goal: persist and load a compact, explicit-only declarative chat profile
without coupling memory into the Email Action Plan workflow.

All three slices (M2.0 policy/contract, M2.1 PostgreSQL persistence, M2.2
gateway integration) are **implemented and uncommitted on `dev`**. What
exists now, so the next agent does not re-derive it:

| Slice | Delivered |
|---|---|
| M2.0 | `DeclarativeProfile` narrowed to the FR-03 first slice plus `source_type` + `expires_at` (contract itself rejects non-explicit `source_type`); `features/ai_chat/profile_policy.py` with pure `authorize_profile_write` |
| M2.1 | `migrations/002_chat_profiles{,.down}.sql`; `PostgresChatProfileRepository` — idempotent upsert preserving `created_at`, SQL-level expiry filter on read, `delete_profile`, `purge_expired` |
| M2.2 | `DeclarativeMemoryPort` gains `write_profile`/`delete_profile`; `MemoryGateway.write_profile`/`delete_profile` check scope then policy; no episodic/semantic write path exists |

Rejection rules the policy enforces: non-`explicit_user_config` provenance,
`source_tool='@Email'` (i.e. inferred from tool/email output), foreign
tenant/user, non-long-term namespace, and any preference over
`MAX_PREFERENCE_LENGTH` (200).

### V2-M2 exit checklist

- [x] Explicit-only profile write policy rejects passive inference.
- [x] Profile CRUD is tenant/user isolated, idempotent, and expiry-aware.
- [x] Gateway returns compact profile data or typed long-term degradation.
- [x] Deletion prevents later profile retrieval.
- [x] No email body or ordinary chat transcript enters the profile schema.
- [x] Focused tests, PostgreSQL integration proof, Ruff, and mypy pass.
- [ ] AC-04 plus applicable AC-17/AC-19 evidence is recorded.
- [x] Dashboard, `tasks/plan.md`, and `tasks/todo.md` agree.

**PostgreSQL gate: passed 2026-08-10.** `docker start cowork-pg` then
`python -m pytest tests/integration/persistence -q` → **15 passed** (10
pre-existing V1-H repository tests plus the 5 new profile scenarios:
idempotent upsert, tenant/user isolation, expiry invisibility + purge,
deletion, and the no-body/no-transcript schema check). One real fix was
needed: `test_migrations_apply_once_and_are_idempotent` pins the applied
migration list, so it now asserts
`("001_mail_todo.sql", "002_chat_profiles.sql")`. Nothing in the profile
repository or migration itself needed changing.

**Resume here.** Record AC-04 as DONE with the run above, add storage
evidence to AC-17, move V2-M2 to DONE 100%, bump the top counters to `2/6`
and `3/20`, then replace this section with the V2-M3 slices — chat-summary
half first, `@Email` tool slices last (§5 priority override).

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

There is no `.venv` on this machine; plain `python` (3.13) resolves correctly.

```bash
python -m pytest <smallest-relevant-test-path> -q
python -m ruff check .
python -m mypy src
```

Because V2-M1 adds shared contracts, expand to the full suite before its exit
gate if the focused tests pass:

```bash
python -m pytest -q
```

For later frontend slices:

```bash
mail-todo-api
python scripts/run_gui.py
playwright-cli open http://127.0.0.1:8501
```

## 10. Known frontend/runtime evidence

Playwright review on 2026-08-10 established:

- Gmail OAuth recognized; bounded five-message read-only run completed `5/5`
  (`succeeded`) with the correct empty-task state.
- Knowledge retrieval returned five provenance-linked chunks; no console
  errors; DOM/storage/result schema showed no raw-email fields.
- GUI is still email-first with no chat input — expected until V2-M4.
- Debt to track, not fix here: sections are `h3` with no `h1`/`h2`; Knowledge
  UI returns ranked chunks rather than the grounded answer the frontend spec
  promises; one retrieval took 3411 ms vs the 3000 ms target (one sample, not
  a p95); at 320 px the Knowledge table clips long source paths.

Do not "fix while here." Track these for the appropriate DEMO or performance
slice.

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
| Profile shape is the narrowed FR-03 slice | `language`, `timezone`, `assistant_persona`, `response_tone` plus `source_type` and `expires_at`; the richer FR-03 list waits for a real settings UI |
| Profile rows are session-independent | Key is `tenant/user/ai_chat/long_term`, not `MemoryNamespace.logical_key()` (which pins `session_id`), so a preference survives the session that set it |
| Explicit-only is a storage constraint too | `chat_profiles.source_type` carries a `CHECK (= 'explicit_user_config')` alongside the feature-layer policy |
| Profile write failure surfaces, read failure degrades | FR-18 degradation covers the per-turn read; a user-requested write raises `MemorySourceUnavailableError` instead of silently dropping |

### Open decisions (resolve only when their milestone needs them)

| Decision | Needed by | Current handling |
|---|---|---|
| PostgreSQL episode schema migration shape | V2-M3 | Profile shape resolved (`002_chat_profiles.sql`); episodes get their own migration |
| What Qdrant is actually for | before any Qdrant work | **Unresolved — the user asked on 2026-08-10 whether integrating Qdrant per `docs/evaluations/email-rag/EMAIL-RAG-STATUS.md` would affect the PostgreSQL work; the clarifying interview was cut short.** Findings so far: structurally the two are disjoint. Qdrant would be a fourth `SemanticMemoryPort` implementation (`features/email_action_plan/ports.py:213`, joining `InRepoSemanticMemory` / `HybridSemanticMemory` / `NullSemanticMemory`) swapped in at the composition root; PostgreSQL implements an unrelated repository set (runs, tasks, outbox, `chat_profiles`). No shared table, transaction, or migration. **The fork to settle first:** if Qdrant only holds the company corpus, the answer is "zero effect on PostgreSQL". If it is also meant to back PRD-v2 semantic/episodic chat memory, then PostgreSQL becomes the record of truth and Qdrant its index, and FR-16's "propagate deletion to search indexes" turns into real dual-write/consistency work layered on the deletion path built in V2-M2. Ask before scaffolding either way — invariant 4 keeps Qdrant milestone-gated regardless |
| Retention period values per product/tenant | V2-M6 | `expires_at` and `purge_expired` exist; no policy value or scheduler is wired (FR-16 background purge stays M6) |
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
| 2026-08-10 | V2-M2 slices M2.0–M2.2 | Explicit-only policy, profile port/gateway CRUD, `002_chat_profiles` migration, `PostgresChatProfileRepository` |
| 2026-08-10 | `tests/integration/persistence` → 15 passed against `cowork-pg` | V2-M2 exit gate; AC-04 DONE, AC-17 storage half proven |
| 2026-08-10 | 496 passed / 1 skipped / 4 xfailed; Ruff clean; `mypy src` clean | Full code gate with PostgreSQL live. 24 separate errors in `test_e2e_frontend_api.py` are a broken local `SSL_CERT_FILE`, not a regression (§3) |

## 13. End-of-session handoff template

Before stopping, replace this block's placeholders with current facts:

```text
ACTIVE MILESTONE / SLICE: V2-M2 complete and evidenced; V2-M3 is NEXT, nothing started
STATUS AND PERCENT: V2-M1 DONE 100%; V2-M2 DONE 100%; PRD-v2 2/6 milestones, 3/20 AC
COMMITS: 7e42784, 310d2fd, 1d92fa1, 2a29e29, e5be750, plus the V2-M2 commit on `dev`
TESTS / LINT / TYPES: 496 passed / 1 skipped / 4 xfailed; PostgreSQL gate 15 passed;
  Ruff pass; mypy src pass. The 24 `test_e2e_frontend_api.py` errors are the SSL_CERT_FILE
  environment defect described in §3 — not caused by, and not fixable inside, this repo
AC EVIDENCE ADDED: AC-04 → DONE; AC-17 storage half proven; AC-19 still gateway-half only
FILES CHANGED: _chat_contracts_memory.py, chat_contracts.py, ai_chat/{ports,profile_policy,memory_gateway}.py,
  migrations/002_chat_profiles{,.down}.sql, repositories/postgres.py, 3 test files, dashboard/trackers
USER-OWNED DIRTY FILES PRESERVED: `.wgm/IMPLEMENTATION_PLAN.md` (concurrent `wgm` process — see §3;
  deliberately left uncommitted) and untracked `.claude/.headroom_wrap_marker.json`
OPEN BLOCKER + OWNER + NEXT PROOF: none blocking V2-M3
OPEN QUESTION (user): what Qdrant is meant to store — company corpus only, or PRD-v2
  semantic/episodic memory too. Analysis is in §11; do not scaffold Qdrant until answered.
  Note the `wgm` process has already written an unauthorized Qdrant migration plan (§3)
EXACT NEXT ACTION: start the chat-summary half of V2-M3 — bounded chat summaries with mandatory
  chat/turn provenance, `retrieval_eligible=false` by default, and raw-body rejection enforced in
  code at both write and read boundaries. Defer every `@Email` episode slice to last (§5)
```

The next agent should be able to act from those fields plus §§1, 7, and 8
without replaying prior conversation or rereading all architecture documents.
